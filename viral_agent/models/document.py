"""
文档数据模型
定义知识库文档的数据结构
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel, Field


class DocumentMetadata(BaseModel):
    """文档元数据"""
    filename: str = Field(..., description="文件名")
    format: str = Field(..., description="文件格式 (pdf/word/md/txt)")
    word_count: int = Field(default=0, description="字数")
    pages: Optional[int] = Field(None, description="页数（PDF/Word）")
    title: Optional[str] = Field(None, description="文档标题")
    author: Optional[str] = Field(None, description="作者")
    upload_time: str = Field(default_factory=lambda: datetime.now().isoformat(), description="上传时间")
    file_size: Optional[int] = Field(None, description="文件大小（字节）")


class KnowledgeDocument(BaseModel):
    """知识库文档"""
    doc_id: str = Field(..., description="文档唯一ID")
    title: str = Field(..., description="文档标题")
    description: str = Field(default="", description="文档描述")
    domains: List[str] = Field(default_factory=list, description="关联领域ID列表")
    content: str = Field(..., description="文档内容")
    metadata: DocumentMetadata = Field(..., description="文档元数据")
    keywords: List[str] = Field(default_factory=list, description="关键词")
    chunks: List[str] = Field(default_factory=list, description="分块后的文本")
    enabled: bool = Field(default=True, description="是否启用")

    def to_dict(self) -> Dict[str, Any]:
        """转为字典"""
        return {
            'doc_id': self.doc_id,
            'title': self.title,
            'description': self.description,
            'domains': self.domains,
            'content': self.content,
            'metadata': self.metadata.model_dump(),
            'keywords': self.keywords,
            'chunks': self.chunks,
            'enabled': self.enabled
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KnowledgeDocument":
        """从字典创建"""
        metadata_data = data.get('metadata', {})
        metadata = DocumentMetadata(**metadata_data)

        return cls(
            doc_id=data['doc_id'],
            title=data['title'],
            description=data.get('description', ''),
            domains=data.get('domains', []),
            content=data['content'],
            metadata=metadata,
            keywords=data.get('keywords', []),
            chunks=data.get('chunks', []),
            enabled=data.get('enabled', True)
        )


class DocumentSearchResult(BaseModel):
    """文档搜索结果"""
    doc_id: str = Field(..., description="文档ID")
    chunk_index: int = Field(..., description="块索引")
    text: str = Field(..., description="匹配的文本片段")
    score: float = Field(..., description="相似度分数")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")

    def to_dict(self) -> Dict[str, Any]:
        """转为字典"""
        return {
            'doc_id': self.doc_id,
            'chunk_index': self.chunk_index,
            'text': self.text,
            'score': self.score,
            'metadata': self.metadata
        }
