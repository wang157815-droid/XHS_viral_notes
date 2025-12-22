"""
视频内容质量分析提示词
用于分析小红书视频笔记的内容结构、叙事质量和情感节奏
对标图文笔记的4维分析：开场钩子/结构/情感/收尾
"""

from typing import Dict, Any, List, Optional

# 从解析器模块导入（保持向后兼容）
from viral_agent.prompts.video_content_prompts_parser import (
    parse_content_analysis,
    extract_content_features,
    parse_quick_analysis
)

# 视频内容质量分析提示词
VIDEO_CONTENT_ANALYSIS_PROMPT = """您是一位资深短视频内容分析专家，专精于小红书爆款视频的内容质量评估。
现需要您观看提供的视频，并从以下4个维度进行深度分析。

【重要】：所有分析必须基于实际观看视频内容得出，不能仅凭标题推测。

## 分析维度一：开场钩子分析（前3秒）

评估视频开头如何吸引观众注意力：

**钩子类型：**
1. 悬念型：抛出问题或悬念，引发好奇心
2. 痛点型：直击用户痛点，引发共鸣
3. 效果前置型：开篇展示惊艳效果或对比
4. 疑问型：以问句开场，激发思考
5. 故事型：以故事或场景引入
6. 直接型：开门见山介绍主题
7. 数字型：以数字或数据开场（如"3步解决"）
8. 热点型：借助热点话题切入

**评分标准（1-5分）：**
- 5分：钩子极具吸引力，让人无法划走
- 4分：钩子较强，能引起兴趣
- 3分：钩子一般，中规中矩
- 2分：钩子较弱，吸引力不足
- 1分：无明显钩子或开场拖沓

**视觉冲击评估：**
- 是否有视觉冲击元素（对比、变化、特效等）
- 画面是否吸引眼球

## 分析维度二：内容结构分析

评估视频的整体结构和节奏：

**结构类型：**
1. 教程式：步骤清晰，循序渐进
2. 对比式：前后对比、产品对比、方法对比
3. 故事式：有起承转合的叙事
4. 清单式：列举多个要点或产品
5. 测评式：产品测试和评价
6. Vlog式：生活化记录
7. 科普式：知识讲解和科普
8. 混合式：多种结构结合

**节奏评估：**
- 快节奏：信息密度高，切换频繁
- 中等：节奏适中，张弛有度
- 慢节奏：娓娓道来，详细展开

**转场质量：**
- 流畅自然
- 略显生硬
- 需要改进

## 分析维度三：情感节奏分析

评估视频的情感走向和共鸣点：

**情感曲线：**
1. 平稳型：情绪稳定，信息传递为主
2. 递进型：情感逐步升温
3. 高潮型：有明显的情感高潮点
4. 波动型：情感起伏变化

**共鸣点识别：**
- 记录最强共鸣点出现的时间段
- 共鸣点类型：痛点共鸣/效果惊喜/情感触动/幽默搞笑

**信任建立方式：**
1. 真实体验分享
2. 数据/证据支撑
3. 专业知识展示
4. 亲切人设建立
5. 对比实验证明

## 分析维度四：收尾引导分析

评估视频结尾的行动引导：

**CTA类型：**
1. 关注引导：引导关注账号
2. 收藏引导：引导收藏笔记
3. 评论引导：引导评论互动
4. 购买引导：引导购买产品
5. 点赞引导：引导点赞
6. 无明确CTA：自然结束

**CTA时机：**
- 末尾5秒内
- 末尾10秒内
- 中间穿插
- 无CTA

**结尾记忆点：**
- 是否有金句/总结
- 是否有视觉记忆点
- 是否有重复强调

## 输出格式要求

请严格按照以下JSON格式输出分析结果：

```json
{
    "hook_analysis": {
        "hook_type": "钩子类型",
        "hook_score": 1-5,
        "visual_impact": true/false,
        "hook_description": "简要描述开场钩子"
    },
    "structure_analysis": {
        "structure_type": "结构类型",
        "pacing": "快节奏/中等/慢节奏",
        "transition_quality": "流畅自然/略显生硬/需要改进",
        "structure_description": "简要描述内容结构"
    },
    "emotion_analysis": {
        "emotion_curve": "情感曲线类型",
        "resonance_time": "共鸣点时间段（如15-25s）",
        "resonance_type": "共鸣点类型",
        "trust_building": "信任建立方式"
    },
    "ending_analysis": {
        "cta_type": "CTA类型",
        "cta_position": "CTA时机",
        "memorable_ending": true/false,
        "ending_description": "简要描述结尾特点"
    },
    "overall_score": 1-10,
    "key_insights": ["洞察1", "洞察2", "洞察3"]
}
```
"""


def get_content_analysis_prompt(
    video_url: str = None,
    title: str = None,
    description: str = None,
    duration: int = None
) -> str:
    """
    获取视频内容质量分析提示词

    Args:
        video_url: 视频URL
        title: 视频标题
        description: 视频描述
        duration: 视频时长（秒）

    Returns:
        完整的提示词
    """
    prompt = VIDEO_CONTENT_ANALYSIS_PROMPT

    # 添加视频上下文信息
    context = ["\n【视频信息】"]
    if video_url:
        context.append(f"视频URL: {video_url}")
    if title:
        context.append(f"视频标题: {title}")
    if description:
        desc_text = f"{description[:200]}..." if len(description or '') > 200 else description
        context.append(f"视频描述: {desc_text}")
    if duration:
        context.append(f"视频时长: {duration}秒")

    if len(context) > 1:
        prompt += "\n" + "\n".join(context)

    return prompt


# 批量分析的简化提示词（用于大批量分析时降低成本）
VIDEO_CONTENT_QUICK_ANALYSIS_PROMPT = """快速分析视频内容质量，输出简化结果：

格式：钩子类型|钩子分数|结构类型|节奏|CTA类型|总分

示例：痛点型|4|教程式|中等|收藏引导|8

请只输出一行结果，不要其他解释。"""


def get_quick_analysis_prompt(title: str = None) -> str:
    """获取快速分析提示词"""
    prompt = VIDEO_CONTENT_QUICK_ANALYSIS_PROMPT
    if title:
        prompt += f"\n\n视频标题: {title}"
    return prompt
