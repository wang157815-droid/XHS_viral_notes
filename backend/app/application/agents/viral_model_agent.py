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
# 主矩阵模型数上限: env var 显式设置时优先, 否则由 _compute_dynamic_max 按数据量自动决定
_m_raw = os.getenv("VIRAL_MODEL_MAX_MODELS")
_fb_raw = os.getenv("VIRAL_MODEL_FALLBACK_MAX_MODELS")
if _m_raw is not None:
    _MAX_MODELS: Optional[int] = max(1, int(_m_raw))
elif _fb_raw is not None:
    _MAX_MODELS = max(1, int(_fb_raw))
else:
    _MAX_MODELS = None  # None = 数据驱动模式


def _compute_dynamic_max(total: int) -> int:
    """数据驱动的模型数量上限（软上限）。

    当 VIRAL_MODEL_MAX_MODELS / VIRAL_MODEL_FALLBACK_MAX_MODELS 显式设置时直接返回。
    否则按样本量分段：此上限为"软上限"——有合法相似对才合并；
    若无合法对则自然停止，实际模型数可略超此值。

    总样本  ≤ 15  →  6
    16-30   →  6
    31-60   →  8
    61-100  →  10
    101-150 →  12
    151+    →  15（硬顶）
    """
    if _MAX_MODELS is not None:
        return _MAX_MODELS
    if total <= 15:
        return 6
    if total <= 30:
        return 6
    if total <= 60:
        return 8
    if total <= 100:
        return 10
    if total <= 150:
        return 12
    return 15
# 聚类完成后仍小于该条数的簇会并入最相似邻簇(减少「一条一模型」;总样本 1 条时不生效)
_MIN_CLUSTER_SIZE = max(1, int(os.getenv("VIRAL_MODEL_MIN_CLUSTER_SIZE", "2")))
# unused_directions 列表最多展示条数(含因模型数上限被裁切的方向)
_UNUSED_DISPLAY_MAX = max(5, int(os.getenv("VIRAL_MODEL_UNUSED_DISPLAY_MAX", "12")))
# 每个 ElementCategory 最多展示几个 examples
_MAX_EXAMPLES_PER_CATEGORY = int(os.getenv("VIRAL_MODEL_MAX_EXAMPLES", "3"))
# unused_directions 的额外门槛: 至少要被 N 条爆款选中才有展示价值
_UNUSED_MIN_COUNT = int(os.getenv("VIRAL_MODEL_UNUSED_MIN", "2"))
# LLM 起名超时
_NAMING_TIMEOUT = int(os.getenv("VIRAL_MODEL_NAMING_TIMEOUT", "600"))

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


def _bigram_similarity(a: str, b: str) -> float:
    """基于字符 2-gram Jaccard 相似度 (0.0-1.0)。

    用于比较两个要素标注字符串的语义相近程度。
    - 完全相同 → 1.0
    - 完全无公共 bigram → 0.0
    - 短字符串 (< 2 字) fallback 到字面相等判断
    """
    if a == b:
        return 1.0
    a = (a or "").strip().lower()
    b = (b or "").strip().lower()
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    if len(a) < 2 or len(b) < 2:
        return 0.0
    def _bigrams(s: str):
        return {s[i : i + 2] for i in range(len(s) - 1)}
    bg_a, bg_b = _bigrams(a), _bigrams(b)
    inter = len(bg_a & bg_b)
    union = len(bg_a | bg_b)
    return inter / union if union else 0.0


def _centroid_element_match_score(a: Tuple[str, ...], b: Tuple[str, ...]) -> int:
    """仅比较 6 要素维(不含 content_direction),用于相似度。"""
    if len(a) < 2 or len(b) < 2:
        return 0
    ea, eb = a[1:], b[1:]
    return sum(1 for x, y in zip(ea, eb) if x == y)


def _centroid_fuzzy_match_score(a: Tuple[str, ...], b: Tuple[str, ...]) -> float:
    """模糊版 6 要素相似度(不含 content_direction),返回 0.0-6.0 的浮点分。

    每维得分为 _bigram_similarity,满分 6 分（6 维各满分 1.0）。
    替代精确 _centroid_element_match_score 用于合并判断。
    """
    if len(a) < 2 or len(b) < 2:
        return 0.0
    ea, eb = a[1:], b[1:]
    return sum(_bigram_similarity(x, y) for x, y in zip(ea, eb))


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
    # 模糊相似度阈值（6维满分6.0）：≥2.5约等于42%相似度即允许合并
    # 原4.0（67%）过严导致无合法对，几乎无法自然合并
    _fuzzy_threshold = float(os.getenv("VIRAL_MODEL_FUZZY_THRESHOLD", "2.5"))
    score = _centroid_fuzzy_match_score(ca, cb)
    if score >= _fuzzy_threshold:
        return True
    # 单侧极小簇（≤2条）：即使特征不够相似也允许被吸收，避免独单笔记成模型
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
    _fuzzy_threshold = float(os.getenv("VIRAL_MODEL_FUZZY_THRESHOLD", "4.0"))
    if _centroid_fuzzy_match_score(cv, ct) < _fuzzy_threshold:
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

    # 1) 按软上限合并相似簇；无合法相似对时自然停止
    guard = 0
    while len(clusters) > max_models and guard < total * 3:
        guard += 1
        best: Optional[Tuple[int, int]] = None
        best_score = -1.0
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
                sc = _centroid_fuzzy_match_score(ci["centroid"], cj["centroid"])
                ss = na + nb
                if sc > best_score or (sc == best_score and ss < best_size):
                    best_score = sc
                    best_size = ss
                    best = (i, j)
        if best is None:
            # 无合法相似对 → 自然停止（不强制合并，避免将无关簇硬塞在一起）
            logger.info(
                "[ViralModelAgent] 无合法合并对，自然停止(当前 {} 簇，软上限 {} 簇)",
                len(clusters),
                max_models,
            )
            break
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
                sc = _centroid_fuzzy_match_score(v["centroid"], t["centroid"])
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


