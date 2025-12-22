"""
爆文分析提示词模块
用于小红书爆款笔记深度分析，支持模板化生成
"""
import json
from typing import Dict, Any, List


# 系统角色提示词
VIRAL_ANALYZER_SYSTEM_PROMPT = "你是小红书爆款内容分析专家，擅长从爆款笔记中提取可复用的创作模式和策略。"


# 深度分析提示词模板
VIRAL_DEEP_ANALYSIS_TEMPLATE = """你是小红书爆款内容分析专家。请对以下关于"{keyword}"的爆款图文笔记进行深度分析。

{knowledge_section}【样本笔记】（已提供完整标题和正文）
{samples_json}

【整体统计特征】
- 平均标题长度：{avg_title_length}字
- 平均内容长度：{avg_content_length}字
- 标题高频词：{title_keywords}
- 内容高频词：{content_keywords}
- 平均点赞数：{avg_liked}
- 平均收藏数：{avg_collected}
- 收藏率：{collection_rate}

【深度分析要求】

请基于完整文本内容，从以下维度进行深度分析：

1. 【语义理解】标题策略
   - 标题的语义层次分析（表层吸引 vs 深层价值传递）
   - 关键触发词的心理作用（如"终于""真香""避坑"）
   - 标题与正文的呼应关系
   - 3-5个可复用的标题公式（带示例）

2. 【内容结构】正文框架
   - 开头的钩子设计（痛点/悬念/故事/数据）
   - 主体内容的组织逻辑（列表/对比/步骤/场景）
   - 收尾的行动引导策略
   - 用户痛点与需求的深层洞察

3. 【情感共鸣】表达方式
   - 语气和口吻的特点（亲切感/专业感/真诚感）
   - 引发情绪的关键表达（惊喜/焦虑/认同/好奇）
   - 互动话术的设计（提问/邀请/引导评论）
   - 建立信任的元素（真实体验/数据支撑/对比实验）

4. 【价值传递】核心卖点
   - 每篇笔记的核心价值主张是什么
   - 实用价值 vs 情感价值的平衡
   - 信息密度与可读性的平衡
   - 收藏转化的关键要素

5. 【差异化策略】创新角度
   - 如何在同质化内容中脱颖而出
   - 独特切入点和创新角度
   - 个人IP特色的塑造建议

6. 【实操建议】创作指南
   - 针对该关键词的最佳创作路径
   - 避免的雷区和常见错误
   - 提升互动率的具体技巧

请以JSON格式返回分析结果，结构如下：
{{
  "title_strategy": {{
    "semantic_analysis": "语义分析",
    "trigger_words": ["关键触发词列表"],
    "title_formulas": ["公式1", "公式2", "公式3"]
  }},
  "content_framework": {{
    "opening_hooks": ["开头钩子类型"],
    "structure_patterns": ["结构模式"],
    "pain_points": ["用户痛点列表"],
    "value_propositions": ["核心价值主张"]
  }},
  "emotional_resonance": {{
    "tone_characteristics": "语气特点",
    "emotional_triggers": ["情绪触发点"],
    "trust_building": ["建立信任的方式"]
  }},
  "differentiation": {{
    "unique_angles": ["独特角度"],
    "innovation_suggestions": ["创新建议"]
  }},
  "actionable_tips": {{
    "best_practices": ["最佳实践"],
    "pitfalls_to_avoid": ["需要避免的雷区"],
    "engagement_tactics": ["提升互动的战术"]
  }}
}}
"""


# JSON响应格式规范
VIRAL_ANALYSIS_JSON_SCHEMA = {
    "title_strategy": {
        "semantic_analysis": "str",
        "trigger_words": "List[str]",
        "title_formulas": "List[str]"
    },
    "content_framework": {
        "opening_hooks": "List[str]",
        "structure_patterns": "List[str]",
        "pain_points": "List[str]",
        "value_propositions": "List[str]"
    },
    "emotional_resonance": {
        "tone_characteristics": "str",
        "emotional_triggers": "List[str]",
        "trust_building": "List[str]"
    },
    "differentiation": {
        "unique_angles": "List[str]",
        "innovation_suggestions": "List[str]"
    },
    "actionable_tips": {
        "best_practices": "List[str]",
        "pitfalls_to_avoid": "List[str]",
        "engagement_tactics": "List[str]"
    }
}


def build_viral_analysis_prompt(
    keyword: str,
    samples: List[Dict[str, Any]],
    features: Dict[str, Any],
    knowledge_section: str = ""
) -> str:
    """
    构建爆文深度分析提示词

    Args:
        keyword: 搜索关键词
        samples: 样本笔记列表
        features: 统计特征字典
        knowledge_section: 领域知识（可选）

    Returns:
        格式化后的提示词
    """
    # 提取特征数据
    title_features = features.get('title_features', {})
    content_features = features.get('content_features', {})
    interaction_features = features.get('interaction_features', {})

    # 处理高频词
    title_keywords = ', '.join([
        k['word'] for k in title_features.get('top_keywords', [])[:10]
    ])
    content_keywords = ', '.join([
        k['word'] for k in content_features.get('top_keywords', [])[:10]
    ])

    # 格式化知识部分
    if knowledge_section:
        knowledge_section = knowledge_section + "=" * 50 + "\n\n"

    return VIRAL_DEEP_ANALYSIS_TEMPLATE.format(
        keyword=keyword,
        knowledge_section=knowledge_section,
        samples_json=json.dumps(samples, ensure_ascii=False, indent=2),
        avg_title_length=title_features.get('avg_length', 0),
        avg_content_length=content_features.get('avg_length', 0),
        title_keywords=title_keywords,
        content_keywords=content_keywords,
        avg_liked=interaction_features.get('avg_liked', 0),
        avg_collected=interaction_features.get('avg_collected', 0),
        collection_rate=interaction_features.get('avg_collection_rate', 0)
    )


def parse_viral_analysis_response(response: str) -> Dict[str, Any]:
    """
    解析AI分析响应

    Args:
        response: AI响应文本

    Returns:
        解析后的字典
    """
    try:
        # 尝试提取JSON部分
        if '```json' in response:
            json_str = response.split('```json')[1].split('```')[0]
        elif '```' in response:
            json_str = response.split('```')[1].split('```')[0]
        else:
            json_str = response

        return json.loads(json_str)

    except json.JSONDecodeError:
        return {
            'raw_insights': response,
            'parsed': False
        }


# 视频分析元数据提示词
VIDEO_METADATA_ANALYSIS_TEMPLATE = """{base_prompt}

视频信息：
- URL: {video_url}
- 标题: {title}
- 描述: {description}

请基于标题和描述推断视频内容，并按要求进行分析。
如果信息不足，请使用"/"标记无法判断的部分。
"""


def build_video_metadata_prompt(
    base_prompt: str,
    video_url: str,
    title: str = None,
    description: str = None
) -> str:
    """
    构建视频元数据分析提示词

    Args:
        base_prompt: 基础分析提示词
        video_url: 视频URL
        title: 视频标题
        description: 视频描述

    Returns:
        格式化后的提示词
    """
    return VIDEO_METADATA_ANALYSIS_TEMPLATE.format(
        base_prompt=base_prompt,
        video_url=video_url,
        title=title if title else '无',
        description=description if description else '无'
    )
