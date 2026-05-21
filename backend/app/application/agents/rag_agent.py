"""
RAGAgent：业务约束检索（阶段 4.1 真实化，阶段 4.6 切 pgvector）。

职责定位：
- RAG 的产出是「对下游模型的硬约束」—— 行业通用术语、合规红线、品牌审阅标准等业务规则
- 先用 `rag_query_rewrite.md` 做查询改写，再去 pgvector 检索
- 写 rag_output 分区：
    {
      "query": "...",
      "rewritten_queries": [...],
      "business_rules": [ {"rule_id", "title", "content", "score"} ],
      "hits": [...]   # 向后兼容
    }

阶段 4.1：
- 查询改写：加 response_format + retry
- 真实 pgvector 检索：rewritten_queries 每条调一次 `RAGService.search`，
  按 (doc_id, chunk_index) 去重，按 score 聚合 top-5
- 懒加载 RAGService 实例 + `asyncio.to_thread` 避免阻塞
- 失败或空结果 → 保留 4.0 的占位 business_rules（保证 CanvasRender 不崩）
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from ...domain.task_context import TaskContextWriter
from ...infrastructure.repository import task_repository
from ...llm.model_gateway import ModelInvocationError
from ._json_parsing import extract_json_object
from .base import AgentContext, AgentResult, BaseAgent
from .prompts import prompt_registry


_RAG_SERVICE_SINGLETON: Optional[Any] = None
_RAG_SERVICE_INIT_FAILED = False


def _get_rag_service() -> Optional[Any]:
    """懒加载 RAGService 单例。失败（依赖缺失 / pgvector 异常）后标记不可用，不再重试。"""
    global _RAG_SERVICE_SINGLETON, _RAG_SERVICE_INIT_FAILED
    if _RAG_SERVICE_INIT_FAILED:
        return None
    if _RAG_SERVICE_SINGLETON is not None:
        return _RAG_SERVICE_SINGLETON
    try:
        from viral_agent.services.knowledge.rag_service import RAGService

        _RAG_SERVICE_SINGLETON = RAGService()
        return _RAG_SERVICE_SINGLETON
    except Exception:  # noqa: BLE001 - 依赖/初始化问题统一标记
        _RAG_SERVICE_INIT_FAILED = True
        return None


class RAGAgent(BaseAgent):
    agent_id = "RAGAgent"
    # 4.3pre.3: 画布不再展示"业务约束(知识库)"模块
    # (决策 #1A: RAG 输出是对 LLM 的硬约束,不是面向用户的画布内容)
    # 但 rag_output 分区保留,给 4.4 追加指令/未来规则可视化留口子
    provides: List[str] = []
    depends_on: List[str] = []
    write_partition = "rag_output"

    async def run(self, context: AgentContext) -> AgentResult:
        task_id = context.task_id
        input_spec = context.task_context.get("input_spec") or {}
        parsed = input_spec.get("parsed") or {}
        query_text = (input_spec.get("raw_input") or "").strip()
        keywords: List[str] = list(parsed.get("keywords") or input_spec.get("keywords") or [])

        await self.emit_progress(task_id, "RAG 检索：查询改写 + 业务约束召回", progress=55)

        rewritten_queries = await self._rewrite_queries(task_id, query_text, keywords)
        if not rewritten_queries:
            rewritten_queries = [query_text] if query_text else keywords[:3]

        owner_user_id = self._owner_user_id(task_id)
        business_rules, hits = await self._search_pgvector(task_id, rewritten_queries, owner_user_id)

        if not business_rules:
            business_rules = _fallback_business_rules()
            hits = [
                {
                    "doc_id": br["rule_id"],
                    "title": br["title"],
                    "score": br["score"],
                    "snippet": br["content"][:60],
                }
                for br in business_rules
            ]

        output: Dict[str, Any] = {
            "query": query_text,
            "rewritten_queries": rewritten_queries,
            "business_rules": business_rules,
            "hits": hits,
            "vector_dim": 0,
        }

        TaskContextWriter(context.task_context).write(
            "rag_output", output, agent_id=self.agent_id, note="business_rules_real"
        )
        return AgentResult(ok=True, produced_modules=self.provides, output=output)

    async def _rewrite_queries(
        self,
        task_id: str,
        query_text: str,
        keywords: List[str],
    ) -> List[str]:
        if not (query_text or keywords):
            return []

        rewrite_prompt = prompt_registry.load("rag_query_rewrite.md")
        user_content = (
            f"【原始查询】{query_text or '(空)'}\n"
            f"【关键词】{', '.join(keywords) if keywords else '(空)'}"
        )
        _messages = [
            {"role": "system", "content": rewrite_prompt},
            {"role": "user", "content": user_content},
        ]
        _overrides = {
            "temperature": 0.2,
            "max_tokens": 600,
        }

        for attempt in range(1, 3):
            try:
                text = await self.chat_stream_and_emit(
                    task_id,
                    _messages,
                    overrides=_overrides,
                )
                text = text.strip()
                parsed = extract_json_object(text)
                if parsed:
                    rq = parsed.get("rewritten_queries") or []
                    cleaned = [str(x).strip() for x in rq if str(x).strip()]
                    if cleaned:
                        return cleaned[:3]
                if attempt < 2:
                    await self.emit_log(
                        task_id, "warn", f"RAG 查询改写第 {attempt} 次解析失败,重试"
                    )
            except ModelInvocationError as exc:
                await self.emit_log(
                    task_id, "warn", f"RAG 查询改写第 {attempt} 次降级：{exc.code}"
                )
                if attempt >= 2:
                    break

        # 降级：直接用原始查询
        return [query_text] if query_text else keywords[:3]

    async def _search_pgvector(
        self,
        task_id: str,
        queries: List[str],
        owner_user_id: str,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """多 query 检索 + 按 (doc_id, chunk_index) 去重聚合。"""
        service = _get_rag_service()
        if service is None:
            await self.emit_log(task_id, "info", "RAGService 未就绪（pgvector / deps 不可用），使用占位业务约束")
            return [], []

        async def _one(q: str) -> List[Any]:
            try:
                return await asyncio.to_thread(
                    service.search,
                    q,
                    None,
                    5,
                    0.0,
                    owner_user_id,
                    False,
                )
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                if "dimension" in msg or "vector" in msg:
                    await self.emit_log(
                        task_id,
                        "warn",
                        "RAG 检索失败：pgvector knowledge_chunks 的向量维度与当前 Embedding 模型不一致。"
                        "请重新向量化知识库文档。本次任务将使用占位业务规则兜底。",
                    )
                else:
                    await self.emit_log(
                        task_id, "warn", f"RAG 检索 query='{q[:20]}' 失败：{msg}"
                    )
                return []

        all_results = await asyncio.gather(*[_one(q) for q in queries], return_exceptions=False)

        # 去重 + 聚合：(doc_id, chunk_index) 作 key，保留最高分
        seen: Dict[Tuple[str, int], Dict[str, Any]] = {}
        for batch in all_results:
            for item in batch:
                key = (getattr(item, "doc_id", ""), getattr(item, "chunk_index", 0))
                score = float(getattr(item, "score", 0.0) or 0.0)
                if key not in seen or score > seen[key]["score"]:
                    metadata = getattr(item, "metadata", {}) or {}
                    title = (
                        metadata.get("title")
                        or metadata.get("doc_title")
                        or f"Rule {key[0][:8]}"
                    )
                    seen[key] = {
                        "doc_id": key[0],
                        "chunk_index": key[1],
                        "title": str(title),
                        "text": getattr(item, "text", "") or "",
                        "score": score,
                        "metadata": metadata,
                    }

        if not seen:
            return [], []

        sorted_items = sorted(seen.values(), key=lambda r: r["score"], reverse=True)[:5]

        business_rules: List[Dict[str, Any]] = []
        hits: List[Dict[str, Any]] = []
        for idx, item in enumerate(sorted_items, start=1):
            rule_id = f"kb-{item['doc_id'][:12]}-{item['chunk_index']}"
            text = item["text"]
            business_rules.append(
                {
                    "rule_id": rule_id,
                    "title": item["title"],
                    "content": text,
                    "score": round(item["score"], 4),
                }
            )
            hits.append(
                {
                    "doc_id": item["doc_id"],
                    "title": item["title"],
                    "score": round(item["score"], 4),
                    "snippet": text[:120],
                }
            )

        return business_rules, hits

    @staticmethod
    def _owner_user_id(task_id: str) -> str:
        record = task_repository.get(task_id)
        return record.owner_user_id if record else ""


def _fallback_business_rules() -> List[Dict[str, Any]]:
    return [
        {
            "rule_id": "rule-compliance-01",
            "title": "合规：软广需保留真实体验",
            "content": "软广内容需保留创作者真实体验，避免硬性品牌灌输、绝对化功效宣称。",
            "score": 0.85,
        },
        {
            "rule_id": "rule-terminology-01",
            "title": "行业术语：保留博主行话",
            "content": "保留「手持口播 / 单推手 / 开箱测评 / 种草/拔草」等行业通用说法。",
            "score": 0.80,
        },
    ]
