"""
视频内容时间轴分析提示词
用于提取小红书视频笔记的7个关键数据点
支持动态领域知识库加载
"""

from .knowledge_base import build_dynamic_prompt, detect_domain

# 基础提示词模板（通用部分，不包含领域特定知识）
VIDEO_TIMELINE_BASE_PROMPT = """您是一位资深社交媒体内容分析师，专精于美妆护肤领域的短视频分析。

【重要验证步骤】在分析前，请先确认：
1. 您是否成功读取并观看了视频？如果没有，请回答"无法读取视频"
2. 视频的实际时长是多少秒？（必须准确回答）
3. 如果视频时长小于10秒，所有时间点必须在视频时长范围内

现需要您观看提供的小红书视频，并基于真实视频内容提取7个关键数据点。
这些数据点需要通过实际观看才能准确判断，不能仅凭标题或描述推测。

【关键原则】：
- 所有时间点(A/B/C)必须 < 视频实际时长
- 如果无法确定某个时间点，使用"/"标记
- 严禁推测或编造时间点
- 请根据视频实际内容选择分类，不要受标题影响过度推测

需提取的7个关键数据点及详细说明：

1. 产品出现时间(A)
记录产品首次在画面中展示的确切时间点（单位：秒）
示例：29s、75s、115s
如未出现产品，标记为"/"

2. 产品使用时间(B)
记录博主开始实际使用/演示产品的时间点（单位：秒）
示例：30s、52s、107s
如未使用产品，标记为"/"

3. 干货开始时间(C)
记录实用内容/教程/技巧正式开始的时间点（单位：秒）
示例：20s、45s、98s
如无干货内容，标记为"/"

4. 大类-类型(D)
根据文件中的分类系统，结合视频内容判断：
大类可能为：
- 单品推荐：重点推荐单个产品
- 干货教程：分享实用技巧或方法
- 剧情：讲故事或情景表演
- vlog/沉浸式：生活记录或沉浸式体验
- 合集：多产品推荐
- 系列推荐：同品牌系列产品推荐
- 漫画：动画或漫画形式
- 美食：美食相关内容
- 测评：产品测试对比

类型可能为：
- 单品推荐：专注于单一产品
- 手法干货：展示按摩、拨筋、刮痧等技巧
- 剧情单推：通过剧情推荐产品
- vlog：日常记录形式
- 伪合集：看似合集但重点推荐特定产品
- 品牌系列产品：同品牌多产品推荐
- 变装：前后形象变化
- 动画剧情：动画形式展现剧情
- 女性成长：关注女性成长话题
- 大促单推：节日大促销推荐
- 轻科普单推：轻度科普知识
- 多种用法：展示产品多种使用方法
- 妆教：化妆教程
- 内调外养：内外兼修的养护方法

组合示例：干货教程-手法干货、单品推荐-单品推荐、剧情-剧情单推、干货教程-多种用法

5. 内容切入点(E)
视频如何开始引入主题。具体切入点会根据视频领域动态加载（如眼部护理、面部护理、彩妆等）。

通用切入点包括：
- 问题切入：指出具体皮肤/妆容问题
- 场景切入：季节、场合、生活场景
- 效果切入：展示预期效果或对比
- 年龄切入：特定年龄段的护理需求
- 经验切入：个人护肤/化妆经验分享

【注】：会根据视频标题自动加载相关领域的专业切入点知识。

6. 产品引出方式(F)
博主如何引入产品，常见方式包括：

基于问题引出：
- 肌肤问题（指出具体问题后引出）
- 护理需求（指出护理需求后引出）
- 妆容需求（指出化妆需求后引出）

基于体验引出：
- 自用分享/自用好物
- 护肤/化妆经验
- 他人推荐
- 经验分享

直接引出：
- 直接带出（无过渡直接展示）
- 成分引出（通过成分优势引出）
- 功效引出（通过功效卖点引出）

其他方式：
- 针对性护肤/护理
- 品牌合作/公司寄品
- 近期使用心得

【注】：会根据视频标题自动加载相关领域的专业引出方式。

7. 产品植入方式(G)
产品如何融入视频内容，常见方式包括：

内容植入型：
- 流程中植入产品（在整体流程中自然展示）
- 干货手法中植入（在教学过程中使用）
- 干货流程中植入（作为流程一部分）
- 护理按摩植入（按摩/护理过程中使用）

直接展示型：
- 手持口播（手持产品直接讲解）
- 使用方法中植入（展示具体使用方法）
- 妆教中植入（化妆教程中使用）

特定功能植入：
- 早/晚间护肤流程
- 日常涂抹手法植入
- 多种用法展示
- 完整护理流程中植入

【注】：会根据视频标题自动加载相关领域的专业植入方式。

#输出格式要求：
请严格按照以下格式输出分析结果，用英文逗号分隔7个数据点：
Result_A,B,C,D,E,F,G

通用示例（具体领域示例会动态加载）：
例1：Result_30s,60s,45s,单品推荐-单品推荐,熬夜垮脸,自用分享,手持口播
例2：Result_15s,22s,18s,干货教程-手法干货,肌肤问题,护理经验,干货手法中植入
例3：Result_48s,/,52s,剧情-剧情单推,紧致提升,护理需求,流程中植入产品
例4：Result_38s,46s,/,合集-伪合集,以油养肤,自用好物分享,手持口播

如某项内容无法判断或不存在，请使用"/"标记。请保持分析的客观性和准确性，完全基于视频实际内容。

#重要提示：
数据点必须完全基于观看视频得出，不能仅凭视频标题或封面推测
时间点请精确到秒，不需标注分钟，直接使用数字+s表示
产品出现和使用可能为同一时间，也可能有先展示后使用的情况
请参考原始数据集的分类方式，保持术语的一致性
如遇到新类型内容，请选择最接近的分类，确保与数据集整体保持一致
"""


