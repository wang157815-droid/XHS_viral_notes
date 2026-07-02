"""Conversation tool calling contracts.

模型只负责选择工具和填写参数；所有副作用都必须经过后端校验后执行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


ConversationToolName = Literal[
    "start_xhs_analysis",
    "start_comment_analysis",
    "regenerate_canvas_module",
    "answer_with_knowledge",
    "export_task",
    "answer_general",
    "ask_clarification",
]

ConversationStreamEventType = Literal[
    "status",
    "message_start",
    "message_delta",
    "message_done",
    "message_error",
]


@dataclass
class ConversationToolCall:
    name: ConversationToolName
    arguments: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "arguments": self.arguments,
            "confidence": self.confidence,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConversationToolCall":
        name = str(data.get("name") or data.get("tool_name") or "answer_general")
        if name not in _TOOL_NAMES:
            name = "answer_general"
        args = data.get("arguments") or data.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        return cls(
            name=name,  # type: ignore[arg-type]
            arguments=args,
            confidence=float(data.get("confidence") or 0),
            reason=str(data.get("reason") or ""),
        )


@dataclass
class ConversationToolDecision:
    calls: List[ConversationToolCall] = field(default_factory=list)
    content: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def first_call(self) -> Optional[ConversationToolCall]:
        return self.calls[0] if self.calls else None


@dataclass
class ConversationStreamEvent:
    type: ConversationStreamEventType
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, **self.payload}


_TOOL_NAMES = {
    "start_xhs_analysis",
    "start_comment_analysis",
    "regenerate_canvas_module",
    "answer_with_knowledge",
    "export_task",
    "answer_general",
    "ask_clarification",
}


CONVERSATION_TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "start_comment_analysis",
            "description": "采集指定关键词下的笔记，提取每条笔记中互动量最高的评论并进行分类聚合，生成用户评论洞察报告（Excel）。与爆文模型分析不同，本工具专注于评论内容，不生成 Canvas 和内容矩阵，完成后直接提供下载链接。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 5,
                        "description": "要分析的关键词列表，如['防脱精华']",
                    },
                    "top_notes": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 50,
                        "description": "笔记采集规模参考(非严格上限，实际数量以采集/缓存策略动态决定)。仅当用户明确说出具体数字(如'取前30条笔记')才填写；用户未提及数量时不要填写该字段(表示不限，尽量采集充分样本)。",
                    },
                    "top_comments_per_note": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 20,
                        "description": "每条笔记评论数量参考。当前分析会拉取每条笔记的全部评论(含子评论)，不做条数截断，此字段暂不影响实际采集结果；仅当用户明确要求限定条数才填写。",
                    },
                },
                "required": ["keywords"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_xhs_analysis",
            "description": "创建新的小红书爆文分析任务。只有用户明确要求新建、重新搜索、重新采集或重新生成新的爆文模型时才调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 50},
                    "competitor_keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                    "target_count": {
                        "type": "integer",
                        "minimum": 10,
                        "maximum": 500,
                        "description": "通常不要填写；样本数量来自 advanced_config.sample_count。",
                    },
                    "note_type": {
                        "type": "string",
                        "description": "通常不要填写；笔记类型来自 advanced_config.note_type。",
                    },
                    "time_range": {
                        "type": "string",
                        "description": "通常不要填写；时间范围来自 advanced_config.time_range。",
                    },
                    "confirm_new_task": {"type": "boolean"},
                },
                "required": ["keywords"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "regenerate_canvas_module",
            "description": "基于当前任务 Canvas 局部重生某个模块或段落，不重新跑完整采集分析流程。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "module_id": {"type": "string"},
                    "paragraph_id": {"type": ["string", "null"]},
                    "instruction": {"type": "string"},
                    "cascade": {"type": "boolean"},
                },
                "required": ["module_id", "instruction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "answer_with_knowledge",
            "description": "回答知识库、文档、SOP、规则、合规或案例相关问题，可检索知识库并带引用回答。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_task",
            "description": "导出当前任务的 Excel 或 JSON。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "format": {"type": "string", "enum": ["excel", "json"]},
                },
                "required": ["format"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "answer_general",
            "description": "普通问答或围绕当前 Canvas 的解释，不触发采集、导出或局部重生。",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_clarification",
            "description": "信息不足时向用户追问，保存待补齐的工具和参数。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "missing_fields": {"type": "array", "items": {"type": "string"}},
                    "pending_tool_name": {"type": "string"},
                    "pending_arguments": {"type": "object"},
                },
                "required": ["question", "missing_fields"],
            },
        },
    },
]


def compact_tool_definitions_for_prompt() -> str:
    names = []
    for item in CONVERSATION_TOOL_DEFINITIONS:
        fn = item.get("function") or {}
        names.append(f"- {fn.get('name')}: {fn.get('description')}")
    return "\n".join(names)
