"""
关键词扩展相关数据模型

用于 AI 关键词扩展服务的输入输出数据结构
"""
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime


@dataclass
class ExpandedKeyword:
    """扩展后的关键词"""
    keyword: str                    # 扩展后的关键词
    reason: str                     # 扩展理由
    similarity_score: float         # 与原关键词的语义相似度 (0-1)
    expansion_type: str             # 扩展类型: broader/related/synonym


@dataclass
class KeywordExpansionResult:
    """关键词扩展结果"""
    original_keywords: List[str]                        # 原始关键词
    expanded_keywords: List[ExpandedKeyword] = field(default_factory=list)  # 扩展后的关键词
    expansion_time: str = ""                            # 扩展时间
    ai_model: str = ""                                  # 使用的 AI 模型
    prompt_tokens: int = 0                              # 消耗的 token 数
    completion_tokens: int = 0                          # 生成的 token 数
    success: bool = True                                # 是否成功
    error_message: Optional[str] = None                 # 错误信息

    def __post_init__(self):
        if not self.expansion_time:
            self.expansion_time = datetime.now().isoformat()

    def get_keyword_list(self) -> List[str]:
        """获取扩展后的关键词列表（仅关键词字符串）"""
        return [ek.keyword for ek in self.expanded_keywords]

    def to_dict(self) -> dict:
        """转换为字典（用于 API 响应）"""
        return {
            "original_keywords": self.original_keywords,
            "expanded_keywords": [
                {
                    "keyword": ek.keyword,
                    "reason": ek.reason,
                    "similarity": ek.similarity_score,
                    "type": ek.expansion_type
                }
                for ek in self.expanded_keywords
            ],
            "expansion_time": self.expansion_time,
            "ai_model": self.ai_model,
            "tokens_used": self.prompt_tokens + self.completion_tokens,
            "success": self.success,
            "error": self.error_message
        }
