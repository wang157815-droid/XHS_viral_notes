"""
知识库配置模块
支持从JSON文件动态加载和管理知识库配置
"""

from .knowledge_loader import (
    KnowledgeBaseConfig,
    get_knowledge_config,
    reload_knowledge_config
)

__all__ = [
    'KnowledgeBaseConfig',
    'get_knowledge_config',
    'reload_knowledge_config'
]
