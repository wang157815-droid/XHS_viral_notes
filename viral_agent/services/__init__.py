"""
爆文Agent服务模块

目录结构：
├── core/       核心分析服务（ViralAnalyzer, ViralNoteCollector等）
├── video/      视频分析服务（VideoEnhancedAnalyzer等）
├── image/      图文分析服务（CoverAnalyzer, MultimodalAnalyzer等）
├── export/     导出服务（ExportService, SynthesisService）
├── knowledge/  知识库服务（RAGService, DocumentParser等）
├── download/   下载管理服务
├── av_sync/    音画同步分析服务
└── cleanup_service.py  系统清理服务
"""

# =============================================================================
# 向后兼容导出：保持原有导入路径可用
# 例如: from viral_agent.services.viral_analyzer import ViralAnalyzer
# =============================================================================

# 核心分析服务
from viral_agent.services.core.viral_collector import ViralNoteCollector
from viral_agent.services.core.feature_extractor import ViralFeatureExtractor
from viral_agent.services.core.viral_analyzer import ViralAnalyzer

# 导出服务
from viral_agent.services.export.export_service import (
    export_to_excel,
    export_raw_data_to_excel
)

# 视频分析服务
from viral_agent.services.video.video_enhanced_analyzer import VideoEnhancedAnalyzer
from viral_agent.services.video.video_ai_analyzer import VideoAIAnalyzer
from viral_agent.services.video.video_timeline_analyzer import VideoTimelineAnalyzer
from viral_agent.services.video.video_analyzer import VideoAnalyzer
from viral_agent.services.video.video_cover_classifier import VideoCoverClassifier
from viral_agent.services.video.video_title_classifier import VideoTitleClassifier

# 图文分析服务
from viral_agent.services.image.cover_analyzer import CoverAnalyzer
from viral_agent.services.image.multimodal_analyzer import MultimodalAnalyzer
from viral_agent.services.image.product_analyzer import ProductAnalyzer
from viral_agent.services.image.scene_analyzer import SceneAnalyzer

# 知识库服务
from viral_agent.services.knowledge.rag_service import RAGService
from viral_agent.services.knowledge.document_parser import DocumentParser
from viral_agent.services.knowledge.knowledge_retriever import UnifiedKnowledgeRetriever

# 清理服务
from viral_agent.services.cleanup_service import CleanupService

__all__ = [
    # 核心服务
    'ViralNoteCollector',
    'ViralFeatureExtractor',
    'ViralAnalyzer',
    # 导出服务
    'export_to_excel',
    'export_raw_data_to_excel',
    # 视频分析
    'VideoEnhancedAnalyzer',
    'VideoAIAnalyzer',
    'VideoTimelineAnalyzer',
    'VideoAnalyzer',
    'VideoCoverClassifier',
    'VideoTitleClassifier',
    # 图文分析
    'CoverAnalyzer',
    'MultimodalAnalyzer',
    'ProductAnalyzer',
    'SceneAnalyzer',
    # 知识库
    'RAGService',
    'DocumentParser',
    'UnifiedKnowledgeRetriever',
    # 清理
    'CleanupService',
]
