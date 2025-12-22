# -*- coding: utf-8 -*-
"""
标题分析专属提示词模块

用于AI增强的标题分析，支持知识库注入
分析标题公式、触发词、结构模式等
"""
import json
from typing import Dict, Any, List


# ==================== 系统角色提示词 ====================

TITLE_ANALYZER_SYSTEM_PROMPT = """你是一位专业的小红书爆款标题分析师。
你擅长从数据中发现标题的成功规律，包括：
1. 标题长度与互动的关系
2. 爆款标题的常见公式和结构
3. 高效触发词和情绪钩子
4. 不同类型内容的标题策略

请基于数据给出可复用的标题写作建议。"""


# ==================== 标题分析模板 ====================

TITLE_ANALYSIS_TEMPLATE = """请分析以下爆款笔记标题的成功规律：

{knowledge_section}

## 标题样本（共{sample_count}条）

{titles_text}

## 已有统计数据

- 平均标题长度: {avg_length}字
- 长度分布: {length_distribution}
- 高频词TOP10: {top_keywords}
- 已识别模式: {common_patterns}

## 分析要求

### 1. 标题长度策略
- 最佳长度区间及原因
- 不同长度标题的互动表现差异

### 2. 标题结构分析
- 常见的开头结构（数字开头/疑问开头/痛点开头等）
- 常见的结尾结构（感叹/省略/呼吁等）
- 标点符号的使用规律

### 3. 触发词分析
- 高效情绪触发词（惊喜/焦虑/好奇等）
- 行业热词和流行用语
- 符号emoji的使用技巧

### 4. 爆款公式提炼
- 至少提炼3个可复用的标题公式
- 每个公式配上示例

## 输出格式

```json
{{
  "length_strategy": {{
    "optimal_range": "最佳长度区间",
    "reasoning": "为什么这个长度最有效"
  }},
  "structure_patterns": {{
    "opening_types": [
      {{"type": "开头类型", "example": "示例", "effect": "效果"}}
    ],
    "ending_types": [
      {{"type": "结尾类型", "example": "示例", "effect": "效果"}}
    ],
    "punctuation_tips": ["标点使用技巧"]
  }},
  "trigger_words": {{
    "emotional_triggers": ["情绪触发词"],
    "hot_words": ["行业热词"],
    "emoji_tips": ["符号使用技巧"]
  }},
  "title_formulas": [
    {{
      "formula": "公式描述（如：数字+效果+产品）",
      "example": "具体示例",
      "applicable_scene": "适用场景"
    }}
  ],
  "dos_and_donts": {{
    "dos": ["应该做的"],
    "donts": ["应该避免的"]
  }}
}}
```
"""


# ==================== 知识库模板 ====================

TITLE_KNOWLEDGE_TEMPLATE = """
---
### 【标题写作专业知识】

{domain_name}领域标题要点：

【高效标题公式】
{formulas}

【行业热门关键词】
{hot_keywords}

【成功标题案例】
{examples}

请结合以上知识进行分析。
---
"""


# ==================== 动态构建函数 ====================

def build_title_analysis_prompt(
    titles: List[str],
    stats: Dict[str, Any],
    knowledge: Dict[str, Any] = None,
    domain_name: str = ""
) -> str:
    """
    构建标题分析提示词

    Args:
        titles: 标题列表
        stats: 标题统计数据
        knowledge: 领域知识（可选）
        domain_name: 领域名称

    Returns:
        格式化后的提示词
    """
    # 构建知识库部分
    knowledge_section = ""
    if knowledge:
        knowledge_section = TITLE_KNOWLEDGE_TEMPLATE.format(
            domain_name=domain_name or "通用",
            formulas=knowledge.get('formulas', '暂无'),
            hot_keywords=knowledge.get('hot_keywords', '暂无'),
            examples=knowledge.get('examples', '暂无')
        )

    # 构建标题文本
    titles_text = "\n".join([f"- {t}" for t in titles[:20]])

    # 格式化关键词
    top_keywords = [kw.get('word', '') for kw in stats.get('top_keywords', [])]

    return TITLE_ANALYSIS_TEMPLATE.format(
        knowledge_section=knowledge_section,
        sample_count=len(titles),
        titles_text=titles_text,
        avg_length=stats.get('avg_length', 0),
        length_distribution=json.dumps(
            stats.get('length_distribution', {}),
            ensure_ascii=False
        ),
        top_keywords=json.dumps(top_keywords[:10], ensure_ascii=False),
        common_patterns=json.dumps(
            stats.get('common_patterns', []),
            ensure_ascii=False
        )
    )


def get_system_prompt() -> str:
    """获取系统角色提示词"""
    return TITLE_ANALYZER_SYSTEM_PROMPT


def parse_title_analysis_response(response: str) -> Dict[str, Any]:
    """解析AI返回的标题分析响应"""
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


# ==================== 标题公式知识库 ====================

TITLE_FORMULAS = {
    "数字公式": {
        "pattern": "数字 + 效果/好处",
        "examples": [
            "3步搞定完美底妆",
            "7天见效！眼纹淡了80%",
            "收藏！10个护肤冷知识"
        ]
    },
    "对比公式": {
        "pattern": "前后对比 / 正反对比",
        "examples": [
            "换了这个眼霜后，眼纹真的淡了",
            "千万别这样涂眼霜！正确vs错误手法"
        ]
    },
    "疑问公式": {
        "pattern": "疑问 + 答案暗示",
        "examples": [
            "为什么你的眼霜总是没效果？",
            "眼纹怎么消除？试试这个方法"
        ]
    },
    "情绪公式": {
        "pattern": "情绪词 + 产品/效果",
        "examples": [
            "绝了！这眼霜真的有效",
            "后悔没早点知道这个护眼方法"
        ]
    }
}


def get_title_formulas() -> Dict[str, Any]:
    """获取标题公式知识库"""
    return TITLE_FORMULAS
