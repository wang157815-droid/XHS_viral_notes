"""
视频综合推理提示词
用于整合所有视频分析维度，生成视频爆文模型
对标图文笔记的综合推理服务（SynthesisService）
"""

from typing import Dict, Any, List, Optional
import json

# 从解析器模块导入（保持向后兼容）
from viral_agent.prompts.video_synthesis_prompts_parser import (
    parse_synthesis_result,
    extract_key_recommendations,
    parse_quick_synthesis,
    parse_checklist
)


# 视频爆文模型综合推理提示词
VIDEO_SYNTHESIS_PROMPT = """您是一位资深短视频运营专家，专精于小红书爆款视频的规律总结和创作指导。
现在我将为您提供一批爆款视频的分析数据，请您基于这些数据，综合推理并生成一套完整的视频爆文模型。

## 输入数据说明

您将收到以下几类分析数据：

1. **封面分析统计**：封面类型分布、视觉特征等
2. **标题分析统计**：标题类型分布、关键词特征等
3. **时间轴分析统计**：产品出现时间、干货时间等分布
4. **内容质量分析统计**：开场钩子、结构、情感、收尾等
5. **产品分析统计**：营销场景、植入方式、CTA类型等

## 推理要求

请基于数据，从以下6个维度生成视频爆文模型：

### 1. 开场钩子策略 (hook_strategy)
- 最有效的钩子类型（基于数据）
- 推荐的开场时长（前几秒）
- 钩子设计要点（3-5条）
- 避免的开场方式

### 2. 内容结构策略 (structure_strategy)
- 最优的内容结构类型
- 推荐的节奏把控
- 内容时间分配建议（如：开场-主体-收尾的时间比例）
- 转场技巧建议

### 3. 时间节奏策略 (timing_strategy)
- 产品出现的最佳时机（基于数据统计）
- 干货内容开始的最佳时机
- 整体节奏把控建议
- 关键时间节点提醒

### 4. 产品植入策略 (product_strategy)
- 最有效的产品引出方式
- 最自然的植入方式
- 产品提及频率建议
- 品牌展示策略

### 5. 行动号召策略 (cta_strategy)
- 最有效的CTA类型
- CTA的最佳时机
- CTA的表达方式建议
- CTA与内容的结合技巧

### 6. 最佳实践清单 (best_practices)
- 基于数据总结的10条可执行建议
- 每条建议要具体、可操作
- 按重要性排序

## 输出格式要求

请严格按照以下JSON格式输出：

```json
{
    "hook_strategy": {
        "best_hook_types": ["类型1", "类型2"],
        "optimal_duration": "X秒内",
        "key_points": ["要点1", "要点2", "要点3"],
        "avoid_list": ["避免1", "避免2"]
    },
    "structure_strategy": {
        "best_structure_type": "结构类型",
        "recommended_pacing": "节奏建议",
        "time_allocation": {"opening": "X%", "main_content": "X%", "closing": "X%"},
        "transition_tips": ["技巧1", "技巧2"]
    },
    "timing_strategy": {
        "product_appear_timing": "XX-XX秒",
        "content_start_timing": "XX-XX秒",
        "key_milestones": [{"time": "Xs", "action": "动作描述"}],
        "pacing_advice": "整体节奏建议"
    },
    "product_strategy": {
        "best_intro_ways": ["方式1", "方式2"],
        "best_embed_ways": ["方式1", "方式2"],
        "mention_frequency": "建议频率",
        "brand_display_tips": ["建议1", "建议2"]
    },
    "cta_strategy": {
        "best_cta_types": ["类型1", "类型2"],
        "optimal_timing": "建议时机",
        "expression_tips": ["表达技巧1", "表达技巧2"],
        "integration_advice": "与内容结合的建议"
    },
    "best_practices": ["建议1", "建议2", "...共10条"],
    "success_patterns": [{"pattern_name": "名称", "description": "描述", "applicable_scenarios": ["场景"]}],
    "key_insights": ["核心洞察1", "核心洞察2", "核心洞察3"]
}
```

请确保所有建议都基于提供的数据，具有数据支撑，避免主观臆断。
"""


