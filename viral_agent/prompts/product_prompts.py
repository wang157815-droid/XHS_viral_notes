# -*- coding: utf-8 -*-
"""
产品分析专属提示词模块

用于AI增强的产品植入分析，支持知识库注入
分析产品引出时机、植入方式、营销场景等
"""
import json
from typing import Dict, Any, List


# ==================== 系统角色提示词 ====================

PRODUCT_ANALYZER_SYSTEM_PROMPT = """你是一位专业的小红书内容营销分析师。
你擅长分析爆款笔记中的产品植入策略，能够识别：
1. 产品引出的最佳时机和方式
2. 自然植入vs硬广的区别
3. 不同营销场景的适用性
4. 用户心理和购买转化路径

请基于数据进行客观分析，给出可操作的建议。"""


# ==================== 产品植入分析模板 ====================

PRODUCT_ANALYSIS_TEMPLATE = """请分析以下爆款笔记中的产品植入策略：

{knowledge_section}

## 笔记样本（共{sample_count}篇）

{samples_text}

## 已有统计数据

- 标题中提及产品比例: {title_mention_rate}%
- 开头(前1/3)提及比例: {early_mention_rate}%
- 中间(中1/3)提及比例: {middle_mention_rate}%
- 结尾(后1/3)提及比例: {late_mention_rate}%
- 多次提及(3次以上)比例: {multiple_mention_rate}%

## 分析要求

请从以下维度进行深度分析：

### 1. 产品引出时机分析
- 最佳引出位置（开头/中间/结尾）及原因
- 标题是否应该包含产品信息
- 多次提及的节奏把控

### 2. 植入方式分析
- 自然植入：如何让产品出现不突兀
- 痛点切入：如何用问题引出解决方案
- 效果展示：如何用对比/数据增强说服力
- 场景植入：如何在使用场景中自然带出

### 3. 营销场景匹配
- 适合的内容类型（种草/测评/教程/分享等）
- 目标用户画像
- 信任建立方式

### 4. 转化策略
- 如何引导用户产生购买欲望
- 有效的行动召唤（CTA）方式
- 评论区互动策略

## 输出格式

请以JSON格式返回分析结果：

```json
{{
  "timing_analysis": {{
    "optimal_position": "最佳引出位置（开头/中间/结尾）",
    "reasoning": "为什么这个位置最有效",
    "title_strategy": "标题中是否应该提及产品及如何提及",
    "mention_rhythm": "多次提及的节奏建议"
  }},
  "embedding_methods": {{
    "natural_embedding": ["自然植入方法1", "自然植入方法2"],
    "pain_point_approach": ["痛点切入示例1", "痛点切入示例2"],
    "effect_showcase": ["效果展示方法1", "效果展示方法2"],
    "scene_integration": ["场景植入示例1", "场景植入示例2"]
  }},
  "scene_matching": {{
    "best_content_types": ["适合的内容类型"],
    "target_audience": "目标用户画像描述",
    "trust_building": ["建立信任的方式"]
  }},
  "conversion_tactics": {{
    "desire_triggers": ["激发购买欲望的方法"],
    "cta_examples": ["有效的CTA示例"],
    "comment_strategies": ["评论区互动策略"]
  }},
  "key_insights": ["关键洞察1", "关键洞察2", "关键洞察3"],
  "pitfalls_to_avoid": ["需要避免的雷区1", "需要避免的雷区2"]
}}
```
"""


# ==================== 知识库模板 ====================

PRODUCT_KNOWLEDGE_TEMPLATE = """
---
### 【产品植入专业知识】

{domain_name}主题产品分析要点：

【产品引出方式参考】
{intro_ways}

【植入方法参考】
{embed_methods}

【成功案例参考】
{examples}

请结合以上知识进行分析。
---
"""


# ==================== JSON响应格式规范 ====================

