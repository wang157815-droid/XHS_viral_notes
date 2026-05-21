"""
InsightAgent: 简化为一次 LLM 调用(阶段 4.3pre.3 重构)。

**变化(相比 4.3pre.2)**:
- 彻底移除对旧 semantic_output(industry/competitor/brand 三 sub-dict)的适配层
- 产出新 Canvas 两模块的原生 schema:
    * mod-pain-points 的 items(带频次 count)
    * mod-seo-insights 的 core_keywords / long_tail(带频次 count)
- 新增 stats_axis_label(默认 "高频痛点 / 议程",可被 LLM 或任务元数据覆盖)
- provides 从 3 旧洞察模块改为 ["mod-pain-points", "mod-seo-insights"]

输入:
- viral_model_output: ViralModelMatrix 矩阵(由 ViralModelAgent 产出)
- multimodal_output.annotations: 6 要素标注(含 pain_keywords)
- crawler_output.sources.competitor: 竞品笔记(用于 SEO 聚合)

输出 semantic_output(新 schema,LLM 原生直存):
  {
    "content_direction": {
        "top_direction": "...",
        "summary_points": [...],
        "highlight": "..."
    },
    "pain_points_top": [           # 带频次,画布 mod-pain-points.items 直接消费
        {"keyword": "黑眼圈", "count": 8},
        ...
    ],
    "seo_aggregation": {
        "core_keywords": [{"keyword": "...", "count": N}, ...],
        "long_tail":     [{"keyword": "...", "count": N}, ...],
        "differentiation_advice": "一句差异化建议"
    },
    "stats_axis_label": "高频痛点 / 议程"
  }
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from ...domain.task_context import TaskContextWriter
from ...llm.model_gateway import ModelInvocationError
from ._json_parsing import extract_json_object
from .base import AgentContext, AgentResult, BaseAgent
from .prompts import prompt_registry


# 决策 #2A: 默认轴名,避免把"皮肤问题"这样的品类特定表头硬编码到画布/导出
_DEFAULT_STATS_AXIS_LABEL = "高频痛点 / 议程"

# SEO 关键词聚合相关常量
_SEO_CORE_TOP = 10             # 核心高频词数量
_SEO_LONG_TAIL_TOP = 10        # 长尾词数量
_SEO_COMPETITOR_LIMIT = 24     # 参与聚合的竞品笔记上限
_SEO_MIN_COUNT_CORE = 2        # 核心词至少出现 2 次
_SEO_TOKEN_MIN_LEN = 2
_SEO_TOKEN_MAX_LEN = 12


class InsightAgent(BaseAgent):
    agent_id = "InsightAgent"
    # 4.3pre.3: 改为直接产出两个新画布模块
    provides = ["mod-pain-points", "mod-seo-insights"]
    # 依赖 viral_model 已完成(它本身依赖 multimodal.annotations),
    # 同时消费 crawler_output.sources.competitor(竞品 SEO)
    depends_on = ["mod-viral-model-matrix"]
    write_partition = "semantic_output"

    async def run(self, context: AgentContext) -> AgentResult:
        task_id = context.task_id
        viral_matrix = context.task_context.get("viral_model_output") or {}
        multimodal = context.task_context.get("multimodal_output") or {}
        crawler = context.task_context.get("crawler_output") or {}
        input_spec = context.task_context.get("input_spec") or {}

        annotations: Dict[str, Dict[str, Any]] = multimodal.get("annotations") or {}

        # ---- 统计聚合(LLM 之前的 determinstic 计算) ----
        pain_top = self._aggregate_pain_keywords(annotations)
        sources = crawler.get("sources") or {}
        competitor_titles = self._collect_competitor_titles(sources)
        core_keywords, long_tail = self._aggregate_seo_keywords(sources)

        await self.emit_progress(task_id, "洞察:聚合矩阵 + 痛点 + SEO", progress=70)

        # ---- LLM 一次调用(只用来给出 content_direction 总结 + 差异化建议 + 可选轴名) ----
        llm_result = await self._run_llm_summary(
            task_id=task_id,
            viral_matrix=viral_matrix,
            pain_top=pain_top,
            competitor_titles=competitor_titles,
            core_keywords=core_keywords,
        )

        # ---- 组装 semantic_output(新原生 schema,不再适配旧 3 段) ----
        stats_axis_label = self._resolve_stats_axis_label(
            llm_result, input_spec, default=_DEFAULT_STATS_AXIS_LABEL
        )

        # ---- LLM 第二次调用: 为每条痛点生成本质定义 + 核心诉求阐释 ----
        if pain_top:
            await self._run_llm_pain_insight(task_id, pain_top, stats_axis_label)

        output: Dict[str, Any] = {
            "content_direction": self._build_content_direction(
                llm_result, viral_matrix
            ),
            # 画布 mod-pain-points.items 直接消费:已带 count 频次
            # essence_definition / core_appeal 由 _run_llm_pain_insight 回填(可选)
            "pain_points_top": pain_top,
            "seo_aggregation": {
                "core_keywords": core_keywords,
                "long_tail": long_tail,
                "differentiation_advice": str(
                    (llm_result.get("seo_aggregation") or {}).get(
                        "differentiation_advice"
                    )
                    or ""
                ).strip(),
            },
            "stats_axis_label": stats_axis_label,
        }

        TaskContextWriter(context.task_context).write(
            "semantic_output",
            output,
            agent_id=self.agent_id,
            note="insight_native_v3",
        )
        return AgentResult(ok=True, produced_modules=self.provides, output=output)

    # ------------------------------------------------------------------
    # 输入聚合(determinstic)
    # ------------------------------------------------------------------

    @staticmethod
    def _aggregate_pain_keywords(
        annotations: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """从 annotations[*].pain_keywords 分词计数,返回 `[{keyword, count}]`(top 8)。

        pain_keywords 可能是逗号 / 斜杠 / 顿号 / 分号等混合分隔的字符串。
        """
        if not annotations:
            return []
        counter: Counter = Counter()
        for ann in annotations.values():
            raw = (ann.get("pain_keywords") or "").strip()
            if not raw:
                continue
            for token in _split_tokens(raw):
                t = token.strip()
                if t and _SEO_TOKEN_MIN_LEN <= len(t) <= _SEO_TOKEN_MAX_LEN:
                    counter[t] += 1
        return [
            {"keyword": word, "count": cnt}
            for word, cnt in counter.most_common(8)
        ]

    @staticmethod
    def _collect_competitor_titles(sources: Dict[str, Any]) -> List[str]:
        """收集竞品笔记标题(给 LLM 做差异化建议时做上下文)。"""
        notes = _extract_notes(sources.get("competitor"))
        titles: List[str] = []
        for n in notes[:8]:
            t = (n.get("title") or "").strip()
            if t and t != "(无标题)":
                titles.append(t)
        return titles

    @staticmethod
    def _aggregate_seo_keywords(
        sources: Dict[str, Any],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """从竞品笔记的标题 + pain_keywords 聚合 SEO 词频。

        Returns:
            core_keywords: 频次 ≥ _SEO_MIN_COUNT_CORE 的高频词(top _SEO_CORE_TOP)
            long_tail:     频次 < _SEO_MIN_COUNT_CORE 但出现过的长尾词(top _SEO_LONG_TAIL_TOP)
        """
        notes = _extract_notes(sources.get("competitor"))[:_SEO_COMPETITOR_LIMIT]
        if not notes:
            return [], []

        counter: Counter = Counter()
        for note in notes:
            corpus_parts: List[str] = []
            title = (note.get("title") or "").strip()
            if title and title != "(无标题)":
                corpus_parts.append(title)
            pk = (note.get("pain_keywords") or "").strip()
            if pk:
                corpus_parts.append(pk)
            desc = (note.get("desc") or "").strip()
            if desc:
                corpus_parts.append(desc[:200])

            for chunk in corpus_parts:
                for tok in _split_tokens(chunk):
                    t = tok.strip()
                    if not t:
                        continue
                    if not (_SEO_TOKEN_MIN_LEN <= len(t) <= _SEO_TOKEN_MAX_LEN):
                        continue
                    if _is_noise_token(t):
                        continue
                    counter[t] += 1

        if not counter:
            return [], []

        ranked = counter.most_common()
        core: List[Dict[str, Any]] = []
        long_tail: List[Dict[str, Any]] = []
        for word, cnt in ranked:
            entry = {"keyword": word, "count": cnt}
            if cnt >= _SEO_MIN_COUNT_CORE and len(core) < _SEO_CORE_TOP:
                core.append(entry)
            elif len(long_tail) < _SEO_LONG_TAIL_TOP:
                long_tail.append(entry)
            if len(core) >= _SEO_CORE_TOP and len(long_tail) >= _SEO_LONG_TAIL_TOP:
                break
        return core, long_tail

    # ------------------------------------------------------------------
    # LLM 调用(仅用来给 content_direction 总结 + 差异化建议)
    # ------------------------------------------------------------------

    async def _run_llm_summary(
        self,
        *,
        task_id: str,
        viral_matrix: Dict[str, Any],
        pain_top: List[Dict[str, Any]],
        competitor_titles: List[str],
        core_keywords: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        models = viral_matrix.get("models") or []
        if not models and not pain_top and not competitor_titles:
            return {}  # 空输入直接走 fallback

        model_lines: List[str] = []
        for m in models[:6]:
            model_lines.append(
                f"- {m.get('name', '')} "
                f"(coverage={float(m.get('coverage', 0)) * 100:.1f}%, "
                f"avg_interaction={m.get('avg_interaction', 0)})"
            )
        pain_lines = [
            f"- {p['keyword']} ({p['count']})" for p in pain_top
        ] or ["(空)"]
        seo_lines = [
            f"- {s['keyword']} ({s['count']})" for s in core_keywords[:8]
        ] or ["(空)"]
        title_lines = [f"- {t}" for t in competitor_titles] or ["(空)"]

        user_content = (
            "【爆文模型矩阵】\n"
            + ("\n".join(model_lines) if model_lines else "(空)")
            + "\n\n【痛点关键词频次 Top】\n"
            + "\n".join(pain_lines)
            + "\n\n【竞品 SEO 核心词频次】\n"
            + "\n".join(seo_lines)
            + "\n\n【竞品标题样本】\n"
            + "\n".join(title_lines)
            + "\n\n请严格按指定 JSON 格式输出,不要 markdown 代码块。"
        )
        system_prompt = prompt_registry.load("insight_summary.md")

        _messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        _overrides = {
            "temperature": 0.4,
            "max_tokens": 1000,
        }
        for attempt in range(2):
            try:
                text = await self.chat_stream_and_emit(
                    task_id,
                    _messages,
                    overrides=_overrides,
                )
                text = text.strip()
                parsed = extract_json_object(text)
                if parsed:
                    return parsed
                if attempt == 0:
                    await self.emit_log(
                        task_id, "warn", "insight_summary 第 1 次解析失败,重试"
                    )
            except ModelInvocationError as exc:
                await self.emit_log(
                    task_id, "warn", f"insight_summary 模型降级:{exc.code}"
                )
                if attempt == 1:
                    break
            except asyncio.TimeoutError:
                if attempt == 1:
                    await self.emit_log(task_id, "warn", "insight_summary 超时")
        return {}

    # ------------------------------------------------------------------
    # 组装辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _build_content_direction(
        llm_result: Dict[str, Any],
        viral_matrix: Dict[str, Any],
    ) -> Dict[str, Any]:
        cd = llm_result.get("content_direction") or {}
        models = viral_matrix.get("models") or []
        top_from_matrix = (models[0].get("name") if models else "") or ""

        top_direction = str(
            cd.get("top_direction") or top_from_matrix or ""
        ).strip()
        summary_points_raw = cd.get("summary_points") or []
        if not isinstance(summary_points_raw, list):
            summary_points_raw = []
        summary_points = [
            str(p).strip()
            for p in summary_points_raw
            if str(p).strip()
        ][:5]
        highlight = str(cd.get("highlight") or "").strip()

        return {
            "top_direction": top_direction,
            "summary_points": summary_points,
            "highlight": highlight,
        }

    @staticmethod
    def _resolve_stats_axis_label(
        llm_result: Dict[str, Any],
        input_spec: Dict[str, Any],
        *,
        default: str,
    ) -> str:
        """优先级: 任务元数据明确指定 > LLM 建议 > 默认「高频痛点 / 议程」。

        任务元数据里可能有 `advanced_config.stats_axis_label` 或 `parsed.axis_label`,
        这是给未来"用户自定义轴"留的口子,当前版本不会主动写入。
        """
        advanced_cfg = input_spec.get("advanced_config") or {}
        parsed = input_spec.get("parsed") or {}
        explicit = (
            advanced_cfg.get("stats_axis_label")
            or parsed.get("stats_axis_label")
            or parsed.get("axis_label")
        )
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()

        llm_axis = llm_result.get("stats_axis_label")
        if isinstance(llm_axis, str) and llm_axis.strip():
            return llm_axis.strip()
        return default

    async def _run_llm_pain_insight(
        self,
        task_id: str,
        pain_top: List[Dict[str, Any]],
        stats_axis_label: str,
    ) -> None:
        """为每条痛点标签生成「本质定义」和「核心诉求阐释」，原地回填到 pain_top。

        失败时静默降级——pain_top 保持原有 {keyword, count} 结构，Excel J/K 列留空。
        """
        system_prompt = prompt_registry.load("pain_point_insight.md")

        label_lines = [
            f"- {item['keyword']} ({item.get('count', 0)}次)"
            for item in pain_top
            if item.get("keyword")
        ]
        user_content = (
            f"轴名：{stats_axis_label}\n"
            "标签列表：\n"
            + "\n".join(label_lines)
            + "\n\n请先写一句整体观察，再输出 JSON 数组，不要 markdown 代码块。"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        try:
            text = await asyncio.wait_for(
                self.chat_stream_and_emit(
                    task_id,
                    messages,
                    overrides={"temperature": 0.3, "max_tokens": 2000, "timeout": 120},
                ),
                timeout=90,
            )
        except (asyncio.TimeoutError, ModelInvocationError) as exc:
            await self.emit_log(
                task_id, "warn", f"痛点洞察 LLM 超时/降级({type(exc).__name__})，跳过"
            )
            return

        text = text.strip()
        parsed = _extract_pain_insight_array(text)
        if not parsed:
            await self.emit_log(task_id, "warn", "痛点洞察 JSON 解析失败，跳过")
            return

        # 按 keyword 匹配回填
        by_keyword = {item["keyword"]: item for item in pain_top if item.get("keyword")}
        enriched_count = 0
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            kw = entry.get("keyword") or ""
            if kw not in by_keyword:
                continue
            esdef = str(entry.get("essence_definition") or "").strip()
            appeal = str(entry.get("core_appeal") or "").strip()
            if esdef:
                by_keyword[kw]["essence_definition"] = esdef[:120]
            if appeal:
                by_keyword[kw]["core_appeal"] = appeal[:160]
            if esdef or appeal:
                enriched_count += 1

        await self.emit_log(
            task_id, "info", f"痛点洞察回填完成: {enriched_count}/{len(pain_top)} 条"
        )


# ----------------------------------------------------------------------
# 私有工具函数
# ----------------------------------------------------------------------


def _split_tokens(raw: str) -> List[str]:
    """对"逗号 / 斜杠 / 顿号 / 分号 / 竖线 / 空格"等混合分隔符做分词。"""
    if not raw:
        return []
    # 统一成半角逗号再 split
    normalized = (
        raw.replace("/", ",")
        .replace("、", ",")
        .replace(";", ",")
        .replace(";", ",")
        .replace("|", ",")
        .replace("，", ",")
        .replace("\n", ",")
    )
    parts = [p.strip() for p in normalized.split(",")]
    return [p for p in parts if p]


_NOISE_PATTERNS = (
    re.compile(r"^\d+$"),           # 纯数字
    re.compile(r"^[\W_]+$"),        # 纯符号
)
_NOISE_SET = {
    "的", "了", "和", "与", "及", "是", "有", "在", "我", "你", "他",
    "她", "它", "们", "这", "那", "等", "或", "也", "都", "就",
}


def _is_noise_token(token: str) -> bool:
    if token in _NOISE_SET:
        return True
    return any(pat.match(token) for pat in _NOISE_PATTERNS)


def _extract_notes(source_entry: Any) -> List[Dict[str, Any]]:
    """兼容两种形态:扁平 list 或 `{"notes": [...]}` 嵌套。

    4.3pre.2 的 CrawlerAgent `_build_four_source_view` 当前产出扁平 list,
    但 spec §4.1 保留了 `{"query": ..., "notes": [...]}` 这种嵌套结构的
    语义空间,InsightAgent 两种都要吃。
    """
    if isinstance(source_entry, list):
        return [n for n in source_entry if isinstance(n, dict)]
    if isinstance(source_entry, dict):
        inner = source_entry.get("notes")
        if isinstance(inner, list):
            return [n for n in inner if isinstance(n, dict)]
    return []


# 向后兼容导出(prompt 从 4.3pre.2 起就已经是 insight_summary.md)
PROMPT_TEMPLATE = "[deprecated] 使用 insight_summary.md"


def _extract_pain_insight_array(text: str) -> Optional[List[Any]]:
    """从 LLM 输出中提取痛点洞察 JSON 数组（容忍前置摘要文字）。"""
    if not text:
        return None
    # 找到第一个 '[' 开始尝试解析
    for start in range(len(text)):
        if text[start] != "[":
            continue
        try:
            result, _ = json.JSONDecoder().raw_decode(text, start)
            if isinstance(result, list):
                return result
        except (json.JSONDecodeError, ValueError):
            continue
    return None
