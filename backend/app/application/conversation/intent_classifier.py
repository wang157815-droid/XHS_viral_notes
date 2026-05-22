"""LLM 结构化意图分类器（第二层）。

职责：对规则层无法高置信度判断的输入，调用 LLM 返回结构化 JSON，包含
intent / confidence / slots / missing_fields / clarification_question。

意图空间（6 个）：
  xhs_analysis   — 发起新爆文采集分析任务
  refine_canvas  — 调整当前 Canvas 某模块
  export         — 导出任务结果
  knowledge_qa   — 查询知识库 / SOP / 文档
  general_qa     — 普通问答 / 功能询问 / 概念解释
  unknown        — 无法判断，触发追问
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from loguru import logger

from ...domain.conversation import IntentClassification
from ...llm.model_gateway import model_gateway

_INTENT_SYSTEM_PROMPT = """你是 RedMuse 爆文分析平台的意图识别模块。
你的唯一任务是判断用户消息的真实意图，并抽取必要参数槽位。
禁止思考，直接输出 JSON，不要任何解释或 markdown 代码块。

## 意图定义与触发规则

### xhs_analysis（发起爆文采集分析任务）
触发条件（必须同时满足 A 和 B）：
  A. 有明确的执行动词：帮我 / 搜索 / 采集 / 分析 / 生成 / 跑一下 / 给我 / 找 / 做一个
  B. 有具体的搜索目标词：产品名 / 品牌名 / 品类词（注意："模型""爆文""洞察""爆款"等系统功能词本身不是搜索目标）
禁止触发：
  - 用户在询问系统能力（如"你可以做爆文分析吗""系统支持搜索吗"）
  - 只提到功能词但没有实际产品/品牌/品类词
  - 已有 active_task_id 且未明确说"重新/新建/换一个"

### refine_canvas（调整当前 Canvas 模块）
触发条件：已有 active_task_id，且用户要求修改/优化/展开/重写某个模块或内容
禁止触发：
  - 没有 active_task_id
  - 用户只是在问某个词的意思（如"洞察是什么意思"）

### export（导出任务结果）
触发条件：用户明确要求导出、下载、生成 Excel/JSON 文件

### knowledge_qa（知识库问答）
触发条件：用户询问知识库、SOP、规则文档、案例、合规要求

### general_qa（普通问答）
触发条件：功能询问、概念解释、数据解读、闲聊、追问原因、任何不属于以上意图的问题

### unknown（无法判断）
触发条件：输入太模糊，无法确定意图且置信度 < 0.55

## 配置参数槽位（仅在 xhs_analysis 时抽取）

当用户消息包含以下自然语言描述时，从中抽取对应的 config 槽位：

time_range（时间范围）：
  "最近一天" / "今天" / "24小时内" → "一天内"
  "最近一周" / "最近7天" / "一周内" → "一周内"
  "最近半年" / "半年内" / "6个月内" → "半年内"
  未提及 → null（保持前端默认值）

note_type（笔记类型）：
  "视频" / "视频笔记" / "只要视频" → "视频"
  "图文" / "图文笔记" / "只要图文" → "图文"
  未提及 → null

min_interaction（互动量下限）：
  "互动量大于1000" / "1000以上" / "互动量超过1000" → "1000+"
  "互动量大于5000" / "5000以上" / "5k以上" → "5000+"
  "互动量大于10000" / "万赞以上" / "10000以上" / "1w以上" → "10000+"
  未提及 → null

## few-shot 示例

示例1（xhs_analysis - 明确任务请求）：
输入：帮我搜索格力空调的爆文模型
输出：{"intent":"xhs_analysis","confidence":0.95,"slots":{"keywords":["格力空调"],"competitor_keywords":[],"skip_competitor":false,"time_range":null,"note_type":null,"min_interaction":null},"missing_fields":[],"clarification_question":null,"reason":"明确要求搜索格力空调并生成爆文模型"}

示例2（general_qa - 功能询问，禁止触发任务）：
输入：你可以做爆文分析吗
输出：{"intent":"general_qa","confidence":0.92,"slots":{},"missing_fields":[],"clarification_question":null,"reason":"用户在询问系统是否具备爆文分析功能，而非发起任务"}

示例3（general_qa - 概念解释，不是任务）：
输入：洞察这个模块是什么意思
输出：{"intent":"general_qa","confidence":0.93,"slots":{},"missing_fields":[],"clarification_question":null,"reason":"用户在询问「洞察」词语含义，不是要执行任务"}