PRODUCT_ANALYSIS_SCHEMA = {
    "timing_analysis": {
        "optimal_position": "str",
        "reasoning": "str",
        "title_strategy": "str",
        "mention_rhythm": "str"
    },
    "embedding_methods": {
        "natural_embedding": "List[str]",
        "pain_point_approach": "List[str]",
        "effect_showcase": "List[str]",
        "scene_integration": "List[str]"
    },
    "scene_matching": {
        "best_content_types": "List[str]",
        "target_audience": "str",
        "trust_building": "List[str]"
    },
    "conversion_tactics": {
        "desire_triggers": "List[str]",
        "cta_examples": "List[str]",
        "comment_strategies": "List[str]"
    },
    "key_insights": "List[str]",
    "pitfalls_to_avoid": "List[str]"
}


# ==================== 动态构建函数 ====================

def build_product_analysis_prompt(
    samples: List[Dict[str, Any]],
    timing_stats: Dict[str, float],
    knowledge: Dict[str, Any] = None,
    domain_name: str = ""
) -> str:
    """
    构建产品分析提示词

    Args:
        samples: 笔记样本列表，每个包含title和desc
        timing_stats: 产品引出时机统计数据
        knowledge: 补充知识字典（可选）
        domain_name: 主题名称

    Returns:
        格式化后的提示词
    """
    # 构建知识库部分
    knowledge_section = ""
    if knowledge:
        knowledge_section = PRODUCT_KNOWLEDGE_TEMPLATE.format(
            domain_name=domain_name or "通用",
            intro_ways=knowledge.get('intro_ways', '暂无'),
            embed_methods=knowledge.get('embed_methods', '暂无'),
            examples=knowledge.get('examples', '暂无')
        )

    # 构建样本文本
    samples_text = ""
    for i, sample in enumerate(samples[:10], 1):  # 最多10个样本
        title = sample.get('title', '')
        desc = sample.get('desc', '')[:500]  # 限制描述长度
        samples_text += f"""
### 样本{i}
**标题**: {title}
**内容**: {desc}
"""

    # 构建提示词
    return PRODUCT_ANALYSIS_TEMPLATE.format(
        knowledge_section=knowledge_section,
        sample_count=len(samples),
        samples_text=samples_text,
        title_mention_rate=timing_stats.get('title_mention_rate', 0),
        early_mention_rate=timing_stats.get('early_mention_rate', 0),
        middle_mention_rate=timing_stats.get('middle_mention_rate', 0),
        late_mention_rate=timing_stats.get('late_mention_rate', 0),
        multiple_mention_rate=timing_stats.get('multiple_mention_rate', 0)
    )


def get_system_prompt() -> str:
    """获取系统角色提示词"""
    return PRODUCT_ANALYZER_SYSTEM_PROMPT


def parse_product_analysis_response(response: str) -> Dict[str, Any]:
    """
    解析AI返回的产品分析响应

    支持多种格式：
    1. 纯JSON
    2. ```json 代码块
    3. DeepSeek Reasoner 的 <think>...</think> + JSON 格式

    Args:
        response: AI响应文本

    Returns:
        解析后的字典
    """
    import re

    try:
        # 1. 先移除 DeepSeek Reasoner 的思考过程
        clean_response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)
        clean_response = clean_response.strip()

        # 2. 尝试提取 ```json 代码块
        json_match = re.search(r'```json\s*(.*?)\s*```', clean_response, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1).strip())

        # 3. 尝试提取 ``` 代码块（无语言标记）
        code_match = re.search(r'```\s*(.*?)\s*```', clean_response, re.DOTALL)
        if code_match:
            return json.loads(code_match.group(1).strip())

        # 4. 尝试提取 {...} JSON 对象
        brace_match = re.search(r'\{[\s\S]*\}', clean_response)
        if brace_match:
            return json.loads(brace_match.group())

        # 5. 直接尝试解析整个响应
        return json.loads(clean_response)

    except json.JSONDecodeError:
        return {
            'raw_response': response[:500],
            'parsed': False,
            'error': 'JSON解析失败'
        }


# ==================== 产品植入最佳实践知识库 ====================

