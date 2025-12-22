# -*- coding: utf-8 -*-
"""
爆文创作模型专属提示词模块

用于生成最终的爆文创作指南和模型，支持知识库注入
基于所有分析结果进行综合推理，输出可直接使用的创作建议
"""
import json
from typing import Dict, Any, List, Optional


# ==================== 系统角色提示词 ====================

VIRAL_MODEL_SYSTEM_PROMPT = """你是一位资深的小红书内容运营专家和数据分析师。
你的任务是基于详细的数据分析结果，进行深度思考推理，为每个结论提供有理有据的解释。

要求：
1. 每个结论必须基于提供的实际数据
2. 推理过程要逻辑清晰，说明"为什么这个数据说明了这个结论"
3. 结合小红书平台特性和用户心理进行分析
4. 给出可操作的具体建议
5. 使用专业但易懂的语言

输出格式为JSON，确保可以被解析。"""


# ==================== 爆文模型生成模板 ====================

VIRAL_MODEL_TEMPLATE = """
## 分析数据概览

**关键词**: {keyword}
**爆款笔记数**: {total_notes}篇
**爆款阈值**: {viral_threshold}互动

{knowledge_section}

### 一、标题分析数据
- 平均标题长度: {title_avg_length}字
- 长度分布: {title_length_distribution}
- 高频关键词TOP10: {title_top_keywords}
- 常见模式: {title_common_patterns}

### 二、内容分析数据
- 平均内容长度: {content_avg_length}字
- 内容结构特征: {content_structure_patterns}
- 高频词汇: {content_top_keywords}
- 热门标签: {content_top_tags}

### 三、封面分析数据
- 封面含文字比例: {cover_text_rate}%
- 封面平均文字长度: {cover_avg_text_length}字
- 封面高频词: {cover_top_keywords}
- 真人出镜比例: {cover_people_rate}%
- 产品图比例: {cover_product_rate}%

### 四、产品植入分析数据
- 产品引出时机分布: {product_timing}
- 最佳策略: {product_optimal_strategy}
- TOP营销场景: {product_top_scenes}
- 切入方式分布: {product_approach_methods}

### 五、互动数据
- 平均点赞: {avg_liked}
- 平均收藏: {avg_collected}
- 平均评论: {avg_comment}
- 平均收藏率: {collection_rate}

### 六、AI多模态洞察
{multimodal_summary}

### 七、视频AI洞察
{video_ai_summary}

---

## 请基于以上数据进行综合推理，输出以下JSON格式：

```json
{{
  "title_strategy": {{
    "conclusions": [
      {{
        "point": "结论要点（如：最佳标题长度15-20字）",
        "reasoning": "基于数据的推理（如：数据显示平均标题长度X字，长度分布中Y字占比最高，说明...因为小红书用户...）"
      }}
    ],
    "templates": ["标题模板1", "标题模板2", "标题模板3"],
    "keywords_must_have": ["必含关键词1", "必含关键词2"]
  }},
  "content_strategy": {{
    "conclusions": [
      {{
        "point": "结论要点",
        "reasoning": "推理过程"
      }}
    ],
    "structure_guide": "内容结构建议",
    "hooks": ["开头钩子示例1", "开头钩子示例2"]
  }},
  "cover_strategy": {{
    "conclusions": [
      {{
        "point": "结论要点",
        "reasoning": "推理过程"
      }}
    ],
    "text_guide": "封面文字建议",
    "visual_guide": "视觉设计建议"
  }},
  "product_strategy": {{
    "conclusions": [
      {{
        "point": "结论要点",
        "reasoning": "推理过程"
      }}
    ],
    "timing_guide": "产品引出时机建议",
    "scene_guide": "营销场景建议"
  }},
  "checklist": [
    {{
      "item": "检查项",
      "reason": "为什么重要"
    }}
  ],
  "success_pattern_summary": "综合成功模式总结（200字以内，概括这批爆款的核心成功要素）"
}}
```

请确保：
1. 每个reasoning都要引用具体数据
2. 结论要有可操作性
3. 推理逻辑要清晰完整
"""


# ==================== JSON响应格式规范 ====================

VIRAL_MODEL_JSON_SCHEMA = {
    "title_strategy": {
        "conclusions": "List[{point: str, reasoning: str}]",
        "templates": "List[str]",
        "keywords_must_have": "List[str]"
    },
    "content_strategy": {
        "conclusions": "List[{point: str, reasoning: str}]",
        "structure_guide": "str",
        "hooks": "List[str]"
    },
    "cover_strategy": {
        "conclusions": "List[{point: str, reasoning: str}]",
        "text_guide": "str",
        "visual_guide": "str"
    },
    "product_strategy": {
        "conclusions": "List[{point: str, reasoning: str}]",
        "timing_guide": "str",
        "scene_guide": "str"
    },
    "checklist": "List[{item: str, reason: str}]",
    "success_pattern_summary": "str"
}


# ==================== 知识库模板 ====================

KNOWLEDGE_SECTION_TEMPLATE = """
---
### 【领域专业知识参考】

{domain_name}领域分析要点：

{domain_knowledge}

请结合以上领域知识进行分析，确保建议符合该领域的最佳实践。
---
"""


# ==================== 动态构建函数 ====================

