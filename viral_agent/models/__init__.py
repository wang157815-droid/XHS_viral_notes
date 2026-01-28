"""
爆文Agent数据模型
"""
from .viral_note import ViralNote, ViralAnalysisResult
from .search_mode import SearchMode, ThresholdConfig
from .keyword_expansion import ExpandedKeyword, KeywordExpansionResult

__all__ = [
    'ViralNote',
    'ViralAnalysisResult',
    'SearchMode',
    'ThresholdConfig',
    'ExpandedKeyword',
    'KeywordExpansionResult',
]