def _optimize_note_assignments(
    matrix_rows: List[Tuple[str, List[str]]],
    annotations: Dict[str, Dict[str, Any]],
    max_iter: int = 5,
    min_gain: float = 1.0,
) -> Tuple[List[Tuple[str, List[str]]], int]:
    """K-means 式笔记最优分配：将每条笔记分配到特征最相似的模型质心。

    - 每轮重新计算质心，对所有笔记检查是否换到更匹配的模型（提升 ≥ min_gain 才换）。
    - 迭代至收敛或 max_iter 轮为止。
    - 空簇自动删除；若结果为空则返回原始 matrix_rows。
    - 不调 LLM：直接使用 6 要素维度的模糊相似度（LLM 已在上游完成语义标注）。

    Returns:
        (optimized_rows, total_reassigned_count)
    """
    if len(matrix_rows) <= 1:
        return matrix_rows, 0

    dirs: List[str] = [d for d, _ in matrix_rows]
    id_lists: List[List[str]] = [list(ids) for _, ids in matrix_rows]
    total_reassigned = 0

    for _it in range(max_iter):
        centroids = [_centroid_from_ids(ids, annotations) for ids in id_lists]
        new_id_lists: List[List[str]] = [[] for _ in id_lists]
        iter_changed = 0

        for ci, note_ids in enumerate(id_lists):
            for nid in note_ids:
                ann = annotations.get(nid) or {}
                note_sig = _note_signature_tuple(ann)
                cur_score = _centroid_fuzzy_match_score(note_sig, centroids[ci])

                best_ci = ci
                best_score = cur_score
                for cj, cj_centroid in enumerate(centroids):
                    if cj == ci:
                        continue
                    sc = _centroid_fuzzy_match_score(note_sig, cj_centroid)
                    if sc > best_score + min_gain:
                        best_score = sc
                        best_ci = cj

                new_id_lists[best_ci].append(nid)
                if best_ci != ci:
                    iter_changed += 1

        id_lists = new_id_lists
        total_reassigned += iter_changed
        if iter_changed == 0:
            break

    # 移除空簇，保留原方向标签
    result: List[Tuple[str, List[str]]] = [
        (dirs[i], ids) for i, ids in enumerate(id_lists) if ids
    ]
    if not result:
        return matrix_rows, 0

    result.sort(key=lambda r: (-len(r[1]), str(r[0])))
    return result, total_reassigned


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

        # 向思考框展示初步分组情况
        dir_preview = "、".join(
            f"「{d}」({len(groups[d])}条)" for d in list(groups.keys())[:8]
        ) + ("..." if len(groups) > 8 else "")
        await self._emit_thinking_chunk(
            task_id,
            f"Step 1 · 初步分组：对 {total} 条已标注笔记按 content_direction 分组，"
            f"发现 {len(groups)} 个方向：{dir_preview}\n",
            is_reasoning=True,
        )

        # ---- Step 1.5: LLM 语义归一化 content_direction (唯一方向 > 2 时才触发) ----
        unique_dirs = [d for d in groups if d != "未知"]
        if len(unique_dirs) > 2:
            annotations = await self._llm_normalize_directions(
                task_id, annotations, unique_dirs
            )
            # 归一化后重建 groups（用于 unused_directions 统计）
            groups = defaultdict(list)
            for note_id, ann in annotations.items():
                direction = (ann.get("content_direction") or "未知").strip() or "未知"
                groups[direction].append(note_id)

        # ---- Step 2: playbook 签名聚类 → 矩阵行(条数 ≤ 动态上限,随数据可为 1～上限) ----
        dyn_max = _compute_dynamic_max(total)
        matrix_rows = _build_playbook_clusters(annotations, dyn_max)

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
            f"(动态上限 {dyn_max},原始 direction 分组 {len(groups)} 个)"
        )
        await self.emit_progress(task_id, msg, progress=55)

        # 向思考框展示聚类结果
        cluster_preview = "、".join(
            f"「{d}」({len(ids)}条)" for d, ids in matrix_rows[:6]
        ) + ("..." if len(matrix_rows) > 6 else "")
        await self._emit_thinking_chunk(
            task_id,
            f"Step 2 · playbook 聚类：基于六要素签名二次聚类（动态上限 {dyn_max} 个模型），"
            f"初步生成 {len(matrix_rows)} 个爆文模型：{cluster_preview}\n",
            is_reasoning=True,
        )

        # ---- Step 2.5: 笔记最优分配（K-means 式迭代，不调 LLM） ----
        matrix_rows, reassigned = _optimize_note_assignments(
            matrix_rows, annotations, min_gain=0.3
        )
        _step25_msg = (
            f"[ViralModelAgent] Step2.5 笔记适配优化："
            f"检查 {sum(len(ids) for _, ids in matrix_rows)} 条笔记，"
            f"移动 {reassigned} 条至更匹配的模型，"
            f"当前 {len(matrix_rows)} 个模型"
        )
        logger.info(_step25_msg)
        await self.emit_log(task_id, "info", _step25_msg)
        await self._emit_thinking_chunk(
            task_id,
            f"Step 2.5 · K-means 迭代：对每条笔记计算与所有模型质心的余弦相似度，"
            f"将 {reassigned} 条笔记移至更匹配的模型，当前共 {len(matrix_rows)} 个模型。\n",
            is_reasoning=True,
        )

        # ---- Step 2.6: LLM 全面质量审查 —— 对所有模型检查笔记归属与名称合理性 ----
        _pre_review_count = len(matrix_rows)  # 记录审查前的模型数，供 Reflection 步骤判断
        _step26_msg = f"[ViralModelAgent] Step2.6 全面质量审查：{len(matrix_rows)} 个模型，发起 LLM 验证"
        logger.info(_step26_msg)
        await self.emit_progress(
            task_id,
            f"爆文矩阵:LLM 全面质量审查（{len(matrix_rows)} 个模型，验证笔记归属与合并建议）",
            progress=57,
        )
        matrix_rows = await self._llm_validate_small_clusters(
            task_id, matrix_rows, annotations, small_threshold=len(matrix_rows)
        )

        # ---- Step 2.7: LLM 笔记适配度审查 —— 逐模型读取每条笔记内容，识别不符合笔记并重新分配/拆分 ----
        _step27_msg = (
            f"[ViralModelAgent] Step2.7 笔记适配度审查启动：{len(matrix_rows)} 个模型，"
            f"{sum(len(ids) for _, ids in matrix_rows)} 条笔记"
        )
        logger.info(_step27_msg)
        await self.emit_progress(
            task_id,
            f"爆文矩阵:LLM 笔记级别适配审查（{len(matrix_rows)} 个模型，识别错误归类）",
            progress=58,
        )
        matrix_rows = await self._llm_review_note_fit(
            task_id, matrix_rows, annotations, notes_by_id, total
        )

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

        # ---- Step 4b: Reflection — 质量自评估，低质量时重试一次 ----
        # 注意：若 Step 2.6 已执行 LLM 质量审查并合并了模型，说明质量问题已处理，
        # 跳过 Reflection 重试，避免用重新聚类的结果覆盖 Step 2.6 的成果。
        _review_merged = _pre_review_count - len(matrix_rows)
        quality_score = self._evaluate_quality(models, total)
        if quality_score < 1.0 and _review_merged == 0:
            await self.emit_log(
                task_id, "warn",
                f"[Reflection] 爆文矩阵质量评分 {quality_score:.2f} < 1.0，触发重试（放宽参数）",
            )
            relaxed_max = min(dyn_max + 2, 10)
            retry_rows = _build_playbook_clusters(annotations, relaxed_max)
            # 用 _pre_review_count 而非当前 len(matrix_rows) 做下限比较，
            # 防止 Step 2.6 合并后的较小数量触发不必要的扩张重建
            if retry_rows and len(retry_rows) >= _pre_review_count:
                models = []
                for idx, (direction, note_ids) in enumerate(retry_rows, start=1):
                    model_id = f"M{idx}"
                    elements = self._compute_element_stats(model_id, note_ids, annotations, notes_by_id)
                    avg_interaction = self._avg_interaction(note_ids, notes_by_id)
                    models.append(
                        ViralModel(
                            model_id=model_id,
                            name=direction,
                            description="",
                            coverage=len(note_ids) / total,
                            avg_interaction=avg_interaction,
                            sample_note_ids=note_ids,
                            elements=elements,
                            paragraph_id=model_id,
                        )
                    )
                try:
                    await self._llm_name_models(task_id, models)
                except Exception as exc2:  # noqa: BLE001
                    logger.warning(f"[ViralModelAgent] 重试起名失败({exc2}),保留 direction")
                matrix_rows = retry_rows
                await self.emit_log(
                    task_id, "info",
                    f"[Reflection] 重试完成，生成 {len(models)} 个模型",
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
    # LLM 方向归一化(聚类前预处理)
    # ------------------------------------------------------------------

    async def _llm_normalize_directions(
        self,
        task_id: str,
        annotations: Dict[str, Dict[str, Any]],
        unique_dirs: List[str],
    ) -> Dict[str, Dict[str, Any]]:
        """调用 LLM 将同义 content_direction 归并为规范名。

        - 仅当唯一方向数 > 2 时才被调用。
        - LLM 失败/超时/解析失败均 fallback 到原始 annotations,不阻塞主流程。
        - 返回已应用规范名映射的 annotations 副本(浅复制字典,深复制标注行)。
        """
        _NORMALIZE_TIMEOUT = int(os.getenv("VIRAL_MODEL_NORMALIZE_TIMEOUT", "300"))
        _NORMALIZE_ENABLED = os.getenv("VIRAL_MODEL_DIRECTION_NORMALIZE", "true").lower() not in (
            "0", "false", "no", "off"
        )
        if not _NORMALIZE_ENABLED:
            return annotations

        # 向思考框展示归一化前的方向列表
        dirs_preview = "、".join(f"「{d}」" for d in unique_dirs[:12]) + (
            "..." if len(unique_dirs) > 12 else ""
        )
        await self._emit_thinking_chunk(
            task_id,
            f"Step 1.5 · 方向归一化：检测到 {len(unique_dirs)} 个方向标签，"
            f"调用 LLM 合并同义方向，避免措辞差异导致分组过多。\n"
            f"待归一化方向：{dirs_preview}\n",
            is_reasoning=True,
        )

        system_prompt = prompt_registry.load("viral_model_direction_normalize.md")
        user_content = json.dumps(unique_dirs, ensure_ascii=False)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        try:
            text = await asyncio.wait_for(
                self.chat_stream_and_emit(
                    task_id,
                    messages,
                    overrides={"temperature": 0.1, "max_tokens": 1000, "timeout": 300,
                               "response_format": {"type": "json_object"}},
                ),
                timeout=_NORMALIZE_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            await self.emit_log(
                task_id, "warn",
                f"[ViralModelAgent] 方向归一化 LLM 调用失败({type(exc).__name__}),保留原始方向"
            )
            return annotations

        text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()
        # 取 {...} 段
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return annotations
        try:
            mapping: Dict[str, str] = json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            return annotations

        if not isinstance(mapping, dict) or not mapping:
            return annotations

        # 校验 value 不能是无意义占位
        _BAD_VALUES = {"其他", "综合", "混合型", "其他类型", "综合内容", ""}
        mapping = {
            k: v
            for k, v in mapping.items()
            if isinstance(k, str) and isinstance(v, str)
            and v.strip() not in _BAD_VALUES
            and k != v  # 自身映射到自身无意义
        }

        if not mapping:
            return annotations

        merged_count = sum(1 for d in unique_dirs if d in mapping)
        await self.emit_log(
            task_id, "info",
            f"[ViralModelAgent] 方向归一化:共 {len(unique_dirs)} 个方向，"
            f"归并 {merged_count} 个 → {len(set(mapping.values()))} 个规范名",
        )

        # 向思考框展示归一化合并情况
        merge_strs = [f"「{k}」→「{v}」" for k, v in list(mapping.items())[:8]]
        if merge_strs:
            await self._emit_thinking_chunk(
                task_id,
                f"  归一化结果（{merged_count} 个方向被合并）："
                + "、".join(merge_strs)
                + ("..." if len(mapping) > 8 else "") + "\n",
                is_reasoning=True,
            )

        # 应用映射:浅复制 annotations dict，deep-copy 需要被修改的行
        normalized: Dict[str, Dict[str, Any]] = {}
        for nid, ann in annotations.items():
            raw_dir = (ann.get("content_direction") or "").strip()
            canonical = mapping.get(raw_dir)
            if canonical:
                ann = {**ann, "content_direction": canonical}
            normalized[nid] = ann
        return normalized

    # ------------------------------------------------------------------
    # LLM 小簇二次校验（Step 2.5）
    # ------------------------------------------------------------------

    async def _chat_until_json_parsed(
        self,
        task_id: str,
        messages: List[Dict[str, Any]],
        parser,           # Callable[[str], Any | None]
        label: str,
        stream_overrides: Optional[Dict[str, Any]] = None,
        stream_timeout: int = 600,
        max_retries: int = 3,
    ):
        """流式调用 → 解析 JSON；失败时关闭思考模式用非流式重试，直到成功或耗尽次数。

        Returns:
            解析结果（list / dict），全部失败时返回 None。
        """
        _json_fmt = {"type": "json_object"}
        overrides = dict(stream_overrides or {"temperature": 0.1, "max_tokens": 8000, "timeout": 600})
        # 强制启用 DeepSeek JSON mode，确保输出合法 JSON（官方要求同时 prompt 含 json 字样）
        overrides.setdefault("response_format", _json_fmt)

        for attempt in range(1, max_retries + 1):
            if attempt == 1:
                # 第一次：正常流式调用
                try:
                    text = await asyncio.wait_for(
                        self.chat_stream_and_emit(task_id, messages, overrides=overrides),
                        timeout=stream_timeout,
                    )
                except Exception as exc:
                    logger.warning("[{}] 第{}次流式调用失败: {}", label, attempt, exc)
                    await self.emit_log(task_id, "warn", f"[{label}] 第{attempt}次调用失败，重试…")
                    text = ""
            else:
                # 重试：非流式 + 关闭思考
                retry_msgs = list(messages)
                last = retry_msgs[-1]
                retry_msgs[-1] = {
                    "role": last["role"],
                    "content": last["content"]
                    + "\n\n【重要】请直接输出 JSON，不要输出任何思考过程。",
                }
                try:
                    resp = await asyncio.wait_for(
                        self._gateway.chat(
                            self.agent_id,
                            retry_msgs,
                            task_id=task_id,
                            overrides={"temperature": 0.1, "max_tokens": 3000, "timeout": 120,
                                       "response_format": _json_fmt},
                        ),
                        timeout=150,
                    )
                    text = (resp.get("content") or "").strip()
                    # 剥离可能残留的 think 块
                    if "</think>" in text:
                        text = text.split("</think>", 1)[1].strip()
                    elif "<think>" in text:
                        text = re.sub(r"<think>[\s\S]*", "", text, flags=re.IGNORECASE).strip()
                    logger.debug("[{}] 第{}次非流式回复 {} 字符", label, attempt, len(text))
                except Exception as exc:
                    logger.warning("[{}] 第{}次非流式重试失败: {}", label, attempt, exc)
                    await self.emit_log(task_id, "warn", f"[{label}] 第{attempt}次重试失败")
                    continue

            # 剥离 think（流式路径）
            text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()

            if not text:
                logger.warning("[{}] 第{}次回复为空，重试", label, attempt)
                await self.emit_log(task_id, "warn", f"[{label}] 第{attempt}次响应为空，重试…")
                continue

            result = parser(text)
            if result is not None:
                if attempt > 1:
                    await self.emit_log(task_id, "info", f"[{label}] 第{attempt}次重试解析成功")
                return result

            logger.warning(
                "[{}] 第{}次 JSON 解析失败，完整输出({} 字): {!r}", label, attempt, len(text), text
            )
            await self.emit_log(task_id, "warn", f"[{label}] 第{attempt}次 JSON 解析失败，重试…")

        logger.error("[{}] {} 次尝试均解析失败，返回 None", label, max_retries)
        await self.emit_log(task_id, "warn", f"[{label}] {max_retries} 次均失败，使用兜底")
        return None

    async def _llm_validate_small_clusters(
        self,
        task_id: str,
        matrix_rows: List[Tuple[str, List[str]]],
        annotations: Dict[str, Dict[str, Any]],
        small_threshold: int = 3,
    ) -> List[Tuple[str, List[str]]]:
        """LLM 质量审查：全面模式（small_threshold >= n）或小簇模式。

        全面模式：检查所有模型，发现语义重叠/冗余的模型建议合并。
        小簇模式：仅检查 ≤ small_threshold 条的小模型，建议并入大模型。
        LLM 失败/超时/解析失败均 fallback 保留原始 matrix_rows，不阻塞主流程。
        """
        _VALIDATE_TIMEOUT = int(os.getenv("VIRAL_MODEL_VALIDATE_TIMEOUT", "600"))
        _VALIDATE_ENABLED = os.getenv("VIRAL_MODEL_SMALL_VALIDATE", "true").lower() not in (
            "0", "false", "no", "off"
        )
        if not _VALIDATE_ENABLED:
            await self.emit_log(task_id, "info", "[ViralModelAgent] 质量审查已禁用（VIRAL_MODEL_SMALL_VALIDATE=false）")
            return matrix_rows

        n = len(matrix_rows)
        is_full_review = small_threshold >= n
        mode_label = "全面质量审查" if is_full_review else "小簇验证"

        if is_full_review:
            # 全面审查：所有模型既是候选源也是候选目标
            candidate_idxs = list(range(n))
            target_idxs = list(range(n))
        else:
            candidate_idxs = [i for i, (_, ids) in enumerate(matrix_rows) if 1 <= len(ids) <= small_threshold]
            target_idxs = [i for i, (_, ids) in enumerate(matrix_rows) if len(ids) > small_threshold]
            if not candidate_idxs or not target_idxs:
                await self.emit_log(
                    task_id, "info",
                    f"[{mode_label}] 无需检查（无小簇或无大簇），跳过",
                )
                return matrix_rows

        # 构造每个簇的摘要（质心要素 + 方向标签）
        cluster_summaries = []
        for i, (direction, ids) in enumerate(matrix_rows):
            centroid = _centroid_from_ids(ids, annotations)
            elem = {
                fname: (centroid[fi] if fi < len(centroid) else "")
                for fi, fname in enumerate(_CENTROID_FIELD_ORDER)
            }
            cluster_summaries.append({
                "idx": i,
                "size": len(ids),
                "direction": direction,
                "elements": elem,
                "is_candidate": i in candidate_idxs,
            })

        await self.emit_log(
            task_id, "info",
            f"[{mode_label}] 开始：{n} 个模型，总计 {sum(len(ids) for _, ids in matrix_rows)} 条笔记，"
            f"LLM 检查{'所有模型' if is_full_review else f'{len(candidate_idxs)} 个候选模型'}",
        )
        logger.info(
            "[ViralModelAgent] {} 开始：{} 个模型，{} 条笔记，LLM检查{}",
            mode_label, n, sum(len(ids) for _, ids in matrix_rows),
            "所有模型" if is_full_review else f"{len(candidate_idxs)} 个候选模型",
        )

        # 向思考框发送模型审查开始提示
        sum_notes = sum(len(ids) for _, ids in matrix_rows)
        model_list = "、".join(
            f"「{d}」({len(ids)}条)" for d, ids in matrix_rows[:6]
        ) + ("..." if n > 6 else "")
        await self._emit_thinking_chunk(
            task_id,
            f"{'全面模型质量审查' if is_full_review else '小簇验证'}：检查 {n} 个模型（共 {sum_notes} 条笔记）"
            f"是否存在语义重叠或冗余，判断是否需要合并。\n"
            f"当前模型：{model_list}\n",
            is_reasoning=True,
        )

        mode_str = "full_review" if is_full_review else "small_cluster_validate"
        system_prompt = prompt_registry.load("viral_model_small_cluster_validate.md")
        system_prompt = system_prompt.replace(
            "{{CLUSTERS_JSON}}", json.dumps(cluster_summaries, ensure_ascii=False, indent=2)
        )
        system_prompt = system_prompt.replace("{{CANDIDATE_IDXS}}", str(candidate_idxs))
        system_prompt = system_prompt.replace("{{MODE}}", mode_str)

        user_msg = (
            "请对所有模型进行全面质量审查，找出语义重叠或冗余的模型并建议合并，返回 JSON 决策数组。"
            if is_full_review
            else "请验证上述小模型，返回 JSON 决策数组。"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]

        try:
            decisions = await self._chat_until_json_parsed(
                task_id,
                messages,
                parser=_robust_extract_json_array,
                label=mode_label,
                stream_overrides={"temperature": 0.1, "max_tokens": 8000, "timeout": 600},
                stream_timeout=_VALIDATE_TIMEOUT,
                max_retries=3,
            )
        except Exception as exc:  # noqa: BLE001
            _warn = f"[ViralModelAgent] {mode_label} LLM 调用异常({type(exc).__name__})，保留原始分组"
            logger.warning(_warn)
            await self.emit_log(task_id, "warn", _warn)
            return matrix_rows

        if decisions is None or not isinstance(decisions, list):
            await self.emit_log(task_id, "warn", f"[{mode_label}] 多次重试仍解析失败，保留原分组")
            return matrix_rows

        # 应用决策：构建可变列表，执行合并
        rows: List[List] = [[d, list(ids)] for d, ids in matrix_rows]
        merged_away: set = set()
        merge_count = 0
        keep_count = 0

        for dec in decisions:
            if not isinstance(dec, dict):
                continue
            idx = dec.get("idx")
            action = str(dec.get("action", "keep")).lower()
            reason = str(dec.get("reason", ""))

            if not isinstance(idx, int) or idx not in range(n) or idx in merged_away:
                continue
            # 非全面模式：只处理候选模型
            if not is_full_review and idx not in candidate_idxs:
                continue

            if action == "merge":
                target = dec.get("merge_into")
                if (
                    not isinstance(target, int)
                    or target not in range(n)
                    or target in merged_away
                    or target == idx
                ):
                    await self.emit_log(
                        task_id, "warn",
                        f"[{mode_label}] idx={idx} merge_into={target} 无效，保留",
                    )
                    keep_count += 1
                    continue
                rows[target][1].extend(rows[idx][1])
                merged_away.add(idx)
                merge_count += 1
                await self.emit_log(
                    task_id, "info",
                    f"[{mode_label}] 模型{idx}('{matrix_rows[idx][0]}',{len(matrix_rows[idx][1])}条)"
                    f" → 并入模型{target}('{matrix_rows[target][0]}'): {reason}",
                )
            else:
                keep_count += 1
                # 全面模式下只记录被"保留"的候选（避免日志过多）
                if not is_full_review or len(matrix_rows[idx][1]) <= 3:
                    await self.emit_log(
                        task_id, "info",
                        f"[{mode_label}] 模型{idx}('{matrix_rows[idx][0]}',{len(matrix_rows[idx][1])}条)"
                        f" → 保留独立: {reason}",
                    )

        new_rows: List[Tuple[str, List[str]]] = [
            (row[0], row[1]) for i, row in enumerate(rows) if i not in merged_away
        ]
        _done_msg = f"[ViralModelAgent] {mode_label} 完成：合并 {merge_count} 个冗余模型 → 最终 {len(new_rows)} 个模型"
        logger.info(_done_msg)
        await self.emit_log(task_id, "info", _done_msg)
        # 向思考框追加审查结论
        await self._emit_thinking_chunk(
            task_id,
            f"✓ {'全面质量审查' if is_full_review else '小簇验证'}完成："
            f"合并 {merge_count} 个冗余模型，保留 {keep_count} 个独立模型，"
            f"最终 {len(new_rows)} 个爆文模型。\n",
            is_reasoning=True,
        )
        return new_rows

    # ------------------------------------------------------------------
    # Step 2.7: LLM 笔记适配度审查 —— 逐模型读取每条笔记内容，判断是否真正符合模型
    # ------------------------------------------------------------------

    async def _llm_review_note_fit(
        self,
        task_id: str,
        matrix_rows: List[Tuple[str, List[str]]],
        annotations: Dict[str, Dict[str, Any]],
        notes_by_id: Dict[str, Dict[str, Any]],
        total: int,
    ) -> List[Tuple[str, List[str]]]:
        """LLM 逐模型审查每条笔记的真实适配度（逐模型独立调用，避免整体超时）。

        与 Step 2.6（簇级合并审查）互补：
        - Step 2.6 关注：哪些模型应该合并（模型粒度）
        - Step 2.7 关注：某条笔记是否真的属于当前模型（笔记粒度）

        改进：每个模型单独一次 LLM 调用，payload 小、不超时；
        某个模型调用失败时只跳过该模型，不影响其他模型。
        """
        _REVIEW_TIMEOUT = int(os.getenv("VIRAL_MODEL_NOTE_REVIEW_TIMEOUT", "600"))
        _REVIEW_ENABLED = os.getenv("VIRAL_MODEL_NOTE_REVIEW", "true").lower() not in (
            "0", "false", "no", "off"
        )
        if not _REVIEW_ENABLED:
            logger.info("[ViralModelAgent] Step2.7 笔记适配度审查已禁用（VIRAL_MODEL_NOTE_REVIEW=false）")
            return matrix_rows

        n = len(matrix_rows)
        if n == 0:
            return matrix_rows

        # 为每条笔记提取摘要信息（标题 + 关键要素），避免 token 过多
        def _note_summary(note_id: str) -> dict:
            ann = annotations.get(note_id) or {}
            raw = notes_by_id.get(note_id) or {}
            title = (
                raw.get("title") or raw.get("note_title") or ann.get("title") or ""
            )[:40]
            return {
                "note_id": note_id,
                "title": title,
                "content_direction": (ann.get("content_direction") or "")[:20],
                "hook_type": (ann.get("hook_type") or "")[:20],
                "visual_style": (ann.get("visual_style") or "")[:20],
                "emotional_tone": (ann.get("emotional_tone") or "")[:20],
            }

        # 全部模型概览（简短，供 LLM 选择移动目标时参考）
        all_models_brief = "\n".join(
            f"- 模型{i}（idx={i}）：{direction}（{len(note_ids)} 条笔记）"
            for i, (direction, note_ids) in enumerate(matrix_rows)
        )

        oversized_indices = {
            i for i, (_, note_ids) in enumerate(matrix_rows) if len(note_ids) > total * 0.30
        }
        oversized_names = [matrix_rows[i][0] for i in oversized_indices]
        logger.info(
            "[ViralModelAgent] Step2.7 笔记适配度审查（逐模型）：{} 个模型，{} 条笔记，过大模型：{}",
            n,
            sum(len(ids) for _, ids in matrix_rows),
            oversized_names or "无",
        )
        await self.emit_progress(
            task_id,
            f"爆文矩阵:LLM 笔记适配度审查（{n} 个模型，逐模型独立审查）",
            progress=59,
        )

        system_prompt_tpl = prompt_registry.load("viral_model_note_fit_review.md")
        system_prompt_tpl = system_prompt_tpl.replace(
            "{{ALL_MODELS_BRIEF}}", all_models_brief
        )

        # 应用审查决策（可变 rows）
        rows: List[List] = [[d, list(ids)] for d, ids in matrix_rows]
        move_count = 0
        split_count = 0

        for i in range(n):
            direction, note_ids = matrix_rows[i]
            note_summaries = [_note_summary(nid) for nid in note_ids]
            model_data = {
                "model_idx": i,
                "direction": direction,
                "note_count": len(note_ids),
                "is_oversized": i in oversized_indices,
                "notes": note_summaries,
            }
            user_content = (
                "请审查以下模型中的笔记，找出不符合该模型的笔记并建议移动；"
                "若为 is_oversized=true 的大模型，判断是否需要拆分。\n\n"
                + json.dumps(model_data, ensure_ascii=False, indent=2)
            )
            messages = [
                {"role": "system", "content": system_prompt_tpl},
                {"role": "user", "content": user_content},
            ]

            await self.emit_log(
                task_id, "info",
                f"[Step2.7] 审查模型 {i+1}/{n}：{direction}（{len(note_ids)} 条笔记）",
            )
            rev = await self._chat_until_json_parsed(
                task_id,
                messages,
                parser=_robust_extract_json_object,
                label=f"Step2.7-模型{i}",
                stream_overrides={"temperature": 0.2, "max_tokens": 8000, "timeout": 600},
                stream_timeout=_REVIEW_TIMEOUT,
                max_retries=3,
            )
            if rev is None or not isinstance(rev, dict):
                await self.emit_log(task_id, "warn", f"[Step2.7] 模型{i}('{direction}') 多次重试仍失败，保留")
                continue

            # 应用本模型的审查决策（rev 已是单个 dict）
            if not isinstance(rev, dict):
                continue
            model_idx = rev.get("model_idx")
            action = str(rev.get("action", "ok")).lower()
            reason = str(rev.get("reason", ""))

            if not isinstance(model_idx, int) or model_idx not in range(n):
                continue

            # 向思考框追加当前模型决策摘要
            if action == "ok":
                await self._emit_thinking_chunk(
                    task_id,
                    f"  「{direction}」→ 分类合理，无需调整。\n",
                    is_reasoning=True,
                )
            elif action == "move_notes":
                moves_preview = rev.get("moves") or []
                await self._emit_thinking_chunk(
                    task_id,
                    f"  「{direction}」→ 发现 {len(moves_preview)} 条笔记需移动：{reason}\n",
                    is_reasoning=True,
                )
            elif action == "split":
                await self._emit_thinking_chunk(
                    task_id,
                    f"  「{direction}」→ 建议拆分为子类型：{reason}\n",
                    is_reasoning=True,
                )

            if action == "move_notes":
                moves = rev.get("moves") or []
                for mv in moves:
                    if not isinstance(mv, dict):
                        continue
                    note_id = mv.get("note_id")
                    move_to = mv.get("move_to_idx")
                    mv_reason = str(mv.get("reason", ""))

                    if (
                        not note_id
                        or not isinstance(move_to, int)
                        or move_to not in range(n)
                        or move_to == model_idx
                        or note_id not in rows[model_idx][1]
                    ):
                        continue

                    rows[model_idx][1].remove(note_id)
                    rows[move_to][1].append(note_id)
                    move_count += 1
                    logger.info(
                        "[Step2.7] 笔记移动: {} 从模型{}({}) → 模型{}({}): {}",
                        note_id, model_idx, rows[model_idx][0],
                        move_to, rows[move_to][0], mv_reason,
                    )
                    await self.emit_log(
                        task_id, "info",
                        f"[Step2.7] 笔记'{note_id[:8]}...' 从模型{model_idx}('{rows[model_idx][0]}') "
                        f"移至模型{move_to}('{rows[move_to][0]}'): {mv_reason}",
                    )

            elif action == "split":
                groups_data = rev.get("split_groups") or []
                # 验证：每组至少3条，且总笔记数不超过原模型
                valid_groups = [
                    g for g in groups_data
                    if isinstance(g, dict)
                    and isinstance(g.get("note_ids"), list)
                    and len(g["note_ids"]) >= 3
                ]
                all_split_notes: set = set()
                for g in valid_groups:
                    all_split_notes.update(g["note_ids"])

                # 确保拆分后覆盖了足够多的原始笔记（至少60%），否则不拆分
                orig_notes = set(rows[model_idx][1])
                if len(valid_groups) >= 2 and len(all_split_notes & orig_notes) >= len(orig_notes) * 0.6:
                    # 第一组替换原模型
                    first_group = valid_groups[0]
                    rows[model_idx][1] = [nid for nid in first_group["note_ids"] if nid in orig_notes]
                    rows[model_idx][0] = first_group.get("sub_direction") or rows[model_idx][0]

                    # 其余组追加为新模型
                    for g in valid_groups[1:]:
                        new_note_ids = [nid for nid in g["note_ids"] if nid in orig_notes]
                        sub_dir = g.get("sub_direction") or f"{rows[model_idx][0]}·变体"
                        rows.append([sub_dir, new_note_ids])
                        n = len(rows)  # 更新 n

                    split_count += 1
                    logger.info(
                        "[Step2.7] 模型{}('{}') 拆分为 {} 个子类型: {}",
                        model_idx,
                        matrix_rows[model_idx][0],
                        len(valid_groups),
                        reason,
                    )
                    await self.emit_log(
                        task_id, "info",
                        f"[Step2.7] 模型{model_idx}('{matrix_rows[model_idx][0]}',"
                        f"{len(matrix_rows[model_idx][1])}条) "
                        f"→ 拆分为 {len(valid_groups)} 个子类型: {reason}",
                    )
                else:
                    logger.info(
                        "[Step2.7] 模型{}('{}') 拆分被拒绝（子组不足或覆盖率低），保留",
                        model_idx, matrix_rows[model_idx][0],
                    )

        # 移除拆分/移动后为空的模型
        new_rows: List[Tuple[str, List[str]]] = [
            (row[0], row[1]) for row in rows if row[1]
        ]

        _done_msg = (
            f"[ViralModelAgent] Step2.7 笔记适配度审查完成："
            f"移动 {move_count} 条笔记，拆分 {split_count} 个模型 → 最终 {len(new_rows)} 个模型"
        )
        logger.info(_done_msg)
        await self.emit_log(task_id, "info", _done_msg)
        await self._emit_thinking_chunk(
            task_id,
            f"\n✓ 笔记适配度审查完成：共移动 {move_count} 条笔记，拆分 {split_count} 个模型，"
            f"最终 {len(new_rows)} 个爆文模型。进入模型命名阶段...\n",
            is_reasoning=True,
        )
        return new_rows

    # ------------------------------------------------------------------
    # LLM 起名(批量一次,JSON 数组)
    # ------------------------------------------------------------------

    async def _llm_name_models(
        self, task_id: str, models: List[ViralModel]
    ) -> None:
        """让 LLM 给每个模型起名 + 写模型定义。失败时模型名保持 direction。"""
        if not models:
            return
        system_prompt = prompt_registry.load("viral_model_naming.md")

        # 构造 user 内容: 每个模型的 direction + coverage + 高频要素简表
        user_lines = ["请给以下爆文模型起名，严格按输入顺序输出 JSON 数组:\n"]
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

        # 使用统一重试机制：流式 → JSON 解析失败 → 关闭思考模式非流式重试
        parsed = await self._chat_until_json_parsed(
            task_id,
            messages,
            parser=self._extract_json_array,
            label="模型起名",
            stream_overrides={"temperature": 0.3, "max_tokens": 8000, "timeout": 600},
            stream_timeout=_NAMING_TIMEOUT,
            max_retries=3,
        )

        if not parsed:
            await self.emit_log(task_id, "warn", "模型起名 LLM 均失败，保留算法默认名")
            return

        # 按 model_id 匹配回填(LLM 顺序可能错乱)
        # 策略：先大小写不敏感精确匹配；若全部未命中则按位置顺序兜底
        by_id = {m.model_id: m for m in models}
        by_id_lower = {k.lower(): v for k, v in by_id.items()}

        def _apply(model, entry: dict) -> None:
            name = self._sanitize_name(entry.get("name") or "")
            defn = self._sanitize_definition(entry.get("definition") or "")
            if name:
                model.name = name
            if defn:
                model.definition = defn

        valid_entries = [e for e in parsed if isinstance(e, dict)]
        matched_count = 0
        for entry in valid_entries:
            raw_mid = (entry.get("model_id") or entry.get("id") or "").strip()
            # 先精确匹配，再大小写不敏感匹配
            model = by_id.get(raw_mid) or by_id_lower.get(raw_mid.lower())
            if model:
                _apply(model, entry)
                matched_count += 1

        # 全部未命中 → 按位置顺序兜底（LLM 漏掉了 model_id 字段）
        if matched_count == 0 and valid_entries:
            logger.warning(
                "[ViralModelAgent] 起名回复均无 model_id，按顺序位置兜底回填 {} 条",
                min(len(valid_entries), len(models)),
            )
            for entry, model in zip(valid_entries, models):
                _apply(model, entry)

    @staticmethod
    def _extract_json_array(text: str) -> Optional[List[Any]]:
        """粗暴提取 JSON 数组(不复用 _json_parsing,因为它只支持 dict)。"""
        return _robust_extract_json_array(text)

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

    @staticmethod
    def _sanitize_definition(defn: str) -> str:
        """属加种差法/操作性定义: 去空白、限长 ≤200 字符。"""
        s = (defn or "").strip()
        return s[:200]

    # ------------------------------------------------------------------
    # 质量评估（Reflection）
    # ------------------------------------------------------------------

    @staticmethod
    def _evaluate_quality(models: List[ViralModel], total_annotations: int) -> float:
        """规则打分，返回 0.0~1.0。低于 1.0 表示质量不足，触发重试。

        扣分规则：
        - 模型数 < 2 且样本量 >= 10：可能聚类过保守，扣 0.3
        - 任意模型 elements 中所有 code 的 categories 均为空：扣 0.4
        - 任意两模型 sample_note_ids 重叠 > 30%：说明分类重叠，扣 0.3
        - 任意模型 coverage < 2% 且 total >= 20：过小碎片簇，扣 0.2
        """
        if not models:
            return 0.0
        score = 1.0
        if len(models) < 2 and total_annotations >= 10:
            score -= 0.3
        for m in models:
            all_empty = all(not cats for cats in m.elements.values())
            if all_empty:
                score -= 0.4
                break

        # 模型间 sample_note_ids 重叠检测
        for i in range(len(models)):
            for j in range(i + 1, len(models)):
                ids_i = set(models[i].sample_note_ids)
                ids_j = set(models[j].sample_note_ids)
                if not ids_i or not ids_j:
                    continue
                overlap = len(ids_i & ids_j)
                smaller = min(len(ids_i), len(ids_j))
                if smaller > 0 and overlap / smaller > 0.3:
                    score -= 0.3
                    break  # 发现一对重叠即扣分，不累计
            else:
                continue
            break

        # 过小碎片簇检测
        if total_annotations >= 20:
            for m in models:
                if m.coverage < 0.02:
                    score -= 0.2
                    break

        return max(0.0, score)

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


# ──────────────────────────────────────────────────────────────────────────────
# 模块级 JSON 健壮解析工具（供 ViralModelAgent 内各函数共用）
# ──────────────────────────────────────────────────────────────────────────────

def _escape_ctrl_in_strings(text: str) -> str:
    """转义 JSON 字符串值内的真实控制字符（换行、回车、制表符）。

    LLM 有时在 reason/summary 字段中插入真实换行符（0x0A），违反 JSON 规范。
    字符级扫描，仅对处于字符串值内的控制符做转义。
    """
    out: list[str] = []
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and in_string:
            out.append(ch)
            i += 1
            if i < len(text):
                out.append(text[i])
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
        elif in_string and ch == "\n":
            out.append("\\n")
        elif in_string and ch == "\r":
            out.append("\\r")
        elif in_string and ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _try_raw_decode(text: str, marker: str) -> Optional[Any]:
    """从 text 中扫描所有 marker 字符位置，逐一尝试 raw_decode，返回第一个成功结果。

    相比只尝试第一个位置，可以跳过思考前缀中意外出现的同名符号。
    """
    decoder = json.JSONDecoder()
    for candidate in (text, _escape_ctrl_in_strings(text)):
        pos = 0
        while True:
            idx = candidate.find(marker, pos)
            if idx == -1:
                break
            try:
                data, _ = decoder.raw_decode(candidate, idx)
                return data
            except (json.JSONDecodeError, ValueError):
                pos = idx + 1
    return None


def _preprocess_llm_json(text: str) -> str:
    """去除 think 块 + 提取代码围栏内容（若有）。"""
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()
    m = re.search(r"```(?:json|JSON)?\s*\n?([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    return text


def _robust_extract_json_array(text: str) -> Optional[List[Any]]:
    """健壮地从 LLM 输出中提取第一个 JSON 数组。

    处理：<think> 块、代码围栏、尾部注释文字、字符串内真实换行符。
    """
    text = _preprocess_llm_json(text)
    data = _try_raw_decode(text, "[")
    return data if isinstance(data, list) else None


def _robust_extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """健壮地从 LLM 输出中提取第一个 JSON 对象（dict）。

    处理：<think> 块、代码围栏、尾部注释文字、字符串内真实换行符。
    """
    text = _preprocess_llm_json(text)
    data = _try_raw_decode(text, "{")
    return data if isinstance(data, dict) else None
