"""
统一知识检索服务

领域知识（JSON 配置）已于 2026-05 下线（B 方案）：本检索器退化为纯 RAG 模式。
仍保留 retrieve_knowledge / get_knowledge_summary / build_ai_prompt_with_knowledge 接口契约以兼容上游调用方，
但 domains / structured_knowledge 字段恒为空、json_domains 永远为 0。
"""
from typing import List, Dict, Any, Optional
from loguru import logger

from viral_agent.services.knowledge.rag_service import RAGService


class UnifiedKnowledgeRetriever:
    """纯 RAG 知识检索器。"""

    def __init__(self, enable_rag: bool = True):
        """初始化检索器：enable_rag=False 仅用于测试。"""
        # 领域知识已下线，json_config 始终为 None
        self.json_config = None

        self.enable_rag = enable_rag
        if enable_rag:
            try:
                self.rag_service = RAGService()
                logger.info("RAG服务初始化成功")
            except Exception as e:
                logger.error(f"RAG服务初始化失败: {e}")
                self.rag_service = None
        else:
            self.rag_service = None
            logger.info("RAG服务已禁用")

    def retrieve_knowledge(
        self,
        title: str = "",
        description: str = "",
        query: Optional[str] = None,
        top_k: int = 5
    ) -> Dict[str, Any]:
        """
        检索相关知识（融合JSON配置和RAG）

        Args:
            title: 笔记标题
            description: 笔记描述
            query: 额外查询（可选）
            top_k: RAG返回结果数

        Returns:
            {
                'structured_knowledge': '结构化知识（JSON）',
                'document_knowledge': ['相关文档片段...'],
                'domains': ['检测到的领域ID'],
                'has_json': bool,
                'has_rag': bool
            }
        """
        result = {
            'structured_knowledge': '',
            'document_knowledge': [],
            'domains': [],
            'has_json': False,
            'has_rag': False,
            'rag_results': []
        }

        # 领域知识已下线，structured_knowledge / domains / has_json 保持默认空值

        # RAG 检索文档知识
        if self.rag_service:
            try:
                # 构建搜索查询
                search_query = query or f"{title} {description}"

                # 向量检索（不再按领域过滤）
                search_results = self.rag_service.search(
                    query=search_query,
                    domains=None,
                    top_k=top_k
                )

                result['rag_results'] = search_results
                result['has_rag'] = bool(search_results)

                # 格式化文档知识
                document_knowledge = []
                for res in search_results:
                    formatted_text = (
                        f"【文档片段】{res.text}\n"
                        f"来源: {res.metadata.get('title', '未知')} "
                        f"(相似度: {res.score:.2%})"
                    )
                    document_knowledge.append(formatted_text)

                result['document_knowledge'] = document_knowledge

                logger.debug(f"RAG检索到 {len(search_results)} 个结果")

            except Exception as e:
                logger.error(f"RAG检索失败: {e}")
                result['document_knowledge'] = []

        return result

    def search_documents(
        self,
        query: str,
        domains: Optional[List[str]] = None,
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """
        直接搜索文档（仅RAG，不涉及JSON配置）

        Args:
            query: 搜索查询
            domains: 领域过滤
            top_k: 返回结果数

        Returns:
            搜索结果列表
        """
        if not self.rag_service:
            logger.warning("RAG服务未初始化")
            return []

        try:
            results = self.rag_service.search(
                query=query,
                domains=domains,
                top_k=top_k
            )

            return [res.to_dict() for res in results]

        except Exception as e:
            logger.error(f"文档搜索失败: {e}")
            return []

    def get_knowledge_summary(self) -> Dict[str, Any]:
        """获取知识库摘要信息（仅 RAG）。"""
        summary = {
            'json_domains': 0,
            'rag_documents': 0,
            'total_knowledge': '0个知识源'
        }

        if self.rag_service:
            try:
                docs = self.rag_service.list_all_documents()
                summary['rag_documents'] = len(docs)
            except Exception as e:
                logger.error(f"获取RAG统计失败: {e}")

        summary['total_knowledge'] = f"{summary['rag_documents']}个知识源"
        return summary

    def build_ai_prompt_with_knowledge(
        self,
        base_prompt: str,
        title: str = "",
        description: str = "",
        query: Optional[str] = None
    ) -> str:
        """
        构建带知识增强的AI Prompt

        Args:
            base_prompt: 基础提示词
            title: 标题
            description: 描述
            query: 查询

        Returns:
            增强后的Prompt
        """
        # 检索知识
        knowledge = self.retrieve_knowledge(
            title=title,
            description=description,
            query=query
        )

        # 构建知识部分
        knowledge_text = ""

        if knowledge['structured_knowledge']:
            knowledge_text += "【结构化知识库（行业规则）】\n"
            knowledge_text += knowledge['structured_knowledge']
            knowledge_text += "\n\n"

        if knowledge['document_knowledge']:
            knowledge_text += "【历史成功案例（RAG检索）】\n"
            knowledge_text += "\n\n".join(knowledge['document_knowledge'])
            knowledge_text += "\n\n"

        # 组合Prompt
        if knowledge_text:
            enhanced_prompt = f"{knowledge_text}\n{'='*50}\n\n{base_prompt}"
        else:
            enhanced_prompt = base_prompt

        return enhanced_prompt
