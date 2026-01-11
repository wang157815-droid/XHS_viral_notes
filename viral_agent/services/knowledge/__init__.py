"""
知识库服务模块

包含RAG知识检索和文档处理功能：
- RAGService: ChromaDB向量检索服务
- DocumentParser: 文档解析（PDF/Word/MD/TXT）
- UnifiedKnowledgeRetriever: 统一知识检索器
"""

from viral_agent.services.knowledge.rag_service import RAGService
from viral_agent.services.knowledge.document_parser import DocumentParser
from viral_agent.services.knowledge.knowledge_retriever import UnifiedKnowledgeRetriever

__all__ = [
    'RAGService',
    'DocumentParser',
    'UnifiedKnowledgeRetriever',
]
