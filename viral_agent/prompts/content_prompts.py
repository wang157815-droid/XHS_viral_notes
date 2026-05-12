# -*- coding: utf-8 -*-
"""
内容分析专属提示词模块

用于AI增强的正文内容分析，支持知识库注入
分析内容结构、写作手法、情感共鸣等
"""
import json
from typing import Dict, Any, List


# ==================== 系统角色提示词 ====================

CONTENT_ANALYZER_SYSTEM_PROMPT = """你是一位专业的小红书内容分析专家。
你擅长分析爆款笔记的内容结构和写作技巧，包括：
1. 开头钩子设计和首句抓力
2. 内容框架和逻辑结构
3. 情感共鸣和信任建立
4. 收尾引导和互动激发

请基于数据分析内容规律，给出可复用的写作建议。"""


# ==================== 内容分析模板 ====================

CONTENT_ANALYSIS_TEMPLATE = """请分析以下爆款笔记的内容规律：

{knowledge_section}

## 内容样本（共{sample_count}篇）

{content_samples}

## 已有统计数据

- 平均内容长度: {avg_length}字
- 结构特征: {structure_patterns}
- 高频词汇: {top_keywords}
- 热门标签: {top_tags}

## 分析要求

### 1. 开头钩子分析
- 常见的开头类型（痛点/悬念/故事/数据等）
- 首句的抓力技巧
- 如何3秒内抓住用户注意力

### 2. 内容结构分析
- 常见的内容框架（总分/递进/对比/故事等）
- 段落组织和信息密度
- 图文配合策略

### 3. 情感共鸣分析
- 语气和口吻特点
- 引发情绪的表达方式
- 真实感和可信度的建立

### 4. 收尾引导分析
- 常见的结尾类型
- 互动引导技巧
- 如何促进收藏/评论

## 输出格式

```json
{{
  "opening_hooks": {{
    "types": [
      {{"type": "钩子类型", "technique": "技巧", "example": "示例"}}
    ],
    "first_sentence_tips": ["首句技巧"],
    "attention_grabbing": ["抓注意力的方法"]
  }},
  "content_structure": {{
    "frameworks": [
      {{"name": "框架名称", "description": "描述", "applicable": "适用场景"}}
    ],
    "paragraph_tips": ["段落组织技巧"],
    "info_density": "信息密度建议"
  }},
  "emotional_resonance": {{
    "tone_suggestions": ["语气建议"],
    "emotional_triggers": ["情绪触发技巧"],
    "trust_building": ["建立信任的方法"]
  }},
  "closing_tactics": {{
    "ending_types": [
      {{"type": "结尾类型", "effect": "效果"}}
    ],
    "interaction_guides": ["互动引导技巧"],
    "collection_triggers": ["促进收藏的方法"]
  }},
  "key_takeaways": ["关键要点1", "关键要点2"]
}}
```
"""


# ==================== 动态构建函数 ====================

def build_content_analysis_prompt(
    contents: List[Dict[str, str]],
    stats: Dict[str, Any],
    knowledge: str = "",
    domain_name: str = ""
) -> str:
    """
    构建内容分析提示词

    Args:
        contents: 内容列表，每个包含title和desc
        stats: 内容统计数据
        knowledge: 补充知识
        domain_name: 主题名称

    Returns:
        格式化后的提示词
    """
    # 构建知识库部分
    knowledge_section = ""
    if knowledge:
        knowledge_section = f"""
---
### 【内容写作专业知识】

{domain_name or '通用'}主题内容要点：

{knowledge}

请结合以上知识进行分析。
---
"""

    # 构建内容样本
    content_samples = ""
    for i, c in enumerate(contents[:8], 1):
        title = c.get('title', '')
        desc = c.get('desc', '')[:300]
        content_samples += f"""
### 样本{i}: {title}
{desc}
"""

    # 格式化统计数据
    top_keywords = [kw.get('word', '') for kw in stats.get('top_keywords', [])]
    top_tags = [tag.get('tag', '') for tag in stats.get('top_tags', [])]

    return CONTENT_ANALYSIS_TEMPLATE.format(
        knowledge_section=knowledge_section,
        sample_count=len(contents),
        content_samples=content_samples,
        avg_length=stats.get('avg_length', 0),
        structure_patterns=json.dumps(
            stats.get('structure_patterns', {}),
            ensure_ascii=False
        ),
        top_keywords=json.dumps(top_keywords[:10], ensure_ascii=False),
        top_tags=json.dumps(top_tags[:5], ensure_ascii=False)
    )


def get_system_prompt() -> str:
    """获取系统角色提示词"""
    return CONTENT_ANALYZER_SYSTEM_PROMPT


def parse_content_analysis_response(response: str) -> Dict[str, Any]:
    """解析AI返回的内容分析响应"""
    import re

    try:
        json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_str = response

        return json.loads(json_str.strip())

    except json.JSONDecodeError:
        return {'raw_response': response, 'parsed': False}


# ==================== 内容写作最佳实践 ====================

CONTENT_BEST_PRACTICES = {
    "opening_hooks": {
        "痛点开头": "直接戳中用户痛点，引发共鸣",
        "悬念开头": "抛出问题或悬念，激发好奇心",
        "故事开头": "用个人经历或故事吸引用户",
        "数据开头": "用惊人数据或效果抓注意力",
        "场景开头": "描绘场景让用户产生代入感"
    },
    "structures": {
        "总分结构": "先总述观点，再分点展开",
        "递进结构": "层层深入，由浅入深",
        "对比结构": "前后对比，正反对比",
        "故事结构": "起因-经过-结果-感悟"
    }
}


def get_content_best_practices() -> Dict[str, Any]:
    """获取内容写作最佳实践"""
    return CONTENT_BEST_PRACTICES
