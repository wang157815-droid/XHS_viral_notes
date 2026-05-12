"""
通用知识提示词兼容模块
"""

# ==================== 通用基础知识 ====================

BASE_CATEGORY_KNOWLEDGE = """
【内容类型分类】
大类可能为：
- 单品推荐：重点推荐单个产品
- 干货教程：分享实用技巧或方法
- 剧情：讲故事或情景表演
- vlog/沉浸式：生活记录或沉浸式体验
- 合集：多产品推荐
- 系列推荐：同品牌系列产品推荐
- 测评：产品测试对比

【产品植入方式】
内容植入型：
- 流程中植入产品（在整体流程中自然展示）
- 干货手法中植入（在教学过程中使用）
- 妆教中植入（化妆教程中使用）

直接展示型：
- 手持口播（手持产品直接讲解）
- 使用方法中植入（展示具体使用方法）
"""


# ==================== 兼容接口 ====================

DOMAIN_KEYWORDS = {}
_USE_JSON_CONFIG = False

def detect_domain(title: str = "", description: str = "") -> list:
    return []


def get_domain_knowledge(domains: list) -> str:
    return ""


def build_dynamic_prompt(title: str = "", description: str = "", base_prompt: str = "") -> str:
    return f"""{base_prompt}

【通用知识】
{BASE_CATEGORY_KNOWLEDGE}

请基于通用知识进行分析。
"""


def _get_domain_names(domains: list) -> str:
    return ""