PRODUCT_BEST_PRACTICES = {
    "timing": {
        "早期引出": {
            "适用场景": "产品是核心卖点、用户有明确需求",
            "技巧": "开头3句内用痛点/问题引出产品",
            "示例": "黑眼圈困扰我3年！直到遇到了这款眼霜..."
        },
        "中期引出": {
            "适用场景": "需要铺垫信任、产品需要解释",
            "技巧": "先讲故事/教程，中间自然引出",
            "示例": "按照这个手法涂眼霜，效果翻倍！（此处展示产品）"
        },
        "后期引出": {
            "适用场景": "干货类内容、先提供价值再推荐",
            "技巧": "结尾彩蛋式推荐，增加惊喜感",
            "示例": "以上就是我的护肤心得，最后悄悄告诉你这款神器..."
        }
    },
    "methods": {
        "痛点切入": "先描述用户痛点，让用户产生共鸣，再引出产品作为解决方案",
        "效果切入": "用前后对比、数据展示、用户证言等方式证明产品效果",
        "场景切入": "在真实使用场景中自然展示产品，让用户产生代入感",
        "成分切入": "解析产品成分和功效，用专业知识建立信任"
    }
}


def get_best_practices() -> Dict[str, Any]:
    """获取产品植入最佳实践知识"""
    return PRODUCT_BEST_PRACTICES


# ==================== AI语义分析：产品提及位置 ====================

PRODUCT_MENTION_ANALYSIS_PROMPT = """请分析以下小红书笔记中的产品/品牌提及情况。

## 笔记内容

**标题**: {title}

**正文**:
{content}

## 分析要求

请仔细阅读笔记内容，识别其中提及的产品或品牌，并判断首次提及的位置。

注意：
1. 产品提及包括：直接提及品牌名、产品名、"这款"/"它"等指代词
2. 位置判断基于正文内容的相对位置（不含标题）
3. 如果正文中完全没有产品提及，position填"无"

## 输出格式

请严格按以下JSON格式返回（不要添加其他内容）：

```json
{{
  "has_product_mention": true,
  "product_name": "识别到的产品/品牌名（如无法确定填'未明确'）",
  "first_mention_position": "early/middle/late/none",
  "first_mention_text": "首次提及产品的原文片段（约10-20字）",
  "mention_type": "direct/indirect",
  "mention_count": 3,
  "confidence": 0.9
}}
```

字段说明：
- first_mention_position: "early"=前1/3, "middle"=中1/3, "late"=后1/3, "none"=无提及
- mention_type: "direct"=直接提及产品名, "indirect"=使用代词/隐性指代
- confidence: 判断置信度 0-1"""


BATCH_PRODUCT_MENTION_PROMPT = """请分析以下{count}篇小红书笔记中的产品提及情况。

## 笔记列表

{notes_text}

## 分析要求

对每篇笔记，判断产品首次在正文中被提及的位置（前1/3、中1/3、后1/3、无提及）。

注意：
1. 产品提及包括：品牌名、产品名、"这款"/"这个"/"它"等指代产品的词
2. 只分析正文内容，标题中的提及单独统计
3. 位置判断基于字数比例

## 输出格式

请严格按以下JSON格式返回：

```json
{{
  "notes_analysis": [
    {{
      "note_index": 1,
      "title_has_product": true,
      "content_position": "early",
      "product_hint": "识别到的产品关键词",
      "confidence": 0.85
    }},
    {{
      "note_index": 2,
      "title_has_product": false,
      "content_position": "middle",
      "product_hint": "这款眼霜",
      "confidence": 0.9
    }}
  ],
  "summary": {{
    "title_mention_count": 5,
    "early_count": 8,
    "middle_count": 4,
    "late_count": 3,
    "no_mention_count": 0,
    "avg_confidence": 0.87
  }}
}}
```

content_position取值: "early"=前1/3, "middle"=中1/3, "late"=后1/3, "none"=无提及"""


def build_single_note_analysis_prompt(title: str, content: str) -> str:
    """
    构建单篇笔记的产品提及分析提示词

    Args:
        title: 笔记标题
        content: 笔记正文

    Returns:
        格式化后的提示词
    """
    return PRODUCT_MENTION_ANALYSIS_PROMPT.format(
        title=title,
        content=content[:1500]  # 限制长度，避免token过多
    )


