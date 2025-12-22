"""
RAG服务 - 基于ChromaDB的向量检索
"""
import os
import json
from typing import List, Dict, Any, Optional
from pathlib import Path
from loguru import logger
from openai import OpenAI
from dotenv import load_dotenv

from viral_agent.models.document import KnowledgeDocument, DocumentSearchResult

# 加载环境变量
load_dotenv()


class RAGService:
    """RAG检索服务"""

    def __init__(
        self,
        persist_directory: str = "viral_agent/storage/chromadb",
        collection_name: str = "knowledge_base"
    ):
        """
        初始化RAG服务

        Args:
            persist_directory: ChromaDB持久化目录
            collection_name: 集合名称
        """
        self.persist_directory = persist_directory
        self.collection_name = collection_name

        # 确保目录存在
        Path(persist_directory).mkdir(parents=True, exist_ok=True)

        # 初始化ChromaDB
        try:
            import chromadb
            from chromadb.config import Settings

            self.client = chromadb.PersistentClient(
                path=persist_directory,
                settings=Settings(anonymized_telemetry=False)
            )

            # 创建或获取集合
            self.collection = self.client.get_or_create_collection(
                name=collection_name,
                metadata={"description": "小红书爆文知识库"}
            )

            logger.info(f"ChromaDB初始化成功: {persist_directory}")

        except ImportError:
            logger.error("ChromaDB未安装，请运行: pip install chromadb")
            raise

        # 初始化Embedding模型
        self._init_embedding_client()

    def _init_embedding_client(self):
        """初始化Embedding客户端"""
        # 优先使用单独配置的Embedding API，如果没有则回退到主模型配置
        api_key = os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY")
        api_base = os.getenv("EMBEDDING_API_BASE") or os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")

        if not api_key:
            logger.warning("未配置EMBEDDING_API_KEY或OPENAI_API_KEY，将使用ChromaDB默认embedding")
            self.embedding_client = None
            self.embedding_model = "default"
            return

        try:
            self.embedding_client = OpenAI(
                api_key=api_key,
                base_url=api_base
            )
            # 使用小模型降低成本
            self.embedding_model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

            # 记录使用的配置
            if os.getenv("EMBEDDING_API_KEY"):
                logger.info(f"使用单独的Embedding API配置")
            else:
                logger.info(f"使用主模型API配置")
            logger.info(f"Embedding模型: {self.embedding_model}")
            logger.info(f"Embedding API Base: {api_base}")
        except Exception as e:
            logger.error(f"Embedding客户端初始化失败: {e}")
            self.embedding_client = None
            self.embedding_model = "default"

    def add_document(
        self,
        doc: KnowledgeDocument,
        auto_chunk: bool = True
    ) -> bool:
        """
        添加文档到向量库

        Args:
            doc: 知识文档对象
            auto_chunk: 是否自动分块

        Returns:
            是否成功
        """
        try:
            # 文本分块
            if auto_chunk and not doc.chunks:
                from viral_agent.services.document_parser import DocumentParser
                parser = DocumentParser()
                doc.chunks = parser.split_text(doc.content, chunk_size=500, overlap=50)

            if not doc.chunks:
                doc.chunks = [doc.content]

            logger.info(f"添加文档: {doc.title}, 分块数: {len(doc.chunks)}")

            # 生成向量
            if self.embedding_client:
                embeddings = self._get_embeddings(doc.chunks)
            else:
                embeddings = None  # 使用ChromaDB默认embedding

            # 存入ChromaDB
            for i, chunk in enumerate(doc.chunks):
                chunk_id = f"{doc.doc_id}_chunk_{i}"

                metadata = {
                    'doc_id': doc.doc_id,
                    'title': doc.title,
                    'chunk_index': i,
                    'domains': json.dumps(doc.domains, ensure_ascii=False),
                    'description': doc.description,
                    'filename': doc.metadata.filename,
                    'format': doc.metadata.format,
                    'upload_time': doc.metadata.upload_time
                }

                if embeddings:
                    self.collection.add(
                        ids=[chunk_id],
                        embeddings=[embeddings[i]],
                        documents=[chunk],
                        metadatas=[metadata]
                    )
                else:
                    # 使用默认embedding
                    self.collection.add(
                        ids=[chunk_id],
                        documents=[chunk],
                        metadatas=[metadata]
                    )

            logger.success(f"文档添加成功: {doc.title}")
            return True

        except Exception as e:
            logger.error(f"添加文档失败 {doc.title}: {e}")
            return False

    def search(
        self,
        query: str,
        domains: Optional[List[str]] = None,
        top_k: int = 5,
        min_score: float = 0.0
    ) -> List[DocumentSearchResult]:
        """
        语义搜索知识库

        Args:
            query: 查询问题
            domains: 限定领域（可选）
            top_k: 返回结果数
            min_score: 最小相似度分数

        Returns:
            搜索结果列表
        """
        try:
            # 生成查询向量
            if self.embedding_client:
                query_embedding = self._get_embeddings([query])[0]
            else:
                query_embedding = None

            # ChromaDB 不支持 $contains，使用后处理过滤
            # 先获取更多结果，再在 Python 中过滤
            fetch_count = top_k * 3 if domains else top_k

            # 向量检索（不使用 where 过滤）
            if query_embedding:
                results = self.collection.query(
                    query_embeddings=[query_embedding],
                    n_results=fetch_count
                )
            else:
                # 使用默认embedding
                results = self.collection.query(
                    query_texts=[query],
                    n_results=fetch_count
                )

            # 格式化并过滤结果
            formatted = self._format_results(results, min_score)

            # 如果指定了领域，在 Python 中过滤
            if domains and formatted:
                filtered = []
                for result in formatted:
                    # 从 metadata 中获取 domains（存储为 JSON 字符串）
                    doc_domains_str = result.metadata.get('domains', '[]')
                    try:
                        doc_domains = json.loads(doc_domains_str)
                    except json.JSONDecodeError:
                        doc_domains = []

                    # 检查是否有交集
                    if any(d in doc_domains for d in domains):
                        filtered.append(result)
                        if len(filtered) >= top_k:
                            break
                return filtered

            return formatted[:top_k]

        except Exception as e:
            logger.error(f"搜索失败: {e}")
            return []

    def delete_document(self, doc_id: str) -> bool:
        """
        删除文档

        Args:
            doc_id: 文档ID

        Returns:
            是否成功
        """
        try:
            # 获取所有相关chunk
            results = self.collection.get(
                where={"doc_id": doc_id}
            )

            if results['ids']:
                self.collection.delete(ids=results['ids'])
                logger.info(f"删除文档: {doc_id}, 删除{len(results['ids'])}个块")
                return True
            else:
                logger.warning(f"文档不存在: {doc_id}")
                return False

        except Exception as e:
            logger.error(f"删除文档失败 {doc_id}: {e}")
            return False

    def list_all_documents(
        self,
        domain_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        列出所有文档

        Args:
            domain_filter: 领域过滤（可选）

        Returns:
            文档列表
        """
        try:
            # 获取所有文档
            where = {"domain": {"$contains": domain_filter}} if domain_filter else None
            results = self.collection.get(where=where, limit=1000)

            # 去重（每个文档有多个chunk，只返回一次）
            docs_map = {}
            for metadata in results['metadatas']:
                doc_id = metadata.get('doc_id')
                if doc_id and doc_id not in docs_map:
                    docs_map[doc_id] = {
                        'doc_id': doc_id,
                        'title': metadata.get('title', ''),
                        'description': metadata.get('description', ''),
                        'domains': json.loads(metadata.get('domains', '[]')),
                        'filename': metadata.get('filename', ''),
                        'format': metadata.get('format', ''),
                        'upload_time': metadata.get('upload_time', '')
                    }

            return list(docs_map.values())

        except Exception as e:
            logger.error(f"列出文档失败: {e}")
            return []

    def _get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """生成文本向量"""
        if not self.embedding_client:
            raise ValueError("Embedding客户端未初始化")

        try:
            response = self.embedding_client.embeddings.create(
                model=self.embedding_model,
                input=texts
            )
            return [item.embedding for item in response.data]
        except Exception as e:
            logger.error(f"生成向量失败: {e}")
            raise

    def _format_results(
        self,
        results: Dict[str, Any],
        min_score: float = 0.0
    ) -> List[DocumentSearchResult]:
        """格式化搜索结果"""
        formatted = []

        if not results['ids'] or not results['ids'][0]:
            return formatted

        for i in range(len(results['ids'][0])):
            # ChromaDB返回的是距离，需要转换为相似度分数
            distance = results['distances'][0][i] if 'distances' in results else 0
            score = 1 - distance  # 距离越小，相似度越高

            if score < min_score:
                continue

            metadata = results['metadatas'][0][i]

            result = DocumentSearchResult(
                doc_id=metadata.get('doc_id', ''),
                chunk_index=metadata.get('chunk_index', 0),
                text=results['documents'][0][i],
                score=round(score, 4),
                metadata=metadata
            )

            formatted.append(result)

        return formatted