def get_timeline_analysis_prompt(video_url: str = None, title: str = None, description: str = None) -> str:
    """
    获取时间轴分析提示词（动态加载领域知识库）

    Args:
        video_url: 视频URL
        title: 视频标题（可选）
        description: 视频描述（可选）

    Returns:
        完整的提示词（基础提示词 + 动态领域知识）
    """
    # 构建包含动态知识库的提示词
    full_prompt = build_dynamic_prompt(
        title=title or "",
        description=description or "",
        base_prompt=VIDEO_TIMELINE_BASE_PROMPT
    )

    # 添加视频上下文信息
    context = []
    if video_url:
        context.append(f"\n【视频信息】")
        context.append(f"视频URL: {video_url}")
    if title:
        context.append(f"视频标题: {title}")
    if description:
        context.append(f"视频描述: {description}")

    if context:
        return full_prompt + "\n" + "\n".join(context)
    return full_prompt


def parse_timeline_analysis(result: str) -> dict:
    """
    解析时间轴分析结果

    Args:
        result: AI返回的分析结果（格式：Result_A,B,C,D,E,F,G）

    Returns:
        结构化的时间轴信息
    """
    try:
        # 清理结果字符串
        result = result.strip()

        # 移除Result_前缀
        if result.startswith('Result_'):
            result = result[7:]

        # 解析7个数据点
        parts = result.split(',')

        if len(parts) >= 7:
            return {
                'product_appear_time': parts[0].strip(),  # A: 产品出现时间
                'product_use_time': parts[1].strip(),     # B: 产品使用时间
                'content_start_time': parts[2].strip(),   # C: 干货开始时间
                'content_type': parts[3].strip(),         # D: 大类-类型
                'entry_point': parts[4].strip(),          # E: 内容切入点
                'product_intro_way': parts[5].strip(),    # F: 产品引出方式
                'product_embed_way': parts[6].strip(),    # G: 产品植入方式
                'raw_result': result
            }
        else:
            return {
                'error': 'Insufficient data points',
                'raw_result': result,
                'parsed_count': len(parts)
            }
    except Exception as e:
        return {
            'error': str(e),
            'raw_result': result
        }


def extract_timeline_features(analysis_result: dict) -> dict:
    """
    从时间轴分析结果中提取特征

    Args:
        analysis_result: 解析后的时间轴分析结果

    Returns:
        提取的特征字典
    """
    features = {
        'has_product': False,
        'product_timing': None,
        'has_content': False,
        'content_timing': None,
        'content_category': None,
        'content_subcategory': None,
        'entry_strategy': None,
        'product_strategy': None,
        'embed_strategy': None
    }

    try:
        # 产品相关特征
        if analysis_result.get('product_appear_time', '/') != '/':
            features['has_product'] = True
            time_str = analysis_result['product_appear_time'].replace('s', '')
            features['product_timing'] = int(time_str) if time_str.isdigit() else None

        # 内容相关特征
        if analysis_result.get('content_start_time', '/') != '/':
            features['has_content'] = True
            time_str = analysis_result['content_start_time'].replace('s', '')
            features['content_timing'] = int(time_str) if time_str.isdigit() else None

        # 内容类型解析
        content_type = analysis_result.get('content_type', '')
        if '-' in content_type:
            parts = content_type.split('-')
            features['content_category'] = parts[0]
            features['content_subcategory'] = '-'.join(parts[1:])

        # 策略特征
        features['entry_strategy'] = analysis_result.get('entry_point')
        features['product_strategy'] = analysis_result.get('product_intro_way')
        features['embed_strategy'] = analysis_result.get('product_embed_way')

    except Exception as e:
        features['error'] = str(e)

    return features