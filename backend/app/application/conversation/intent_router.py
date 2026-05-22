"""轻量意图路由器（规则快速路径，第一层）。

职责：以高置信度（≥ 0.90）快速处理能确定意图的简单情况，减少对 LLM 分类器的调用。
对所有不确定情况返回 confidence < 0.90，由调用方升级到 IntentClassifier（LLM 第二层）处理。

不在本层处理的逻辑（全部交给 LLM 分类器）：
- xhs_analysis：关键词匹配太容易误触发"询问类"句子，必须由 LLM 判断有无行动意图
- general_qa / knowledge_qa 的精确区分
- 任何置信度模糊的输入

本层只触发以下明确意图：
  export       — 明确出现"导出"+"文件格式"等词
  refine_canvas — 有活跃任务 + 明确修改动词 + 画布对象词
  unknown      — 空输入
  （其他全部返回 confidence=0.5，交 LLM 处理）
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ...domain.conversation import IntentClassification

# ─── 高置信度规则关键词 ───────────────────────────────────────────────────────

_EXPORT_TRIGGERS = re.compile(
    r"(?:导出|下载|生成(?:报告|报表)|export)",
    re.IGNORECASE,
)
_EXPORT_FORMAT_HINT = re.compile(
    r"(?:excel|xlsx|json|报告|报表|文件)",
    re.IGNORECASE,
)

# 明确修改画布的动词（必须与画布对象词组合才触发）
_REFINE_VERBS = re.compile(
    r"(?:修改|改成|重写|优化|调整|展开|补充|删掉|增加|加上|减少|弱化|强化|换成|突出|重新生成|重新写|帮我改|帮我写)",
)
_CANVAS_OBJECTS = re.compile(
    r"(?:这个模块|这里|痛点|seo|SEO|关键词|模型矩阵|草稿|竞品|概览|统计|封面|标题|结构|画布|右侧|当前结果)",
)

# 新分析显式触发词（有活跃任务时允许新建）
_NEW_ANALYSIS_EXPLICIT = re.compile(
    r"(?:新建|重新采集|重新搜索|重新跑|重跑|再跑一遍|另做|另起|换一个任务|新的分析)",
)

# ─── 工具方法保留（keyword/module 抽取供 LLM 层调用） ──────────────────────────


class IntentRouter:
    """规则快速路径，只返回 confidence ≥ 0.90 的意图；否则返回 confidence=0.50 让调用方升级到 LLM。"""

    def classify(
        self,
        *,
        content: str,
        conversation_summary: str = "",
        recent_messages: Optional[List[Dict[str, Any]]] = None,
        active_task_id: Optional[str] = None,
        hint_keywords: Optional[List[str]] = None,
        competitor_keywords: Optional[List[str]] = None,
    ) -> IntentClassification:
        text = content.strip()
        recent_messages = recent_messages or []
        hint_keywords = self._clean_terms(hint_keywords or [])
        competitor_keywords = self._clean_terms(competitor_keywords or [])

        # 1. 空输入
        if not text:
            return IntentClassification(
                intent="unknown",
                confidence=0.0,
                reason="empty input",
                clarification_needed=True,
                clarification_question="请补充你想咨询或分析的内容。",
            )

        # 2. 明确导出请求（同时含"导出"词 + 文件格式词）
        if _EXPORT_TRIGGERS.search(text) and (
            _EXPORT_FORMAT_HINT.search(text) or "导出" in text
        ):
            return IntentClassification(
                intent="export",
                confidence=0.90,
                reason="命中明确导出关键词",
                clarification_needed=not bool(active_task_id),
                clarification_question=None if active_task_id else "当前还没有可导出的分析任务，请先生成爆文模型。",
            )

        # 3. 有活跃任务 + 明确修改动词 + 画布对象词 → refine_canvas
        if (
            active_task_id
            and not _NEW_ANALYSIS_EXPLICIT.search(text)
            and _REFINE_VERBS.search(text)
            and _CANVAS_OBJECTS.search(text)
        ):
            target_module_ids = self.extract_target_modules(text)
            return IntentClassification(
                intent="refine_canvas",
                confidence=0.90,
                reason="有活跃任务且明确命中画布修改动词+对象",
                target_module_ids=target_module_ids,
                slots={"instruction": text, "module_id": target_module_ids[0] if target_module_ids else None},
            )

        # 4. 其余情况：置信度不足，交给 LLM 分类器
        return IntentClassification(
            intent="general_qa",
            confidence=0.50,
            reason="规则层无高置信路径，需 LLM 分类",
        )

    @staticmethod
    def extract_keywords(text: str, competitor_keywords: Optional[List[str]] = None) -> List[str]:
        """从文本中提取主品关键词（供 LLM 分类器回填 slots 时备用）。"""
        quoted = re.findall(r"[「\"]([^」\"]{1,30})[」\"]", text)
        if quoted:
            competitors = set(competitor_keywords or [])
            return [
                item
                for item in dict.fromkeys(item.strip() for item in quoted if item.strip())
                if item not in competitors
            ][:5]

        main_match = re.search(
            r"(?:搜索|采集|分析|看看|生成)(?:小红书)?([^，,。；;]+)",
            text,
        )
        if main_match:
            candidate = main_match.group(1)
            candidate = re.split(r"(?:竞品|对比|并|给出|生成|爆文|模型)", candidate, maxsplit=1)[0]
            cleaned = IntentRouter._clean_term(candidate)
            if cleaned:
                return [cleaned]

        candidates = re.findall(r"[\u4e00-\u9fa5A-Za-z0-9][\u4e00-\u9fa5A-Za-z0-9_-]{1,20}", text)
        stop_words = {
            "小红书", "搜索", "采集", "生成", "爆文", "爆款", "模型",
            "分析", "一下", "帮我", "知识库", "竞品", "竞品为", "并给出",
            "洞察", "矩阵", "模型矩阵", "可以", "支持", "功能", "系统",
        }
        result: List[str] = []
        for item in candidates:
            if item in stop_words:
                continue
            if any(item in word or word in item for word in stop_words):
                cleaned = item
                for word in stop_words:
                    cleaned = cleaned.replace(word, "")
                item = cleaned.strip()
            if len(item) >= 2 and item not in result:
                result.append(item)
            if len(result) >= 5:
                break
        return result

    @staticmethod
    def extract_competitor_keywords(text: str) -> List[str]:
        matches = re.findall(
            r"(?:竞品(?:为|是|包括|有|:|：)?|对比)([^，,。；;]+)",
            text,
        )
        result: List[str] = []
        for match in matches:
            for part in re.split(r"[、/和与\s]+", match):
                cleaned = IntentRouter._clean_term(part)
                if cleaned and cleaned not in result:
                    result.append(cleaned)
        return result[:5]

    @staticmethod
    def extract_target_modules(text: str) -> List[str]:
        text = str(text or "")
        mapping: List[tuple[tuple[str, ...], str]] = [
            (("痛点", "需求", "人群", "卖点"), "mod-pain-points"),
            (("seo", "SEO", "关键词", "搜索词", "长尾词", "长尾"), "mod-seo-insights"),
            (("模型矩阵", "爆文模型", "模型", "矩阵", "产品植入", "封面", "标题", "结构"), "mod-viral-model-matrix"),
            (("概览", "统计", "样本量", "数据总览"), "mod-overview-stats"),
            (("草稿", "文案", "正文", "脚本", "写一篇", "生成一篇"), "mod-draft-workbench"),
            (("竞品",), "mod-competitor-samples-image"),
            (("互动", "高互动", "点赞", "收藏", "评论"), "mod-top-interaction-samples-image"),
            (("serp", "SERP", "搜索排名", "搜索结果"), "mod-serp-top-samples-image"),
            (("视频",), "mod-competitor-samples-video"),
            (("图文", "图片"), "mod-competitor-samples-image"),
        ]
        result: List[str] = []
        for keywords, module_id in mapping:
            if any(keyword in text for keyword in keywords) and module_id not in result:
                result.append(module_id)
        return result[:3]

    @staticmethod
    def _looks_like_canvas_followup(text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return False
        starters = ("再", "把", "将", "让", "帮我把", "帮我", "请把", "能不能", "可以")
        objects = ("这个", "这里", "右侧", "画布", "模块", "内容", "结果", "当前")
        return stripped.startswith(starters) and any(item in stripped for item in objects)

    @staticmethod
    def _clean_terms(items: List[str]) -> List[str]:
        result: List[str] = []
        for item in items:
            cleaned = IntentRouter._clean_term(item)
            if cleaned and cleaned not in result:
                result.append(cleaned)
        return result[:5]

    @staticmethod
    def _clean_term(value: str) -> str:
        cleaned = str(value or "").strip()
        cleaned = re.sub(r"^(?:小红书|搜索|采集|分析|帮我|请|给我|看看|关于)", "", cleaned).strip()
        cleaned = re.sub(r"(?:爆文模型|爆文|爆款|模型矩阵|模型|并给出|给出|生成)$", "", cleaned).strip()
        cleaned = cleaned.strip(" ，,。；;:：\"""'''")
        return cleaned

    @staticmethod
    def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
        return any(keyword.lower() in text.lower() for keyword in keywords)


intent_router = IntentRouter()
