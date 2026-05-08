"""轻量意图路由。

4.4 MVP 采用规则优先，后续可以在低置信度时接入 LLM 兜底。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ...domain.conversation import IntentClassification


_XHS_KEYWORDS = ("小红书", "爆文", "爆款", "采集", "搜索", "爬取", "生成模型", "爆文模型", "模型矩阵")
_KNOWLEDGE_KEYWORDS = ("知识库", "文档", "资料", "规则", "SOP", "合规", "案例", "手册", "规范")
_EXPORT_KEYWORDS = ("导出", "excel", "Excel", "xlsx", "json", "JSON")
_REFINE_KEYWORDS = (
    "修改",
    "改成",
    "重写",
    "优化",
    "调整",
    "更自然",
    "展开",
    "补充",
    "删掉",
    "增加",
    "加上",
    "减少",
    "弱化",
    "强化",
    "换成",
    "突出",
    "保留",
    "不要",
)
_NEW_ANALYSIS_KEYWORDS = (
    "新建",
    "重新采集",
    "重新搜索",
    "重新跑",
    "重跑",
    "再跑一遍",
    "另做",
    "另起",
    "换一个任务",
    "新的分析",
    "新的爆文模型",
)


class IntentRouter:
    def classify(
        self,
        *,
        content: str,
        conversation_summary: str = "",
        recent_messages: Optional[List[Dict[str, Any]]] = None,
        active_task_id: Optional[str] = None,
        domain_ids: Optional[List[str]] = None,
        hint_keywords: Optional[List[str]] = None,
        competitor_keywords: Optional[List[str]] = None,
    ) -> IntentClassification:
        text = content.strip()
        lowered = text.lower()
        recent_messages = recent_messages or []
        domain_ids = domain_ids or []
        hint_keywords = self._clean_terms(hint_keywords or [])
        competitor_keywords = self._clean_terms(competitor_keywords or [])

        if not text:
            return IntentClassification(
                intent="unknown",
                confidence=0.0,
                reason="empty input",
                clarification_needed=True,
                clarification_question="请补充你想咨询或分析的内容。",
            )

        if self._contains_any(lowered, _EXPORT_KEYWORDS):
            return IntentClassification(
                intent="export",
                confidence=0.82,
                reason="命中导出类关键词",
                domain_ids=domain_ids,
                clarification_needed=not bool(active_task_id),
                clarification_question=None if active_task_id else "当前还没有可导出的分析任务，请先生成爆文模型。",
            )

        target_module_ids = self.extract_target_modules(text)
        wants_new_analysis = bool(
            active_task_id
            and self._contains_any(text, _NEW_ANALYSIS_KEYWORDS)
            and self._contains_any(text, _XHS_KEYWORDS)
        )
        if active_task_id and not wants_new_analysis and (
            self._contains_any(text, _REFINE_KEYWORDS)
            or target_module_ids
            or self._looks_like_canvas_followup(text)
        ):
            return IntentClassification(
                intent="refine_canvas",
                confidence=0.78,
                reason="已有活跃任务且命中 Canvas 调整类关键词",
                target_module_ids=target_module_ids,
                domain_ids=domain_ids,
            )

        if self._contains_any(text, _XHS_KEYWORDS):
            if active_task_id and not self._contains_any(text, _NEW_ANALYSIS_KEYWORDS):
                return IntentClassification(
                    intent="general_qa",
                    confidence=0.68,
                    reason="已有活跃任务，疑似围绕当前 Canvas 的追问，避免误触发新采集任务",
                    target_module_ids=target_module_ids,
                    domain_ids=domain_ids,
                )
            extracted_competitors = competitor_keywords or self.extract_competitor_keywords(text)
            extracted_keywords = hint_keywords or self.extract_keywords(text, extracted_competitors)
            return IntentClassification(
                intent="xhs_analysis",
                confidence=0.86,
                reason="命中小红书/爆文任务类关键词",
                extracted_keywords=extracted_keywords,
                competitor_keywords=extracted_competitors,
                domain_ids=domain_ids,
            )

        if self._contains_any(text, _KNOWLEDGE_KEYWORDS) or domain_ids:
            return IntentClassification(
                intent="knowledge_qa",
                confidence=0.8,
                reason="命中知识库/文档/规则类关键词或显式领域过滤",
                extracted_keywords=hint_keywords or self.extract_keywords(text),
                domain_ids=domain_ids,
                should_retrieve_knowledge=True,
            )

        if len(text) <= 4 and not recent_messages and not conversation_summary:
            return IntentClassification(
                intent="general_qa",
                confidence=0.55,
                reason="短输入，降级为普通问答",
            )

        return IntentClassification(
            intent="general_qa",
            confidence=0.72,
            reason="未命中任务或知识库关键词，按普通问答处理",
            domain_ids=domain_ids,
        )

    @staticmethod
    def extract_keywords(text: str, competitor_keywords: Optional[List[str]] = None) -> List[str]:
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
            "小红书",
            "搜索",
            "采集",
            "生成",
            "爆文",
            "爆款",
            "模型",
            "分析",
            "一下",
            "帮我",
            "知识库",
            "竞品",
            "竞品为",
            "并给出",
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
            (("竞品", "对比"), "mod-competitor-samples-image"),
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
        cleaned = cleaned.strip(" ，,。；;:：\"“”'‘’")
        return cleaned

    @staticmethod
    def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
        return any(keyword.lower() in text.lower() for keyword in keywords)


intent_router = IntentRouter()
