"""
图文分析服务模块

包含图片和文本联合分析的功能：
- CoverAnalyzer: 封面分析（颜色、布局、OCR）
- AIOCRService: AI 视觉模型 OCR 服务（替代 EasyOCR）
- MultimodalAnalyzer: 图文多模态分析
- ProductAnalyzer: 产品植入分析
- SceneAnalyzer: 营销场景识别
"""

from viral_agent.services.image.cover_analyzer import CoverAnalyzer
from viral_agent.services.image.ai_ocr_service import AIOCRService, get_ai_ocr_service
from viral_agent.services.image.multimodal_analyzer import MultimodalAnalyzer
from viral_agent.services.image.product_analyzer import ProductAnalyzer
from viral_agent.services.image.scene_analyzer import SceneAnalyzer

__all__ = [
    'CoverAnalyzer',
    'AIOCRService',
    'get_ai_ocr_service',
    'MultimodalAnalyzer',
    'ProductAnalyzer',
    'SceneAnalyzer',
]