示例4（refine_canvas - 有任务时调整模块）：
输入：把痛点模块重新写一遍，更有说服力
输出：{"intent":"refine_canvas","confidence":0.91,"slots":{"instruction":"重新写痛点模块，更有说服力","module_id":null},"missing_fields":[],"clarification_question":null,"reason":"有活跃任务，用户要求重写痛点模块"}

示例5（xhs_analysis - 新建任务替换现有）：
输入：换一个，搜索戴森吹风机
输出：{"intent":"xhs_analysis","confidence":0.90,"slots":{"keywords":["戴森吹风机"],"competitor_keywords":[],"skip_competitor":false,"time_range":null,"note_type":null,"min_interaction":null},"missing_fields":[],"clarification_question":null,"reason":"用户明确要换搜索目标，搜索戴森吹风机"}

示例6（xhs_analysis - 关键词不足，需追问）：
输入：帮我搜索一下
输出：{"intent":"xhs_analysis","confidence":0.72,"slots":{"keywords":[]},"missing_fields":["keywords"],"clarification_question":"请告诉我你想搜索的产品或品牌名称？","reason":"有执行动词但缺少具体搜索目标"}

示例7（general_qa - 解读当前分析结果）：
输入：为什么这个互动数据这么高
输出：{"intent":"general_qa","confidence":0.90,"slots":{},"missing_fields":[],"clarification_question":null,"reason":"用户在追问当前分析结果的原因，属于普通问答"}

示例8（xhs_analysis - 有竞品）：
输入：搜索花西子口红，竞品是完美日记
输出：{"intent":"xhs_analysis","confidence":0.95,"slots":{"keywords":["花西子口红"],"competitor_keywords":["完美日记"],"skip_competitor":false,"time_range":null,"note_type":null,"min_interaction":null},"missing_fields":[],"clarification_question":null,"reason":"明确搜索目标且指定竞品"}

示例9（xhs_analysis - 含时间范围和互动量限制）：
输入：帮我搜集最近一周互动量大于1000的空调爆文笔记
输出：{"intent":"xhs_analysis","confidence":0.95,"slots":{"keywords":["空调"],"competitor_keywords":[],"skip_competitor":false,"time_range":"一周内","note_type":null,"min_interaction":"1000+"},"missing_fields":[],"clarification_question":null,"reason":"明确搜索目标且携带时间范围和互动量约束"}

示例10（xhs_analysis - 只要视频，万赞以上）：
输入：找一下最近半年护肤品的视频爆文，要互动量超过10000的
输出：{"intent":"xhs_analysis","confidence":0.95,"slots":{"keywords":["护肤品"],"competitor_keywords":[],"skip_competitor":false,"time_range":"半年内","note_type":"视频","min_interaction":"10000+"},"missing_fields":[],"clarification_question":null,"reason":"明确搜索目标，指定半年内、视频类型、互动量1万以上"}

## 输出格式（严格 JSON，禁止 markdown 包裹）

