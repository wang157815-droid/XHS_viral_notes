# -*- coding: utf-8 -*-
"""
图片OCR分析专属提示词模块

用于AI增强的图片文字分析，支持知识库注入
分析封面文字、图片内文字的规律和策略
"""
import json
from typing import Dict, Any, List


# ==================== 系统角色提示词 ====================

OCR_ANALYZER_SYSTEM_PROMPT = """你是一位专业的小红书视觉内容分析师。
你擅长分析图片中的文字设计和视觉传达策略，包括：
1. 封面文字的设计规律
2. 图片内文字的信息层次
3. 文字与视觉元素的配合
4. 不同场景的文字策略

请基于OCR提取的文字内容，分析成功规律。"""


# ==================== OCR分析模板 ====================

OCR_ANALYSIS_TEMPLATE = """请分析以下爆款笔记图片中的文字规律：

{knowledge_section}

## OCR提取的文字内容（共{sample_count}张图）

{ocr_samples}

## 已有统计数据

- 含文字图片比例: {text_coverage_rate}%
- 平均文字长度: {avg_text_length}字
- 高频词汇: {top_keywords}
- 有效文字密度: {text_density}

## 分析要求

### 1. 封面文字分析
- 封面文字的常见类型（标题重复/关键词/数字/符号等）
- 文字长度和位置规律
- 字体和颜色的使用倾向

### 2. 内容图文字分析
- 教程类图片的文字布局
- 步骤/对比类图片的文字策略
- 信息图的文字层次

### 3. 视觉传达分析
- 文字与图片主体的关系
- 信息突出的视觉技巧
- emoji/符号的使用规律

### 4. 优化建议
- 什么样的图片需要加文字
- 文字量多少合适
- 如何让文字更醒目

## 输出格式

```json
{{
  "cover_text_analysis": {{
    "common_types": ["封面文字类型"],
    "length_tips": "长度建议",
    "position_tips": "位置建议",
    "style_tips": "样式建议"
  }},
  "content_image_text": {{
    "tutorial_layout": "教程类布局建议",
    "comparison_layout": "对比类布局建议",
    "info_hierarchy": "信息层次建议"
  }},
  "visual_communication": {{
    "text_image_relation": "文字与图片关系建议",
    "highlight_techniques": ["突出技巧"],
    "emoji_usage": "符号使用建议"
  }},
  "optimization_tips": {{
    "when_to_add_text": "何时加文字",
    "optimal_amount": "最佳文字量",
    "visibility_tips": ["醒目技巧"]
  }},
  "key_patterns": ["关键规律1", "关键规律2"]
}}
```
"""


# ==================== 动态构建函数 ====================

def build_ocr_analysis_prompt(
    ocr_texts: List[Dict[str, Any]],
    stats: Dict[str, Any],
    knowledge: str = "",
    domain_name: str = ""
) -> str:
    """
    构建OCR分析提示词

    Args:
        ocr_texts: OCR提取的文字列表
        stats: OCR统计数据
        knowledge: 领域知识
        domain_name: 领域名称

    Returns:
        格式化后的提示词
    """
    # 构建知识库部分
    knowledge_section = ""
    if knowledge:
        knowledge_section = f"""
---
### 【图片文字设计知识】

{domain_name or '通用'}领域图片文字要点：

{knowledge}

请结合以上知识进行分析。
---
"""

    # 构建OCR样本
    ocr_samples = ""
    for i, item in enumerate(ocr_texts[:15], 1):
        note_title = item.get('note_title', '未知')[:30]
        texts = item.get('texts', [])
        if texts:
            ocr_samples += f"""
### 图片{i} (笔记: {note_title})
文字内容: {', '.join(texts[:5])}
"""

    # 格式化统计数据
    text_analysis = stats.get('text_analysis', {})
    top_keywords = [kw.get('word', '') for kw in text_analysis.get('top_keywords', [])]

    return OCR_ANALYSIS_TEMPLATE.format(
        knowledge_section=knowledge_section,
        sample_count=len(ocr_texts),
        ocr_samples=ocr_samples,
        text_coverage_rate=text_analysis.get('text_coverage_rate', 0),
        avg_text_length=text_analysis.get('avg_text_length', 0),
        top_keywords=json.dumps(top_keywords[:10], ensure_ascii=False),
        text_density=stats.get('text_density', '适中')
    )


def get_system_prompt() -> str:
    """获取系统角色提示词"""
    return OCR_ANALYZER_SYSTEM_PROMPT


def parse_ocr_analysis_response(response: str) -> Dict[str, Any]:
    """解析AI返回的OCR分析响应"""
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


# ==================== 图片文字最佳实践 ====================

IMAGE_TEXT_BEST_PRACTICES = {
    "cover": {
        "should_include": ["核心卖点", "数字效果", "关键词"],
        "optimal_length": "5-15字",
        "position": "通常在图片上方1/3或中央",
        "style": "醒目字体，与背景形成对比"
    },
    "tutorial": {
        "layout": "步骤编号+简短说明",
        "amount": "每图2-5个文字区块",
        "highlight": "关键步骤用箭头或圈标注"
    },
    "comparison": {
        "layout": "左右/上下对比，明确标注Before/After",
        "text": "突出变化数据或效果词"
    }
}


def get_image_text_practices() -> Dict[str, Any]:
    """获取图片文字最佳实践"""
    return IMAGE_TEXT_BEST_PRACTICES
