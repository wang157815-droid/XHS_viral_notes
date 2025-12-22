"""
视频产品分析提示词
用于分析小红书视频笔记中的产品植入策略
扩展原有7个数据点(A-G)到12个数据点(A-L)
对标图文笔记的4维产品分析：时机/场景/方式/策略
"""

from typing import Dict, Any, List, Optional

# 从解析器模块导入（保持向后兼容）
from viral_agent.prompts.video_product_prompts_parser import (
    parse_product_analysis,
    extract_product_features,
    aggregate_product_stats
)

# 视频产品深度分析提示词（扩展版，12个数据点）
VIDEO_PRODUCT_ANALYSIS_PROMPT = """您是一位资深视频营销分析师，专精于小红书短视频的产品植入策略分析。
现需要您观看提供的视频，并提取12个关键产品分析数据点。

【重要】：所有分析必须基于实际观看视频内容得出，时间点必须 < 视频实际时长。

## 需提取的12个数据点

### 时间维度（A-C，单位：秒）

**A. 产品出现时间 (product_appear_time)**
产品首次在画面中展示的确切时间点
- 示例：15s、30s、45s
- 如未出现产品，标记为"/"

**B. 产品使用时间 (product_use_time)**
博主开始实际使用/演示产品的时间点
- 示例：20s、35s、60s
- 如未使用产品，标记为"/"

**C. 干货开始时间 (content_start_time)**
实用内容/教程/技巧正式开始的时间点
- 示例：10s、25s、40s
- 如无干货内容，标记为"/"

### 分类维度（D-G）

**D. 内容大类-类型 (content_type)**
可选值：
- 单品推荐-单品推荐
- 单品推荐-大促单推
- 干货教程-手法干货
- 干货教程-多种用法
- 干货教程-妆教
- 剧情-剧情单推
- vlog-沉浸式
- 合集-伪合集
- 测评-对比测评
- 其他（请注明）

**E. 内容切入点 (entry_point)**
视频如何开始引入主题：
- 问题切入：指出具体问题（如黑眼圈、痘痘、暗沉）
- 场景切入：季节、场合、生活场景
- 效果切入：展示预期效果或对比
- 年龄切入：特定年龄段的需求
- 经验切入：个人经验分享
- 热点切入：借助热点话题
- 其他（请注明）

**F. 产品引出方式 (product_intro_way)**
博主如何引入产品：
- 肌肤问题引出：基于皮肤问题引出
- 自用分享：个人使用经验分享
- 需求引出：基于护理/化妆需求
- 直接带出：无过渡直接展示
- 成分引出：通过成分优势引出
- 功效引出：通过功效卖点引出
- 他人推荐：朋友/网友推荐
- 其他（请注明）

**G. 产品植入方式 (product_embed_way)**
产品如何融入视频内容：
- 流程中植入：在整体流程中自然展示
- 干货手法中植入：在教学过程中使用
- 手持口播：手持产品直接讲解
- 妆教中植入：化妆教程中使用
- 护理按摩植入：按摩/护理过程中使用
- 多种用法展示：展示产品多种使用方法
- 其他（请注明）

### 营销维度（H-L，新增）

**H. 营销场景 (marketing_scene)**
视频的营销定位：
- 种草推荐：以推荐为主的种草内容
- 使用测评：产品使用后的测评反馈
- 教程演示：教学性质的演示内容
- 日常分享：生活化的日常分享
- 避坑指南：帮助避免踩坑的指南
- 开箱体验：新品开箱体验
- 合集推荐：多产品合集推荐
- 其他（请注明）

**I. 产品提及次数 (mention_count)**
视频中产品被口头提及或展示的次数
- 填写数字，如：1、3、5
- 包括产品名、品牌名、口播提及

**J. 品牌可见性 (brand_visible)**
品牌logo/名称是否在视频中清晰可见：
- 清晰可见：品牌信息明显展示
- 部分可见：品牌信息有但不明显
- 不可见：未展示品牌信息

**K. 行动号召类型 (cta_type)**
视频中的购买/行动引导：
- 购买引导：引导购买产品
- 链接引导：引导点击链接
- 搜索引导：引导搜索关键词
- 私信引导：引导私信咨询
- 无明确引导：没有明确的行动号召
- 其他（请注明）

**L. CTA出现时间 (cta_time)**
行动号召出现的时间点（秒）
- 示例：55s、120s
- 如无CTA，标记为"/"

## 输出格式要求

请严格按照以下格式输出分析结果，用英文逗号分隔12个数据点：
Result_A,B,C,D,E,F,G,H,I,J,K,L

### 示例输出：
例1：Result_20s,35s,15s,单品推荐-单品推荐,问题切入,肌肤问题引出,手持口播,种草推荐,3,清晰可见,购买引导,58s
例2：Result_15s,22s,10s,干货教程-手法干货,经验切入,自用分享,干货手法中植入,教程演示,2,部分可见,收藏引导,45s
例3：Result_30s,/,25s,剧情-剧情单推,场景切入,直接带出,流程中植入,日常分享,1,不可见,无明确引导,/

如某项内容无法判断或不存在，请使用"/"标记。
请保持分析的客观性和准确性，完全基于视频实际内容。
"""


def get_product_analysis_prompt(
    video_url: str = None,
    title: str = None,
    description: str = None,
    duration: int = None
) -> str:
    """
    获取视频产品分析提示词

    Args:
        video_url: 视频URL
        title: 视频标题
        description: 视频描述
        duration: 视频时长（秒）

    Returns:
        完整的提示词
    """
    prompt = VIDEO_PRODUCT_ANALYSIS_PROMPT

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
        context.append(f"视频时长: {duration}秒（所有时间点必须小于此值）")

    if len(context) > 1:
        prompt += "\n" + "\n".join(context)

    return prompt