{
  "intent": "<意图名>",
  "confidence": <0.0-1.0>,
  "slots": {
    "keywords": [],
    "competitor_keywords": [],
    "skip_competitor": false,
    "module_id": null,
    "instruction": null,
    "time_range": null,
    "note_type": null,
    "min_interaction": null
  },
  "missing_fields": [],
  "clarification_question": null,
  "reason": "<简短理由>"
}"""


class IntentClassifier:
    """LLM 结构化意图分类器。

    调用时机：规则层（IntentRouter）置信度 < 0.90 时由 ConversationService 调用。
    返回完整的 IntentClassification，包含 slots 和 missing_fields。
    """

    async def classify(
        self,
        *,
        content: str,
        active_task_id: Optional[str] = None,
        task_status: Optional[str] = None,
        recent_messages: Optional[List[Dict[str, Any]]] = None,
        canvas_modules: Optional[List[Dict[str, Any]]] = None,
        hint_keywords: Optional[List[str]] = None,
        competitor_keywords: Optional[List[str]] = None,
    ) -> IntentClassification:
        """调用 LLM 对用户消息做结构化意图分类。"""
        context = self._build_context(
            active_task_id=active_task_id,
            task_status=task_status,
            recent_messages=recent_messages or [],
            canvas_modules=canvas_modules or [],
            hint_keywords=hint_keywords or [],
            competitor_keywords=competitor_keywords or [],
        )
        user_prompt = (
            f"当前上下文：\n{json.dumps(context, ensure_ascii=False)}\n\n"
            f"用户消息：{content}\n\n"
            f"输出 JSON："
        )
        try:
            result = await model_gateway.chat(
                "ConversationTitleAgent",  # 复用轻量模型档
                [
                    {"role": "system", "content": _INTENT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                modality="text",
                overrides={
                    "max_tokens": 300,
                    "temperature": 0.0,
                    "extra_body": {
                        "enable_thinking": False,
                        "thinking": {"type": "disabled"},
                    },
                },
            )
            raw = str(result.get("content") or "").strip()
            return self._parse(raw, content)
        except Exception as exc:
            logger.warning("[intent_classifier] LLM 调用失败，降级为 general_qa: {}", exc)
            return IntentClassification(
                intent="general_qa",
                confidence=0.55,
                reason=f"LLM 分类失败降级: {exc}",
            )

    @staticmethod
    def _build_context(
        *,
        active_task_id: Optional[str],
        task_status: Optional[str],
        recent_messages: List[Dict[str, Any]],
        canvas_modules: List[Dict[str, Any]],
        hint_keywords: List[str],
        competitor_keywords: List[str],
    ) -> Dict[str, Any]:
        # 只取最近 3 条消息摘要，避免 token 过多
        recent_summary = [
            {
                "role": m.get("role"),
                "content": str(m.get("content") or "")[:100],
            }
            for m in recent_messages[-3:]
        ]
        # 只传 module_id 列表，不传完整 Canvas 内容
        module_ids = [m.get("module_id") or m.get("moduleId") for m in canvas_modules if m]
        return {
            "has_active_task": bool(active_task_id),
            "task_status": task_status,
            "canvas_module_ids": module_ids,
            "recent_messages": recent_summary,
            "hint_keywords": hint_keywords,
            "competitor_hint": competitor_keywords,
        }

    @staticmethod
    def _parse(raw: str, original_content: str) -> IntentClassification:
        """解析 LLM 返回的 JSON，失败时降级为 general_qa。"""
        # 清除可能的 markdown 代码块包裹
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            data = json.loads(cleaned)
        except Exception:
            # 尝试从文本中提取 JSON 片段
            m = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group())
                except Exception:
                    data = {}
            else:
                data = {}

        valid_intents = {"xhs_analysis", "refine_canvas", "export", "knowledge_qa", "general_qa", "unknown"}
        intent = str(data.get("intent") or "general_qa")
        if intent not in valid_intents:
            intent = "general_qa"

        confidence = float(data.get("confidence") or 0.6)
        confidence = max(0.0, min(1.0, confidence))

        raw_slots = data.get("slots") or {}
        slots: Dict[str, Any] = {
            "keywords": [str(k) for k in (raw_slots.get("keywords") or []) if k],
            "competitor_keywords": [str(k) for k in (raw_slots.get("competitor_keywords") or []) if k],
            "skip_competitor": bool(raw_slots.get("skip_competitor", False)),
            "module_id": raw_slots.get("module_id"),
            "instruction": raw_slots.get("instruction"),
            # 自然语言配置槽位（仅 xhs_analysis 有意义）
            "time_range": raw_slots.get("time_range") or None,
            "note_type": raw_slots.get("note_type") or None,
            "min_interaction": raw_slots.get("min_interaction") or None,
        }

        missing_fields = [str(f) for f in (data.get("missing_fields") or [])]
        clarification_question = data.get("clarification_question") or None

        # 业务校验：xhs_analysis 但关键词为空 → 追问
        if intent == "xhs_analysis" and not slots["keywords"] and "keywords" not in missing_fields:
            missing_fields.append("keywords")
            if not clarification_question:
                clarification_question = "请告诉我你想搜索的产品或品牌名称？"

        clarification_needed = bool(missing_fields and clarification_question)

        # 把槽位中的关键词同步到 extracted_keywords，保持与旧接口兼容
        extracted_keywords = slots["keywords"]
        extracted_competitors = slots["competitor_keywords"]

        logger.info(
            "[intent_classifier] intent={} conf={:.2f} keywords={} reason={}",
            intent, confidence, extracted_keywords,
            str(data.get("reason") or "")[:60],
        )

        return IntentClassification(
            intent=intent,  # type: ignore[arg-type]
            confidence=confidence,
            reason=str(data.get("reason") or ""),
            extracted_keywords=extracted_keywords,
            competitor_keywords=extracted_competitors,
            clarification_needed=clarification_needed,
            clarification_question=clarification_question,
            slots=slots,
            missing_fields=missing_fields,
        )


intent_classifier = IntentClassifier()
