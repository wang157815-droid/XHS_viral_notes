"""
视频封面分析提示词
用于分析小红书视频笔记的封面及其压字特征
"""

VIDEO_COVER_ANALYSIS_PROMPT = """您是一位资深社交媒体内容分析师，专精于美妆护肤领域的短视频分析。
现需要您观看提供的小红书视频，重点对小红书视频笔记的封面及其压字进行详细分析。
请您对不同视频的封面及其压字进行分类处理，归纳封面及其压字的共性特征。
请您必须根据实际视频笔记的真实封面及其压字分析，不要有任何其他的多余分析。

#图片分类标准：
1. 个人形象类：
   - 颜值图：主要展示博主面部或全身形象，突出美观外表
   - 素颜图：无妆容或轻度妆容的真实面貌展示
   - 状态对比图：展示使用前后、好/差状态对比的图片

2. 产品展示类：
   - 纯产品图：产品单独展示、多产品合影或平铺展示
   - 人物与产品互动：手持产品、与产品同框或使用产品的场景
   - 博主+产品组合：通常为拼图形式，同时展示博主和产品

3. 内容呈现类：
   - 流程图/教程：展示使用步骤、方法指导的图解内容
   - 干货截图：APP界面截图、数据图表或过程记录截图
   - 科普拼图：包含知识点、专业术语解释的信息图

4. 专业聚焦类：
   - 眼部状态展示：特别关注眼部问题或改善效果
   - 护肤/化妆过程：展示特定美容环节的详细步骤
   - 专业人设/明星相关：展示专业形象或使用明星图片

#分析要求：
1. 仅输出一个简单的分类结果字符串
2. 使用以下固定格式输出：主分类-子分类-图片类型

#输出格式说明：
- 主分类：选择1-4中的一个大类
- 子分类：选择对应主分类下的具体子类别
- 图片类型：单图/拼图/截图

#示例输出：
产品展示类-人物与产品互动-单图

#限制
不要输出任何分析过程或额外解释，只提供上述格式的单行分类结果。"""

def get_cover_classification_prompt(image_url: str = None) -> str:
    """
    获取封面分类提示词

    Args:
        image_url: 封面图片URL（可选）

    Returns:
        完整的提示词
    """
    base_prompt = VIDEO_COVER_ANALYSIS_PROMPT
    if image_url:
        return f"{base_prompt}\n\n图片URL: {image_url}"
    return base_prompt


def parse_cover_classification(result: str) -> dict:
    """
    解析封面分类结果

    Args:
        result: AI返回的分类结果

    Returns:
        结构化的分类信息
    """
    try:
        # 清理结果字符串
        result = result.strip()

        # 解析分类格式：主分类-子分类-图片类型
        parts = result.split('-')

        if len(parts) >= 3:
            return {
                'main_category': parts[0].strip(),
                'sub_category': parts[1].strip(),
                'image_type': parts[2].strip(),
                'raw_result': result
            }
        else:
            return {
                'main_category': 'unknown',
                'sub_category': 'unknown',
                'image_type': 'unknown',
                'raw_result': result,
                'error': 'Invalid format'
            }
    except Exception as e:
        return {
            'error': str(e),
            'raw_result': result
        }


# ============ 视频封面深度分析提示词（新增） ============

VIDEO_COVER_DETAIL_ANALYSIS_PROMPT = """您是一位资深视觉内容分析师，专精于小红书视频封面的深度分析。
请对提供的视频封面图片进行全面分析，从以下4个维度输出详细结果。

## 分析维度一：文字分析

**1. 文字覆盖率 (text_coverage)**
评估封面上文字占画面的比例：
- 无：没有文字
- 少：文字面积<10%
- 中：文字面积10%-30%
- 多：文字面积>30%

**2. 关键词类型 (text_keywords)**
识别文字中的关键词类型（可多选）：
- 数字：包含数字（如"3步"、"100%"）
- 问句：疑问句（如"怎么办？"、"你知道吗？"）
- 情感词：感叹、夸张词（如"绝了！"、"太香了"）
- 品牌词：品牌名称
- 功效词：描述效果（如"去黑眼圈"、"抗衰老"）
- 无：无文字或无法识别

**3. 文字位置 (text_position)**
文字主要出现的位置：
- 标题区：图片顶部区域
- 中心区：图片中央
- 底部区：图片底部
- 分散：多处分布
- 无：没有文字

## 分析维度二：颜色分析

**1. 主色调 (dominant_color)**
识别画面的主要色调：
- 暖色系：红、橙、黄、粉
- 冷色系：蓝、绿、紫
- 中性色：白、黑、灰、棕
- 混合：多种色调混合

**2. 亮度 (brightness)**
- 暗：整体偏暗
- 中：适中
- 亮：整体明亮

**3. 饱和度 (saturation)**
- 低：颜色淡雅
- 中：适中
- 高：颜色鲜艳浓郁

## 分析维度三：布局分析

**1. 图片方向 (orientation)**
- 横：横向图片
- 竖：纵向图片
- 方：接近正方形

**2. 是否拼图 (is_collage)**
- 是：多图拼接/对比图
- 否：单一画面

**3. 人物出镜 (person_presence)**
- 无：无人物
- 单人-全身：单人全身出镜
- 单人-半身：单人半身出镜
- 单人-面部：单人面部特写
- 多人：多人同框
- 局部：手部、眼部等局部

**4. 产品展示 (product_display)**
- 无：无产品
- 手持：手持产品展示
- 平铺：产品平铺展示
- 使用中：正在使用产品
- 产品特写：产品放大展示

## 分析维度四：视觉风格

**1. 整体风格 (style)**
- 真实：真实拍摄，无过多处理
- 滤镜：使用滤镜美化
- 专业：专业摄影感
- 日常：生活化随拍感
- 卡通/手绘：卡通或手绘风格

**2. 视觉复杂度 (complexity)**
- 简洁：元素少，重点突出
- 中等：适量元素
- 丰富：元素较多，信息量大

## 输出格式要求

请严格按照以下JSON格式输出分析结果：

```json
{
    "text_analysis": {
        "text_coverage": "无/少/中/多",
        "text_keywords": ["类型1", "类型2"],
        "text_position": "位置",
        "text_content": "识别到的主要文字内容"
    },
    "color_analysis": {
        "dominant_color": "色调类型",
        "brightness": "暗/中/亮",
        "saturation": "低/中/高",
        "main_colors": ["颜色1", "颜色2"]
    },
    "layout_analysis": {
        "orientation": "横/竖/方",
        "is_collage": true/false,
        "person_presence": "类型",
        "product_display": "类型"
    },
    "visual_style": {
        "style": "风格类型",
        "complexity": "简洁/中等/丰富"
    }
}
```
"""


