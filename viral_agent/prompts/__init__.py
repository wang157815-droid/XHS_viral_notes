"""
提示词模块
包含爆文分析、视频分析等各类AI提示词模板

模块结构：
- viral_analysis_prompts: 爆文深度分析提示词
- viral_model_prompts: 爆文创作模型提示词
- product_prompts: 产品分析提示词
- title_prompts: 标题分析提示词
- content_prompts: 内容分析提示词
- ocr_prompts: 图片OCR分析提示词
- video_*_prompts: 视频分析相关提示词
"""

# 视频分析提示词
from .video_cover_prompts import VIDEO_COVER_ANALYSIS_PROMPT
from .video_title_prompts import VIDEO_TITLE_ANALYSIS_PROMPT
from .video_timeline_prompts import VIDEO_TIMELINE_BASE_PROMPT

# 爆文分析提示词
from .viral_analysis_prompts import (
    VIRAL_ANALYZER_SYSTEM_PROMPT,
    VIRAL_DEEP_ANALYSIS_TEMPLATE,
    VIRAL_ANALYSIS_JSON_SCHEMA,
    build_viral_analysis_prompt,
    parse_viral_analysis_response,
    VIDEO_METADATA_ANALYSIS_TEMPLATE,
    build_video_metadata_prompt
)

# 爆文创作模型提示词（新增）
from .viral_model_prompts import (
    VIRAL_MODEL_SYSTEM_PROMPT,
    VIRAL_MODEL_TEMPLATE,
    VIRAL_MODEL_JSON_SCHEMA,
    build_viral_model_prompt,
    get_system_prompt as get_viral_model_system_prompt,
    parse_viral_model_response
)

# 产品分析提示词（新增）
from .product_prompts import (
    PRODUCT_ANALYZER_SYSTEM_PROMPT,
    PRODUCT_ANALYSIS_TEMPLATE,
    PRODUCT_ANALYSIS_SCHEMA,
    build_product_analysis_prompt,
    get_system_prompt as get_product_system_prompt,
    parse_product_analysis_response,
    get_best_practices as get_product_best_practices
)

# 标题分析提示词（新增）
from .title_prompts import (
    TITLE_ANALYZER_SYSTEM_PROMPT,
    TITLE_ANALYSIS_TEMPLATE,
    build_title_analysis_prompt,
    get_system_prompt as get_title_system_prompt,
    parse_title_analysis_response,
    get_title_formulas
)

# 内容分析提示词（新增）
from .content_prompts import (
    CONTENT_ANALYZER_SYSTEM_PROMPT,
    CONTENT_ANALYSIS_TEMPLATE,
    build_content_analysis_prompt,
    get_system_prompt as get_content_system_prompt,
    parse_content_analysis_response,
    get_content_best_practices
)

# 图片OCR分析提示词（新增）
from .ocr_prompts import (
    OCR_ANALYZER_SYSTEM_PROMPT,
    OCR_ANALYSIS_TEMPLATE,
    build_ocr_analysis_prompt,
    get_system_prompt as get_ocr_system_prompt,
    parse_ocr_analysis_response,
    get_image_text_practices
)

# 关键词扩展提示词（新增）
from .keyword_expansion_prompts import (
    KEYWORD_EXPANSION_SYSTEM_PROMPT,
    KEYWORD_EXPANSION_USER_TEMPLATE,
    build_keyword_expansion_prompt
)

__all__ = [
    # ==================== 视频分析 ====================
    'VIDEO_COVER_ANALYSIS_PROMPT',
    'VIDEO_TITLE_ANALYSIS_PROMPT',
    'VIDEO_TIMELINE_BASE_PROMPT',

    # ==================== 爆文分析 ====================
    'VIRAL_ANALYZER_SYSTEM_PROMPT',
    'VIRAL_DEEP_ANALYSIS_TEMPLATE',
    'VIRAL_ANALYSIS_JSON_SCHEMA',
    'build_viral_analysis_prompt',
    'parse_viral_analysis_response',
    'VIDEO_METADATA_ANALYSIS_TEMPLATE',
    'build_video_metadata_prompt',

    # ==================== 爆文创作模型 ====================
    'VIRAL_MODEL_SYSTEM_PROMPT',
    'VIRAL_MODEL_TEMPLATE',
    'VIRAL_MODEL_JSON_SCHEMA',
    'build_viral_model_prompt',
    'get_viral_model_system_prompt',
    'parse_viral_model_response',

    # ==================== 产品分析 ====================
    'PRODUCT_ANALYZER_SYSTEM_PROMPT',
    'PRODUCT_ANALYSIS_TEMPLATE',
    'PRODUCT_ANALYSIS_SCHEMA',
    'build_product_analysis_prompt',
    'get_product_system_prompt',
    'parse_product_analysis_response',
    'get_product_best_practices',

    # ==================== 标题分析 ====================
    'TITLE_ANALYZER_SYSTEM_PROMPT',
    'TITLE_ANALYSIS_TEMPLATE',
    'build_title_analysis_prompt',
    'get_title_system_prompt',
    'parse_title_analysis_response',
    'get_title_formulas',

    # ==================== 内容分析 ====================
    'CONTENT_ANALYZER_SYSTEM_PROMPT',
    'CONTENT_ANALYSIS_TEMPLATE',
    'build_content_analysis_prompt',
    'get_content_system_prompt',
    'parse_content_analysis_response',
    'get_content_best_practices',

    # ==================== 图片OCR分析 ====================
    'OCR_ANALYZER_SYSTEM_PROMPT',
    'OCR_ANALYSIS_TEMPLATE',
    'build_ocr_analysis_prompt',
    'get_ocr_system_prompt',
    'parse_ocr_analysis_response',
    'get_image_text_practices',

    # ==================== 关键词扩展 ====================
    'KEYWORD_EXPANSION_SYSTEM_PROMPT',
    'KEYWORD_EXPANSION_USER_TEMPLATE',
    'build_keyword_expansion_prompt',
]
