"""知识库检索工具 search_knowledge：在 RAG 文档库做语义检索（pgvector）。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from loguru import logger

from ..tool_registry import ToolContext, ToolRegistry, ToolResult, ToolSpec


_rag_singleton: Any = None


def _get_rag() -> Any:
    global _rag_singleton
    if _rag_singleton is None:
        from viral_agent.services.knowledge.rag_service import RAGService

        _rag_singleton = RAGService()
    return _rag_singleton


async def _search_knowledge(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    query = str(args.get("query") or "").strip()
    if not query:
        return ToolResult(ok=False, content="search_knowledge 需要 query 参数", error="MISSING_ARGS")
    top_k = max(1, min(int(args.get("top_k") or 5), 10))

    def _do_search() -> List[Any]:
        rag = _get_rag()
        return rag.search(
            query=query,
            top_k=top_k,
            min_score=0.0,
            owner_user_id=ctx.owner_user_id or None,
            include_all=True,
        )

    results = await asyncio.to_thread(_do_search)
    items: List[Dict[str, Any]] = []
    for r in results or []:
        items.append(
            {
                "doc_id": str(getattr(r, "doc_id", "") or ""),
                "chunk_index": int(getattr(r, "chunk_index", 0) or 0),
                "score": round(float(getattr(r, "score", 0) or 0), 4),
                "text": str(getattr(r, "text", "") or "")[:600],
            }
        )
    if not items:
        return ToolResult(ok=True, content=f"知识库未检索到与「{query}」相关的内容。", display="知识库无匹配")
    display = f"知识库检索「{query}」命中 {len(items)} 条片段"
    logger.info("[tool.search_knowledge] {}", display)
    return ToolResult(
        ok=True,
        content=display + "\n" + json.dumps(items, ensure_ascii=False),
        data={"query": query, "results": items},
        display=display,
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="search_knowledge",
            description="在内部 RAG 知识库（SOP/规则/文档/案例）做语义检索，返回带相似度的文本片段。回答合规、方法论、内部规范类问题时使用。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索问题或关键词"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "description": "返回片段数，默认 5"},
                },
                "required": ["query"],
            },
            handler=_search_knowledge,
            cost="cheap",
            category="capability",
        )
    )