def get_cover_detail_analysis_prompt(image_url: str = None) -> str:
    """
    获取封面详细分析提示词

    Args:
        image_url: 封面图片URL

    Returns:
        完整的详细分析提示词
    """
    prompt = VIDEO_COVER_DETAIL_ANALYSIS_PROMPT
    if image_url:
        return f"{prompt}\n\n图片URL: {image_url}"
    return prompt


def parse_cover_detail_analysis(result: str) -> dict:
    """
    解析封面详细分析结果

    Args:
        result: AI返回的JSON分析结果

    Returns:
        结构化的详细分析信息
    """
    import json
    import re

    try:
        # 清理结果字符串
        result = result.strip()

        # 尝试提取JSON部分
        json_match = re.search(r'```json\s*(.*?)\s*```', result, re.DOTALL)
        if json_match:
            result = json_match.group(1)
        else:
            json_match = re.search(r'\{.*\}', result, re.DOTALL)
            if json_match:
                result = json_match.group(0)

        # 解析JSON
        data = json.loads(result)

        return {
            'success': True,
            'data': data
        }

    except json.JSONDecodeError as e:
        return {
            'success': False,
            'error': f'JSON解析失败: {str(e)}',
            'raw_result': result
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'raw_result': result
        }


def extract_cover_detail_features(analysis_result: dict) -> dict:
    """
    从详细分析结果中提取特征

    Args:
        analysis_result: 解析后的分析结果

    Returns:
        提取的特征字典
    """
    features = {
        # 文字特征
        'has_text': False,
        'text_coverage': None,
        'has_number': False,
        'has_question': False,
        'has_emotion_word': False,
        'text_position': None,
        # 颜色特征
        'dominant_color': None,
        'is_bright': False,
        'is_saturated': False,
        # 布局特征
        'orientation': None,
        'is_collage': False,
        'has_person': False,
        'person_type': None,
        'has_product': False,
        'product_type': None,
        # 风格特征
        'style': None,
        'complexity': None
    }

    if not analysis_result.get('success'):
        features['error'] = analysis_result.get('error')
        return features

    data = analysis_result.get('data', {})

    try:
        # 文字特征
        text = data.get('text_analysis', {})
        features['text_coverage'] = text.get('text_coverage')
        features['has_text'] = text.get('text_coverage') not in ['无', None]
        keywords = text.get('text_keywords', [])
        features['has_number'] = '数字' in keywords
        features['has_question'] = '问句' in keywords
        features['has_emotion_word'] = '情感词' in keywords
        features['text_position'] = text.get('text_position')

        # 颜色特征
        color = data.get('color_analysis', {})
        features['dominant_color'] = color.get('dominant_color')
        features['is_bright'] = color.get('brightness') == '亮'
        features['is_saturated'] = color.get('saturation') == '高'

        # 布局特征
        layout = data.get('layout_analysis', {})
        features['orientation'] = layout.get('orientation')
        features['is_collage'] = layout.get('is_collage', False)
        features['person_type'] = layout.get('person_presence')
        features['has_person'] = layout.get('person_presence') not in ['无', None]
        features['product_type'] = layout.get('product_display')
        features['has_product'] = layout.get('product_display') not in ['无', None]

        # 风格特征
        style = data.get('visual_style', {})
        features['style'] = style.get('style')
        features['complexity'] = style.get('complexity')

    except Exception as e:
        features['error'] = str(e)

    return features