"""
ViralModelAgent: 爆文模型矩阵生成器(阶段 4.3pre.2 新建)。

**职责**:
- 替代原 StrategyAgent
- 消费 ImageAgent + VideoAgent 的 `multimodal_output.annotations`(共享 dict)
- 用"混合聚类"算法生成"爆文模型 × 6 要素"二维矩阵
- 写到 TaskContext 的 `viral_model_output` 分区

**混合聚类算法(4.3pre.2 + playbook 语义修订)**:
1. 每条笔记构造 **7 维 playbook 签名**: `content_direction` + 6 要素标注字段(与矩阵要素一致),
   与 Image/Video 分析结论绑定,而非仅统计原始 direction 字符串。
2. 相同签名的笔记归入同一微簇;在簇数 > `VIRAL_MODEL_MAX_MODELS` 时,按 **质心(逐维众数)**
   与 **可合并规则** 迭代合并最相似的一对,直至 ≤ 上限(最多 6,**实际簇数可少于 6**)。
3. **可合并规则**: 6 要素质心至少 5 维一致;若主 `content_direction` 不同,则仅当任一侧簇
   很小(≤2 条)时才合并,避免把大不相同的叙事方向硬捏在一起。
4. 合并后再 **消解极小簇**(`< VIRAL_MODEL_MIN_CLUSTER_SIZE`,默认 2):并入最相似邻簇,
   减少「单条成模」噪声(全库仅 1 条时仍保留 1 簇)。
5. 簇的展示用 `direction` 字段:主 direction 占多数则用其命名;否则「多元形态·要素摘要」。
6. LLM 仅负责**起名+描述**,输入已含该簇真实样本算出的 top 要素占比。
7. `unused_directions` 仍按原始 content_direction 统计,笔记已全部进矩阵的方向跳过。

**降级策略**:
- 没 annotations / 全空 → 写空 ViralModelMatrix(下游 Canvas 显示空状态)
- 有标注但分组异常为空 → 写空矩阵 empty_reason=all_below_threshold
- LLM 起名失败 → 用 direction 直接当 name,description 留空
- 没 examples 时 ratio/count 仍可用,前端展示空示例

权威文档: docs/canvas_restructure_spec.md 4.2/5.2 章, plan: 4.3pre.2 step 4
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from ...domain.task_context import TaskContextWriter
from ...domain.viral_model import (
    ELEMENT_ORDER,
    ElementCategory,
    ElementCode,
    ElementExample,
    UnusedDirection,
    ViralModel,
    ViralModelMatrix,
)
from ...llm.model_gateway import ModelInvocationError
from .base import AgentContext, AgentResult, BaseAgent
from .prompts import prompt_registry


# coverage 阈值: 严格模式下 coverage 低于此比例的方向不进入主矩阵(可归 unused)
_COVERAGE_THRESHOLD = float(os.getenv("VIRAL_MODEL_COVERAGE_THRESHOLD", "0.05"))
# 绝对数量下限: 严格模式下每组至少 N 条;与 total 取 min,避免「全库仅 1～2 条标注」时永远无法成模
_COVERAGE_MIN_COUNT = int(os.getenv("VIRAL_MODEL_COVERAGE_MIN_COUNT", "2"))
# 主矩阵最多几个「内容方向」模型(严格入选过多、或兜底 Top-K 共用同一上限)
_m_raw = os.getenv("VIRAL_MODEL_MAX_MODELS")
_fb_raw = os.getenv("VIRAL_MODEL_FALLBACK_MAX_MODELS")
if _m_raw is not None:
    _MAX_MODELS = max(1, int(_m_raw))
elif _fb_raw is not None:
    _MAX_MODELS = max(1, int(_fb_raw))
else:
    _MAX_MODELS = 6
# 聚类完成后仍小于该条数的簇会并入最相似邻簇(减少「一条一模型」;总样本 1 条时不生效)
_MIN_CLUSTER_SIZE = max(1, int(os.getenv("VIRAL_MODEL_MIN_CLUSTER_SIZE", "2")))
# unused_directions 列表最多展示条数(含因模型数上限被裁切的方向)
_UNUSED_DISPLAY_MAX = max(5, int(os.getenv("VIRAL_MODEL_UNUSED_DISPLAY_MAX", "12")))
# 每个 ElementCategory 最多展示几个 examples
_MAX_EXAMPLES_PER_CATEGORY = int(os.getenv("VIRAL_MODEL_MAX_EXAMPLES", "3"))
# unused_directions 的额外门槛: 至少要被 N 条爆款选中才有展示价值
_UNUSED_MIN_COUNT = int(os.getenv("VIRAL_MODEL_UNUSED_MIN", "2"))
# LLM 起名超时
_NAMING_TIMEOUT = int(os.getenv("VIRAL_MODEL_NAMING_TIMEOUT", "30"))

# 6 要素 annotation 字段名 → ElementCode 的映射
_FIELD_TO_CODE: Dict[str, ElementCode] = {
    "cover_type": ElementCode.A_COVER,
    "cover_text_type": ElementCode.B_COVER_TEXT,
    "title_type": ElementCode.C_TITLE,
    "opening_type": ElementCode.D_OPENING,
    "product_intro_type": ElementCode.E_PRODUCT_INTRO,
    "product_placement_type": ElementCode.F_PRODUCT_PLACEMENT,
}

# 质心 / 签名: content_direction + 与 _FIELD_TO_CODE 相同顺序的 6 字段
_CENTROID_FIELD_ORDER: Tuple[str, ...] = ("content_direction",) + tuple(_FIELD_TO_CODE.keys())


def _empty_element_token() -> str:
    return "∅"


def _note_signature_tuple(ann: Dict[str, Any]) -> Tuple[str, ...]:
    cd = ((ann.get("content_direction") or "").strip() or "未知")
    parts: List[str] = [cd]
    for field in _FIELD_TO_CODE:
        v = (ann.get(field) or "").strip()
        parts.append(v if v else _empty_element_token())
    return tuple(parts)


def _centroid_from_ids(
    note_ids: List[str], annotations: Dict[str, Dict[str, Any]]
) -> Tuple[str, ...]:
    vecs = [
        _note_signature_tuple(annotations[n])
        for n in note_ids
        if n in annotations
    ]
    if not vecs:
        return tuple(_empty_element_token() for _ in _CENTROID_FIELD_ORDER)
    dim = len(vecs[0])
    out: List[str] = []
    for i in range(dim):
        ctr = Counter(v[i] for v in vecs)
        out.append(ctr.most_common(1)[0][0])
    return tuple(out)


def _centroid_element_match_score(a: Tuple[str, ...], b: Tuple[str, ...]) -> int:
    """仅比较 6 要素维(不含 content_direction),用于相似度。"""
    if len(a) < 2 or len(b) < 2:
        return 0
    ea, eb = a[1:], b[1:]
    return sum(1 for x, y in zip(ea, eb) if x == y)


def _dominant_content_direction(
    note_ids: List[str], annotations: Dict[str, Dict[str, Any]]
) -> str:
    cds: List[str] = []
    for n in note_ids:
        ann = annotations.get(n) or {}
        cds.append(((ann.get("content_direction") or "").strip() or "未知"))
    if not cds:
        return "未知"
    return Counter(cds).most_common(1)[0][0]


def _can_merge_clusters(
    na: int,
    nb: int,
    ca: Tuple[str, ...],
    cb: Tuple[str, ...],
    annotations: Dict[str, Dict[str, Any]],
    ids_a: List[str],
    ids_b: List[str],
) -> bool:
    if na + nb <= 3:
        return True
    if _centroid_element_match_score(ca, cb) < 5:
        return False
    cd_a = _dominant_content_direction(ids_a, annotations)
    cd_b = _dominant_content_direction(ids_b, annotations)
    if cd_a == cd_b:
        return True
    return min(na, nb) <= 2


def _can_absorb_for_dissolve(
    nv: int,
    nt: int,
    cv: Tuple[str, ...],
    ct: Tuple[str, ...],
    ids_v: List[str],
    ids_t: List[str],
    annotations: Dict[str, Dict[str, Any]],
    min_sz: int,
) -> bool:
    """消解极小簇:不向已成型且叙事方向不同的大簇硬并(除非 playbook 几乎相同且一侧已足够大)。"""
    if _centroid_element_match_score(cv, ct) < 5:
        return False
    if nv + nt <= 3:
        return True
    cd_v = _dominant_content_direction(ids_v, annotations)
    cd_t = _dominant_content_direction(ids_t, annotations)
    if cd_v == cd_t:
        return True
    if nv < min_sz <= nt or nt < min_sz <= nv:
        return True
    return False


def _cluster_row_label(
    note_ids: List[str],
    annotations: Dict[str, Dict[str, Any]],
    centroid: Tuple[str, ...],
) -> str:
    cds = []
    for n in note_ids:
        ann = annotations.get(n) or {}
        cds.append(((ann.get("content_direction") or "").strip() or "未知"))
    if len(note_ids) == 1:
        dom = cds[0]
        return dom[:24] if len(dom) <= 24 else dom[:21] + "…"
    ctr = Counter(cds)
    dom, cnt = ctr.most_common(1)[0]
    half = max(2, (len(note_ids) + 1) // 2)
    if cnt >= half and dom != "未知":
        return dom[:24] if len(dom) <= 24 else dom[:21] + "…"
    tail = "·".join(centroid[1:4])
    if len(tail) > 28:
        tail = tail[:25] + "…"
    return f"多元形态·{tail}" if tail else "多元形态"


def _build_playbook_clusters(
    annotations: Dict[str, Dict[str, Any]],
    max_models: int,
) -> List[Tuple[str, List[str]]]:
    """由 7 维 playbook 签名聚类 + 质心合并,得到 ≤ max_models 个簇,(label, note_ids)。"""
    total = len(annotations)
    if total <= 0:
        return []

    micro: Dict[Tuple[str, ...], List[str]] = defaultdict(list)
    for nid, ann in annotations.items():
        micro[_note_signature_tuple(ann)].append(nid)

    clusters: List[Dict[str, Any]] = []
    for ids in micro.values():
        u = sorted(set(ids))
        clusters.append(
            {
                "ids": u,
                "centroid": _centroid_from_ids(u, annotations),
            }
        )

    if max_models <= 1:
        all_ids = sorted({n for c in clusters for n in c["ids"]})
        lab = _cluster_row_label(all_ids, annotations, _centroid_from_ids(all_ids, annotations))
        return [(lab, all_ids)]

    def _merge_pair(i: int, j: int) -> None:
        if len(clusters[j]["ids"]) > len(clusters[i]["ids"]):
            i, j = j, i
        clusters[i]["ids"] = sorted(set(clusters[i]["ids"]) | set(clusters[j]["ids"]))
        clusters[i]["centroid"] = _centroid_from_ids(clusters[i]["ids"], annotations)
        clusters.pop(j)

    # 1) 收到 budget: 只合并「允许」的对;必要时强制合并最小两簇以免死循环
    guard = 0
    while len(clusters) > max_models and guard < total * 3:
        guard += 1
        best: Optional[Tuple[int, int]] = None
        best_score = -1
        best_size = 10**9
        n_c = len(clusters)
        for i in range(n_c):
            for j in range(i + 1, n_c):
                ci, cj = clusters[i], clusters[j]
                na, nb = len(ci["ids"]), len(cj["ids"])
                if not _can_merge_clusters(
                    na,
                    nb,
                    ci["centroid"],
                    cj["centroid"],
                    annotations,
                    ci["ids"],
                    cj["ids"],
                ):
                    continue
                sc = _centroid_element_match_score(ci["centroid"], cj["centroid"])
                ss = na + nb
                if sc > best_score or (sc == best_score and ss < best_size):
                    best_score = sc
                    best_size = ss
                    best = (i, j)
        if best is None:
            # 强制:合并规模最小的两簇
            idxs = sorted(range(len(clusters)), key=lambda k: len(clusters[k]["ids"]))
            if len(idxs) < 2:
                break
            i, j = idxs[0], idxs[1]
            if i > j:
                i, j = j, i
            _merge_pair(i, j)
            logger.warning(
                "[ViralModelAgent] 聚类数仍>上限且无合法合并对,强制合并最小两簇(剩余 {} 簇)",
                len(clusters),
            )
            continue
        i, j = best
        if i > j:
            i, j = j, i
        _merge_pair(i, j)

    # 2) 消解极小簇:并回最相似且允许吸收的一侧,减少「一条一模型」
    min_sz = _MIN_CLUSTER_SIZE
    if total > 1 and min_sz > 1:
        g2 = 0
        while len(clusters) > 1 and g2 < total * 4:
            g2 += 1
            tiny_idx = min(range(len(clusters)), key=lambda k: len(clusters[k]["ids"]))
            if len(clusters[tiny_idx]["ids"]) >= min_sz:
                break
            victim = tiny_idx
            v = clusters[victim]
            nv = len(v["ids"])
            others = [k for k in range(len(clusters)) if k != victim]
            scored: List[Tuple[int, int, int]] = []
            for k in others:
                t = clusters[k]
                nt = len(t["ids"])
                if not _can_absorb_for_dissolve(
                    nv,
                    nt,
                    v["centroid"],
                    t["centroid"],
                    v["ids"],
                    t["ids"],
                    annotations,
                    min_sz,
                ):
                    continue
                sc = _centroid_element_match_score(v["centroid"], t["centroid"])
                scored.append((sc, -nt, k))
            if not scored:
                break
            scored.sort(reverse=True)
            target_k = scored[0][2]
            clusters[target_k]["ids"] = sorted(
                set(clusters[target_k]["ids"]) | set(v["ids"])
            )
            clusters[target_k]["centroid"] = _centroid_from_ids(
                clusters[target_k]["ids"], annotations
            )
            clusters.pop(victim)

    rows: List[Tuple[str, List[str]]] = []
    for c in clusters:
        ids: List[str] = c["ids"]
        cen: Tuple[str, ...] = c["centroid"]
        label = _cluster_row_label(ids, annotations, cen)
        rows.append((label, ids))

    rows.sort(key=lambda r: (-len(r[1]), str(r[0])))
    return rows


class ViralModelAgent(BaseAgent):
    agent_id = "ViralModelAgent"
    provides = ["mod-viral-model-matrix"]
    # 4.3pre.3: Image/Video Agent 已不对外暴露画布模块(provides=[]),
    # 本 Agent 通过 TaskContext 的 `multimodal_output.annotations` 读取它们的产出,
    # 不再通过 module_graph module_id 建立依赖。module_graph 层面的依赖
    # 在 canvas_render_agent._MODULE_REGISTRATIONS 里由 mod-viral-model-matrix
    # 对 3 个画布样本模块 depends_on 表达。
    depends_on: List[str] = []
    write_partition = "viral_model_output"

    async def run(self, context: AgentContext) -> AgentResult:
        task_id = context.task_id
        crawler = context.task_context.get("crawler_output") or {}
        multimodal = context.task_context.get("multimodal_output") or {}

        all_notes: List[Dict[str, Any]] = list(crawler.get("all_notes") or [])
        annotations: Dict[str, Dict[str, Any]] = (
            multimodal.get("annotations") or {}
        )

        # 没标注 → 空矩阵
        if not annotations:
            await self.emit_progress(
                task_id, "爆文矩阵:无标注样本,写空矩阵", progress=60
            )
            self._write_empty_matrix(context, reason="no_annotations")
            return AgentResult(
                ok=True, produced_modules=self.provides, output={"models": 0}
            )

        # ---- Step 1: 按 content_direction 粗聚类 ----
        groups: Dict[str, List[str]] = defaultdict(list)
        for note_id, ann in annotations.items():
            direction = (ann.get("content_direction") or "未知").strip() or "未知"
            groups[direction].append(note_id)

        total = len(annotations)

        # ---- Step 2: playbook 签名聚类 → 矩阵行(条数 ≤ 上限,随数据可为 1～上限) ----
        matrix_rows = _build_playbook_clusters(annotations, _MAX_MODELS)

        if not matrix_rows:
            await self.emit_progress(
                task_id,
                "爆文矩阵:无有效内容方向分组,写空矩阵",
                progress=60,
            )
            _ud = self._build_unused_directions(
                groups, all_notes, total, allowed=None
            )
            self._write_empty_matrix(
                context,
                reason="all_below_threshold",
                unused_dirs=_ud[:_UNUSED_DISPLAY_MAX],
            )
            return AgentResult(
                ok=True, produced_modules=self.provides, output={"models": 0}
            )

        notes_by_id = {n.get("note_id"): n for n in all_notes if n.get("note_id")}

        msg = (
            f"爆文矩阵:按 6 要素+内容方向 playbook 聚类 → {len(matrix_rows)} 个模型"
            f"(上限 {_MAX_MODELS},原始 direction 分组 {len(groups)} 个)"
        )
        await self.emit_progress(task_id, msg, progress=55)

        # ---- Step 3: 每组算 6 要素占比 + 选 examples ----
        models: List[ViralModel] = []

        for idx, (direction, note_ids) in enumerate(matrix_rows, start=1):
            model_id = f"M{idx}"
            elements = self._compute_element_stats(
                model_id, note_ids, annotations, notes_by_id
            )
            avg_interaction = self._avg_interaction(note_ids, notes_by_id)
            models.append(
                ViralModel(
                    model_id=model_id,
                    name=direction,  # 默认名 = direction;LLM 后会覆盖
                    description="",
                    coverage=len(note_ids) / total,
                    avg_interaction=avg_interaction,
                    sample_note_ids=note_ids,
                    elements=elements,
                    paragraph_id=model_id,  # 模型级反馈锚点 = model_id(M1/M2/...)
                )
            )

        # ---- Step 4: LLM 起名 + 描述 ----
        try:
            await self._llm_name_models(task_id, models)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[ViralModelAgent] LLM 起名失败({exc}),使用 direction 默认名")
            await self.emit_log(
                task_id, "warn", f"模型起名 LLM 调用失败:{type(exc).__name__},使用兜底名"
            )

        # ---- Step 5: unused_directions ----
        matrix_note_ids: set[str] = set()
        for _, ids in matrix_rows:
            matrix_note_ids.update(ids)
        unused_dirs = self._build_unused_directions(
            groups,
            all_notes,
            total,
            allowed=None,
            matrix_note_ids=matrix_note_ids,
        )
        unused_dirs.sort(key=lambda u: u.avg_interaction, reverse=True)
        unused_dirs = unused_dirs[:_UNUSED_DISPLAY_MAX]

        # ---- 组装 + 写回 ----
        try:
            from ...services.viral_taxonomy_loader import get_viral_taxonomy_loader

            tax_version = await get_viral_taxonomy_loader().current_version()
        except Exception:
            tax_version = "0"

        matrix = ViralModelMatrix(
            models=models,
            unused_directions=unused_dirs,
            total_sample_count=total,
            taxonomy_version=str(tax_version),
        )

        TaskContextWriter(context.task_context).write(
            "viral_model_output",
            matrix.to_dict(),
            agent_id=self.agent_id,
            note=f"matrix_{len(models)}models",
        )

        await self.emit_progress(
            task_id,
            f"爆文矩阵:生成完成 · {len(models)} 模型 · {len(unused_dirs)} 条未入矩阵方向提示",
            progress=65,
        )

        return AgentResult(
            ok=True,
            produced_modules=self.provides,
            output={
                "models_count": len(models),
                "unused_count": len(unused_dirs),
                "total_sample_count": total,
            },
        )

    # ------------------------------------------------------------------
    # 算法
    # ------------------------------------------------------------------

    def _compute_element_stats(
        self,
        model_id: str,
        note_ids: List[str],
        annotations: Dict[str, Dict[str, Any]],
        notes_by_id: Dict[str, Dict[str, Any]],
    ) -> Dict[ElementCode, List[ElementCategory]]:
        """对该 model 下所有 note 的 6 要素做计数 → ratio + examples。"""
        result: Dict[ElementCode, List[ElementCategory]] = {}
        total = len(note_ids) or 1

        for field, code in _FIELD_TO_CODE.items():
            counter: Counter = Counter()
            type_to_notes: Dict[str, List[str]] = defaultdict(list)
            for nid in note_ids:
                ann = annotations.get(nid) or {}
                val = (ann.get(field) or "").strip()
                if not val:
                    continue
                counter[val] += 1
                type_to_notes[val].append(nid)

            categories: List[ElementCategory] = []
            # 4.3pre.3: cat_idx 采用 1-based + C 前缀,与画布 paragraph_id 统一规则对齐
            # 形如 "M1-A_cover-C1" / "M1-A_cover-C2" / ...
            for cat_idx, (type_name, count) in enumerate(
                counter.most_common(), start=1
            ):
                examples = self._pick_examples(
                    type_to_notes[type_name], notes_by_id
                )
                categories.append(
                    ElementCategory(
                        type=type_name,
                        ratio=count / total,
                        count=count,
                        examples=examples,
                        paragraph_id=f"{model_id}-{code.value}-C{cat_idx}",
                    )
                )
            result[code] = categories
        return result

    def _pick_examples(
        self,
        note_ids: List[str],
        notes_by_id: Dict[str, Dict[str, Any]],
    ) -> List[ElementExample]:
        """按互动量降序选前 N 条作为 examples。"""
        notes = [notes_by_id[nid] for nid in note_ids if nid in notes_by_id]
        notes.sort(
            key=lambda n: int(n.get("interaction_score") or 0)
            + int(n.get("likes") or 0) * 1.5,
            reverse=True,
        )
        examples: List[ElementExample] = []
        for n in notes[:_MAX_EXAMPLES_PER_CATEGORY]:
            examples.append(
                ElementExample(
                    note_id=n.get("note_id", ""),
                    title=(n.get("title") or "")[:60],
                    cover_url=n.get("cover_url") or "",
                    likes=int(n.get("likes") or 0),
                )
            )
        return examples

    def _avg_interaction(
        self,
        note_ids: List[str],
        notes_by_id: Dict[str, Dict[str, Any]],
    ) -> int:
        if not note_ids:
            return 0
        total = 0
        cnt = 0
        for nid in note_ids:
            n = notes_by_id.get(nid)
            if not n:
                continue
            total += int(n.get("interaction_score") or 0) + int(
                n.get("likes") or 0
            )
            cnt += 1
        return int(total / cnt) if cnt else 0

    def _build_unused_directions(
        self,
        groups: Dict[str, List[str]],
        all_notes: List[Dict[str, Any]],
        total: int,
        *,
        allowed: Optional[set] = None,
        matrix_note_ids: Optional[set] = None,
    ) -> List[UnusedDirection]:
        """coverage 低或被排除 + 平均互动量较高的方向。

        matrix_note_ids: 已进入主矩阵各簇的笔记集合;某方向笔记 ⊆ 该集合时跳过,
        避免「已并进长尾」仍被误报为未入矩阵。
        """
        notes_by_id = {n.get("note_id"): n for n in all_notes if n.get("note_id")}
        result: List[UnusedDirection] = []
        for direction, note_ids in groups.items():
            if allowed is not None and direction in allowed:
                continue
            if matrix_note_ids is not None:
                nid_set = {nid for nid in note_ids if nid}
                if nid_set and nid_set <= matrix_note_ids:
                    continue
            if len(note_ids) < _UNUSED_MIN_COUNT:
                continue
            avg = self._avg_interaction(note_ids, notes_by_id)
            ratio = len(note_ids) / total if total else 0
            result.append(
                UnusedDirection(
                    direction=direction,
                    ratio=round(ratio, 4),
                    avg_interaction=avg,
                    reason=f"占比仅 {ratio*100:.1f}% < {_COVERAGE_THRESHOLD*100:.0f}%, 不入矩阵",
                )
            )
        result.sort(key=lambda u: u.avg_interaction, reverse=True)
        return result

    # ------------------------------------------------------------------
    # LLM 起名(批量一次,JSON 数组)
    # ------------------------------------------------------------------

    async def _llm_name_models(
        self, task_id: str, models: List[ViralModel]
    ) -> None:
        """让 LLM 给每个模型起名 + 写 30 字描述。失败时模型名保持 direction。"""
        if not models:
            return
        system_prompt = prompt_registry.load("viral_model_naming.md")

        # 构造 user 内容: 每个模型的 direction + coverage + 高频要素简表
        user_lines = ["请给以下爆文模型起名 + 描述,严格按输入顺序输出 JSON 数组:\n"]
        for m in models:
            top_elements = []
            for code in ELEMENT_ORDER:
                cats = m.elements.get(code) or []
                if cats:
                    top = cats[0]
                    top_elements.append(f"{code.label}={top.type}({top.ratio*100:.0f}%)")
            user_lines.append(
                f"- model_id={m.model_id}, direction={m.name}, "
                f"coverage={m.coverage*100:.1f}%, top_elements=[{', '.join(top_elements)}]"
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "\n".join(user_lines)},
        ]

        try:
            response = await asyncio.wait_for(
                self._gateway.chat(
                    agent_id=self.agent_id,
                    messages=messages,
                    modality="text",
                    task_id=task_id,
                    overrides={
                        "temperature": 0.3,
                        "max_tokens": 600,
                    },
                ),
                timeout=_NAMING_TIMEOUT,
            )
        except (asyncio.TimeoutError, ModelInvocationError) as exc:
            await self.emit_log(
                task_id, "warn", f"模型起名超时/降级:{type(exc).__name__}"
            )
            return

        text = (response.get("content") or "").strip()
        parsed = self._extract_json_array(text)
        if not parsed:
            await self.emit_log(task_id, "warn", "模型起名 LLM 输出无法解析为 JSON 数组")
            return

        # 按 model_id 匹配回填(LLM 顺序可能错乱)
        by_id = {m.model_id: m for m in models}
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            mid = entry.get("model_id") or entry.get("id") or ""
            if mid not in by_id:
                continue
            name = self._sanitize_name(entry.get("name") or "")
            desc = self._sanitize_desc(entry.get("description") or "")
            if name:
                by_id[mid].name = name
            if desc:
                by_id[mid].description = desc

    @staticmethod
    def _extract_json_array(text: str) -> Optional[List[Any]]:
        """粗暴提取 JSON 数组(不复用 _json_parsing,因为它只支持 dict)。"""
        # 去掉 think 块
        text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()
        # ```json ... ``` 优先
        m = re.search(r"```(?:json|JSON)?\s*\n?([\s\S]*?)```", text)
        if m:
            text = m.group(1)
        # 取 [ ... ] 段
        match = re.search(r"\[[\s\S]*\]", text)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, list) else None
        except (json.JSONDecodeError, ValueError):
            return None

    @staticmethod
    def _sanitize_name(name: str) -> str:
        """爆文模型名: 去空白、限长 ≤12 字符。"""
        s = (name or "").strip()
        return s[:12]

    @staticmethod
    def _sanitize_desc(desc: str) -> str:
        """描述: 限长 ≤40 字符。"""
        s = (desc or "").strip()
        return s[:40]

    # ------------------------------------------------------------------
    # 兜底
    # ------------------------------------------------------------------

    def _write_empty_matrix(
        self,
        context: AgentContext,
        *,
        reason: str,
        unused_dirs: Optional[List[UnusedDirection]] = None,
    ) -> None:
        matrix = ViralModelMatrix(
            models=[],
            unused_directions=unused_dirs or [],
            total_sample_count=0,
            taxonomy_version="0",
        )
        payload = matrix.to_dict()
        payload["empty_reason"] = reason
        TaskContextWriter(context.task_context).write(
            "viral_model_output",
            payload,
            agent_id=self.agent_id,
            note=f"empty_{reason}",
        )