def build_viral_model_prompt(
    summary_data: Dict[str, Any],
    knowledge: str = "",
    domain_name: str = ""
) -> str:
    """
    构建爆文模型生成提示词

    Args:
        summary_data: 包含所有分析数据的摘要字典
        knowledge: 领域知识文本（可选）
        domain_name: 领域名称（可选）

    Returns:
        格式化后的提示词
    """
    # 构建知识库部分
    knowledge_section = ""
    if knowledge:
        knowledge_section = KNOWLEDGE_SECTION_TEMPLATE.format(
            domain_name=domain_name or "通用",
            domain_knowledge=knowledge
        )

    # 提取数据
    title_patterns = summary_data.get('title_patterns', {})
    content_patterns = summary_data.get('content_patterns', {})
    cover_features = summary_data.get('cover_features', {})
    product_features = summary_data.get('product_features', {})
    interaction_features = summary_data.get('interaction_features', {})

    # 格式化标题关键词
    title_keywords = [kw.get('word', '') for kw in title_patterns.get('top_keywords', [])]
    content_keywords = [kw.get('word', '') for kw in content_patterns.get('top_keywords', [])]
    content_tags = [tag.get('tag', '') for tag in content_patterns.get('top_tags', [])]
    cover_keywords = [kw.get('word', '') for kw in cover_features.get('top_keywords', [])]

    # 构建提示词
    return VIRAL_MODEL_TEMPLATE.format(
        keyword=summary_data.get('keyword', ''),
        total_notes=summary_data.get('total_notes', 0),
        viral_threshold=summary_data.get('viral_threshold', 5000),
        knowledge_section=knowledge_section,
        # 标题数据
        title_avg_length=title_patterns.get('avg_length', 0),
        title_length_distribution=json.dumps(
            title_patterns.get('length_distribution', {}),
            ensure_ascii=False
        ),
        title_top_keywords=json.dumps(title_keywords[:10], ensure_ascii=False),
        title_common_patterns=json.dumps(
            title_patterns.get('common_patterns', [])[:5],
            ensure_ascii=False
        ),
        # 内容数据
        content_avg_length=content_patterns.get('avg_length', 0),
        content_structure_patterns=json.dumps(
            content_patterns.get('structure_patterns', {}),
            ensure_ascii=False
        ),
        content_top_keywords=json.dumps(content_keywords[:10], ensure_ascii=False),
        content_top_tags=json.dumps(content_tags[:5], ensure_ascii=False),
        # 封面数据
        cover_text_rate=cover_features.get('text_analysis', {}).get('text_coverage_rate', 0),
        cover_avg_text_length=cover_features.get('text_analysis', {}).get('avg_text_length', 0),
        cover_top_keywords=json.dumps(cover_keywords[:5], ensure_ascii=False),
        cover_people_rate=cover_features.get('visual_analysis', {}).get('people_image_rate', 0),
        cover_product_rate=cover_features.get('visual_analysis', {}).get('product_image_rate', 0),
        # 产品数据
        product_timing=json.dumps(
            product_features.get('timing', {}),
            ensure_ascii=False
        ),
        product_optimal_strategy=product_features.get('optimal_strategy', ''),
        product_top_scenes=json.dumps(
            product_features.get('top_scenes', []),
            ensure_ascii=False
        ),
        product_approach_methods=json.dumps(
            product_features.get('approach_methods', {}),
            ensure_ascii=False
        ),
        # 互动数据
        avg_liked=interaction_features.get('avg_liked', 0),
        avg_collected=interaction_features.get('avg_collected', 0),
        avg_comment=interaction_features.get('avg_comment', 0),
        collection_rate=f"{interaction_features.get('avg_collection_rate', 0) * 100:.1f}%",
        # AI洞察
        multimodal_summary=summary_data.get('multimodal_summary', '多模态分析未完成'),
        video_ai_summary=summary_data.get('video_ai_summary', '视频AI分析未完成')
    )


def get_system_prompt() -> str:
    """
    获取系统角色提示词

    Returns:
        系统提示词文本
    """
    return VIRAL_MODEL_SYSTEM_PROMPT


def parse_viral_model_response(response: str) -> Dict[str, Any]:
    """
    解析AI返回的爆文模型响应

    Args:
        response: AI响应文本

    Returns:
        解析后的字典，包含各策略字段
    """
    import re

    try:
        # 尝试提取JSON部分
        json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_str = response

        # 清理可能的问题字符
        json_str = json_str.strip()
        if json_str.startswith('```'):
            json_str = json_str[3:]
        if json_str.endswith('```'):
            json_str = json_str[:-3]

        return json.loads(json_str)

    except json.JSONDecodeError:
        # 返回原始文本标记
        return {
            'raw_response': response,
            'parsed': False,
            'error': 'JSON解析失败'
        }


# ==================== 示例数据（用于测试和Few-shot） ====================

EXAMPLE_INPUT = {
    "keyword": "眼霜推荐",
    "total_notes": 15,
    "viral_threshold": 5000,
    "title_patterns": {
        "avg_length": 18,
        "top_keywords": [{"word": "眼霜"}, {"word": "眼纹"}],
        "common_patterns": ["数字+效果", "问题+解决方案"]
    }
}

EXAMPLE_OUTPUT = {
    "title_strategy": {
        "conclusions": [
            {
                "point": "最佳标题长度为15-20字",
                "reasoning": "数据显示平均标题长度18字，符合小红书用户快速浏览的阅读习惯"
            }
        ],
        "templates": [
            "【数字】天见效！{产品}让{问题}说拜拜",
            "{问题}救星！{产品}真实测评"
        ],
        "keywords_must_have": ["眼霜", "眼纹", "黑眼圈"]
    }
}
