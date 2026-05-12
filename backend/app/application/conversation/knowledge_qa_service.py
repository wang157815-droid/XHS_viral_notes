"""用户侧知识库问答服务。"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional, Tuple

from loguru import logger

from ...core.security import RoleLevel, role_allows
from ...domain.conversation import IntentClassification, KnowledgeCitation
from ...llm.model_gateway import ModelInvocationError, model_gateway


class KnowledgeQAService:
    def __init__(
        self,
        rag_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._rag_factory = rag_factory

    async def answer(
        self,
        *,
        question: str,
        intent: IntentClassification,
        conversation_summary: str = "",
        current_user: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, List[KnowledgeCitation], Dict[str, Any]]:
        queries = self._rewrite_queries(question, conversation_summary)
        try:
            raw_results = await self._search(queries, current_user=current_user)
        except Exception as exc:
            logger.warning("Knowledge QA retrieval unavailable: {}", exc)
            return (
                "当前知识库检索不可用，我可以先按普通经验回答，但不会伪造知识库引用。请稍后检查 Embedding 或 pgvector 配置后重试。",
                [],
                {"rewrite_queries": queries, "model_error_code": "KNOWLEDGE_RETRIEVAL_UNAVAILABLE"},
            )

        citations = self._to_citations(raw_results)
        if not citations:
            return (
                "我没有在当前知识库中检索到足够相关的内容，因此暂时无法给出带引用的知识库回答。",
                [],
                {"rewrite_queries": queries, "retrieval_summary": {"vector": 0}},
            )

        prompt_messages = self._build_prompt(question, conversation_summary, citations)
        try:
            result = await model_gateway.chat(
                "KnowledgeQAAgent",
                prompt_messages,
                modality="text",
                overrides={"max_tokens": 900, "temperature": 0.2},
            )
            answer = str(result.get("content") or "").strip()
            if not answer:
                answer = "我检索到了相关知识库内容，但本次回答生成为空，请稍后重试。"
            return (
                answer,
                citations,
                {
                    "rewrite_queries": queries,
                    "retrieval_summary": {"vector": len(citations)},
                    "model_profile": result.get("profile_id"),
                },
            )
        except ModelInvocationError as exc:
            logger.warning("Knowledge QA model unavailable: {} {}", exc.code, exc)
            fallback = self._fallback_answer(citations)
            return (
                f"我检索到了相关知识库内容，但大模型服务暂时不可用（{exc.code}）。先给你引用摘要：\n{fallback}",
                citations,
                {
                    "rewrite_queries": queries,
                    "retrieval_summary": {"vector": len(citations)},
                    "model_error_code": exc.code,
                },
            )

    async def prepare_stream_prompt(
        self,
        *,
        question: str,
        intent: IntentClassification,
        conversation_summary: str = "",
        current_user: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[Dict[str, str]], List[KnowledgeCitation], Dict[str, Any]]:
        queries = self._rewrite_queries(question, conversation_summary)
        raw_results = await self._search(queries, current_user=current_user)
        citations = self._to_citations(raw_results)
        if not citations:
            return (
                [
                    {
                        "role": "system",
                        "content": (
                            "你是 RedMuse 工作台助手。用简体中文简洁回答，不要伪造知识库引用。"
                            "回答必须是合法 Markdown，不要输出 HTML；使用 `- ` 列表、`1. ` 步骤、反引号标记字段或路径。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"知识库中没有检索到足够相关的内容。请说明无法给出带引用回答，并给出下一步建议。\n问题：{question}",
                    },
                ],
                [],
                {"rewrite_queries": queries, "retrieval_summary": {"vector": 0}},
            )
        return (
            self._build_prompt(question, conversation_summary, citations),
            citations,
            {"rewrite_queries": queries, "retrieval_summary": {"vector": len(citations)}},
        )

    async def _search(
        self,
        queries: List[str],
        *,
        current_user: Optional[Dict[str, Any]] = None,
    ) -> List[Any]:
        rag = self._create_rag()
        combined: List[Any] = []
        include_all = not current_user or role_allows(current_user.get("role"), RoleLevel.admin)
        owner_user_id = str((current_user or {}).get("user_id") or "")
        for query in queries:
            results = await asyncio.to_thread(
                rag.search,
                query,
                None,
                5,
                0.0,
                owner_user_id,
                include_all,
            )
            combined.extend(results or [])
        return combined

    def _create_rag(self) -> Any:
        if self._rag_factory:
            return self._rag_factory()
        from viral_agent.services.knowledge.rag_service import RAGService

        return RAGService()

    @staticmethod
    def _rewrite_queries(question: str, conversation_summary: str) -> List[str]:
        base = question.strip()
        if conversation_summary:
            return [base, f"{conversation_summary}\n{base}"][:2]
        return [base]

    @staticmethod
    def _to_citations(results: List[Any]) -> List[KnowledgeCitation]:
        seen: set[tuple[str, int]] = set()
        citations: List[KnowledgeCitation] = []
        for result in results:
            doc_id = str(getattr(result, "doc_id", "") or "")
            chunk_index = int(getattr(result, "chunk_index", 0) or 0)
            key = (doc_id, chunk_index)
            if not doc_id or key in seen:
                continue
            seen.add(key)
            metadata = getattr(result, "metadata", {}) or {}
            text = str(getattr(result, "text", "") or "")
            citations.append(
                KnowledgeCitation(
                    doc_id=doc_id,
                    chunk_index=chunk_index,
                    title=str(metadata.get("title") or metadata.get("filename") or doc_id),
                    snippet=text[:500],
                    score=float(getattr(result, "score", 0) or 0),
                    source="vector",
                )
            )
            if len(citations) >= 5:
                break
        return citations

    @staticmethod
    def _build_prompt(
        question: str,
        conversation_summary: str,
        citations: List[KnowledgeCitation],
    ) -> List[Dict[str, str]]:
        context = "\n\n".join(
            f"[{idx + 1}] {item.title} / chunk {item.chunk_index}\n{item.snippet}"
            for idx, item in enumerate(citations)
        )
        summary_part = f"\n会话摘要：{conversation_summary}" if conversation_summary else ""
        return [
            {
                "role": "system",
                "content": (
                    "你是 RedMuse 知识库问答助手。必须基于给定知识片段回答；"
                    "如果片段不足，明确说明不足，不要编造来源。回答使用简体中文。"
                    "回答必须是合法 Markdown，不要输出 HTML；优先使用简短段落和 `- ` 列表；"
                    "需要步骤时使用 `1. ` 编号列表；字段、路径、命令和关键词使用反引号。"
                ),
            },
            {
                "role": "user",
                "content": f"问题：{question}{summary_part}\n\n知识片段：\n{context}",
            },
        ]

    @staticmethod
    def _fallback_answer(citations: List[KnowledgeCitation]) -> str:
        lines = []
        for idx, citation in enumerate(citations, start=1):
            lines.append(f"{idx}. {citation.title}: {citation.snippet[:160]}")
        return "\n".join(lines)