def build_synthesis_prompt(
    keyword: str,
    cover_stats: Dict[str, Any] = None,
    title_stats: Dict[str, Any] = None,
    timeline_stats: Dict[str, Any] = None,
    content_stats: Dict[str, Any] = None,
    product_stats: Dict[str, Any] = None,
    sample_notes: List[Dict[str, Any]] = None
) -> str:
    """
    构建视频综合推理提示词

    Args:
        keyword: 分析的关键词
        cover_stats: 封面分析统计
        title_stats: 标题分析统计
        timeline_stats: 时间轴分析统计
        content_stats: 内容质量分析统计
        product_stats: 产品分析统计
        sample_notes: 样本笔记列表（用于参考）

    Returns:
        完整的提示词
    """
    prompt = VIDEO_SYNTHESIS_PROMPT

    # 添加分析数据
    data_sections = [f"\n## 分析数据\n\n关键词：{keyword}\n"]

    # 添加各类统计数据
    _add_stats_section(data_sections, "封面分析统计", cover_stats)
    _add_stats_section(data_sections, "标题分析统计", title_stats)
    _add_stats_section(data_sections, "时间轴分析统计", timeline_stats)
    _add_stats_section(data_sections, "内容质量分析统计", content_stats)
    _add_stats_section(data_sections, "产品分析统计", product_stats)

    # 添加样本笔记
    if sample_notes:
        _add_sample_notes(data_sections, sample_notes)

    return prompt + "\n".join(data_sections)


def _add_stats_section(
    sections: List[str],
    title: str,
    stats: Dict[str, Any]
) -> None:
    """添加统计数据段落"""
    if stats:
        sections.append(f"### {title}\n{json.dumps(stats, ensure_ascii=False, indent=2)}\n")


def _add_sample_notes(
    sections: List[str],
    sample_notes: List[Dict[str, Any]]
) -> None:
    """添加样本笔记"""
    sample_data = [
        {
            'title': note.get('title', ''),
            'interaction_score': note.get('interaction_score', 0),
            'note_type': note.get('note_type', '')
        }
        for note in sample_notes[:5]
    ]
    sections.append(
        f"### 样本笔记（互动最高的5篇）\n{json.dumps(sample_data, ensure_ascii=False, indent=2)}\n"
    )


# 简化版综合推理提示词（用于快速生成）
VIDEO_QUICK_SYNTHESIS_PROMPT = """基于以下视频分析数据，快速生成5条核心创作建议：

数据摘要：
{data_summary}

请输出5条最重要的、可立即执行的创作建议，每条建议控制在30字以内。

格式：
1. 建议内容
2. 建议内容
3. 建议内容
4. 建议内容
5. 建议内容
"""


def build_quick_synthesis_prompt(data_summary: str) -> str:
    """构建快速综合推理提示词"""
    return VIDEO_QUICK_SYNTHESIS_PROMPT.format(data_summary=data_summary)


# 视频创作检查清单生成提示词
VIDEO_CHECKLIST_PROMPT = """基于分析数据，生成一份视频创作检查清单。

请输出一份可打印的检查清单，包含以下部分：
1. 开场检查（3-5项）
2. 内容检查（5-7项）
3. 产品植入检查（3-5项）
4. 收尾检查（3-5项）
5. 技术检查（3-5项）

每项检查应该是一个是/否问题，便于创作者自查。

格式示例：
□ 开场3秒内是否有明确的钩子？
□ 产品是否在30秒内出现？
"""


def build_checklist_prompt(
    keyword: str,
    synthesis_data: Dict[str, Any] = None
) -> str:
    """构建检查清单生成提示词"""
    prompt = VIDEO_CHECKLIST_PROMPT + f"\n\n关键词：{keyword}\n"
    if synthesis_data:
        prompt += f"\n参考数据：\n{json.dumps(synthesis_data, ensure_ascii=False, indent=2)}"
    return prompt