def build_batch_analysis_prompt(notes: List[Dict[str, str]]) -> str:
    """
    构建批量笔记的产品提及分析提示词

    Args:
        notes: 笔记列表，每个包含title和desc

    Returns:
        格式化后的提示词
    """
    notes_text = ""
    for i, note in enumerate(notes[:25], 1):  # 最多25篇
        title = note.get('title', '')
        desc = note.get('desc', '')[:600]  # 限制每篇描述长度
        notes_text += f"""
### 笔记{i}
**标题**: {title}
**正文**: {desc}
---
"""

    return BATCH_PRODUCT_MENTION_PROMPT.format(
        count=len(notes),
        notes_text=notes_text
    )


def parse_single_note_response(response: str) -> Dict[str, Any]:
    """
    解析单篇笔记的AI分析响应

    支持 DeepSeek Reasoner 的 <think>...</think> 格式
    """
    import re

    try:
        # 1. 先移除 DeepSeek Reasoner 的思考过程
        clean_response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)
        clean_response = clean_response.strip()

        # 2. 尝试提取 ```json 代码块
        json_match = re.search(r'```json\s*(.*?)\s*```', clean_response, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group(1).strip())
        else:
            # 3. 尝试提取 {...} JSON 对象
            brace_match = re.search(r'\{[\s\S]*\}', clean_response)
            if brace_match:
                result = json.loads(brace_match.group())
            else:
                result = json.loads(clean_response)

        # 验证必需字段
        required_fields = ['has_product_mention', 'first_mention_position']
        for field in required_fields:
            if field not in result:
                result[field] = None

        return result

    except json.JSONDecodeError:
        return {
            'has_product_mention': None,
            'first_mention_position': 'unknown',
            'error': 'JSON解析失败',
            'raw_response': response[:500]
        }


def parse_batch_response(response: str) -> Dict[str, Any]:
    """
    解析批量分析的AI响应

    支持多种格式：
    1. 纯JSON
    2. ```json 代码块
    3. DeepSeek Reasoner 的 <think>...</think> + JSON 格式
    """
    import re

    # 可选导入loguru（测试环境可能没有）
    try:
        from loguru import logger
        has_logger = True
    except ImportError:
        has_logger = False

    def log_debug(msg):
        if has_logger:
            logger.debug(msg)

    def log_error(msg):
        if has_logger:
            logger.error(msg)

    try:
        # 调试日志：记录原始响应的前500字符
        log_debug(f"parse_batch_response 原始响应前500字符: {response[:500]}")

        # 1. 先移除 DeepSeek Reasoner 的思考过程
        clean_response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)
        clean_response = clean_response.strip()

        # 调试日志：检查是否移除了think标签
        if len(clean_response) != len(response):
            log_debug(f"移除think标签后长度变化: {len(response)} -> {len(clean_response)}")

        # 2. 尝试提取 ```json 代码块
        json_match = re.search(r'```json\s*(.*?)\s*```', clean_response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
            log_debug("成功匹配 ```json 代码块")
            return json.loads(json_str.strip())

        # 3. 尝试提取 ``` 代码块（无语言标记）
        code_match = re.search(r'```\s*(.*?)\s*```', clean_response, re.DOTALL)
        if code_match:
            json_str = code_match.group(1)
            log_debug("成功匹配 ``` 代码块")
            return json.loads(json_str.strip())

        # 4. 尝试提取 {...} JSON 对象
        brace_match = re.search(r'\{[\s\S]*\}', clean_response)
        if brace_match:
            log_debug("成功匹配 {...} JSON对象")
            return json.loads(brace_match.group())

        # 5. 直接尝试解析整个响应
        log_debug("尝试直接解析整个响应")
        return json.loads(clean_response)

    except json.JSONDecodeError as e:
        log_error(f"JSON解析失败: {e}")
        log_error(f"清理后响应内容（前800字符）: {clean_response[:800]}")
        return {
            'notes_analysis': [],
            'summary': {},
            'error': 'JSON解析失败',
            'raw_response': response[:500]
        }
