"""联网检索工具 web_search：基于 web_search_client.fetch_web_context（Tavily 等）。"""

from __future__ import annotations

from typing import Any, Dict

from loguru import logger

from ....services.web_search_client import fetch_web_context
from ..tool_registry import ToolContext, ToolRegistry, ToolResult, ToolSpec


async def _web_search(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    query = str(args.get("query") or "").strip()
    if not query:
        return ToolResult(ok=False, content="web_search 需要 query 参数", error="MISSING_ARGS")
    max_results = max(1, min(int(args.get("max_results") or 5), 10))
    text = await fetch_web_context(query, max_results=max_results)
    if not text:
        return ToolResult(
            ok=True,
            content=f"未配置联网检索或未检索到「{query}」相关结果（可继续用其它工具或已有信息作答）。",
            display="联网检索无结果/未配置",
        )
    logger.info("[tool.web_search] query={} chars={}", query, len(text))
    return ToolResult(
        ok=True,
        content=f"联网检索「{query}」结果：\n{text}",
        data={"query": query, "text": text},
        display=f"联网检索「{query}」已返回",
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="web_search",
            description="联网检索公开网络信息（行业资讯、热点、品牌背景等小红书站内拿不到的外部信息）。未配置 API Key 时返回空，可降级用其它信息作答。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词或问题"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10, "description": "返回条数，默认 5"},
                },
                "required": ["query"],
            },
            handler=_web_search,
            cost="cheap",
            category="capability",
        )
    )
