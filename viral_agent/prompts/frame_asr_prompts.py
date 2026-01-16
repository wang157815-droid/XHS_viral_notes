"""
帧+ASR联合分析Prompt模板
用于多模态AI分析视频帧与语音转录的配合关系
"""

# 主分析Prompt：分析多帧图片+完整语音转录
FRAME_ASR_JOINT_PROMPT = """
你是一个专业的短视频内容分析师，擅长分析视频画面与语音的配合关系。

【视频信息】
标题：{title}
描述：{description}
总时长：{duration:.1f}秒
帧数：{frame_count}帧

【帧时间轴】
{frame_timeline}

【完整语音转录】
{full_transcript}

请分析这个视频的画面与语音配合情况，包括：

1. **逐帧分析**：对每一帧描述：
   - 画面内容：此刻画面展示什么
   - 对应语音：此时段说了什么（根据时间戳匹配）
   - 配合关系：同步展示/先画面后口播/先口播后画面/无关联
   - 内容类型：开场吸引/干货内容/产品展示/互动引导/结尾
   - 产品状态：是否可见、是否被提及
   - 吸引力评分：1-10分

2. **内容结构**：整体内容如何组织（如：开场-痛点-解决方案-产品-结尾）

3. **转折点**：内容类型发生变化的时刻及过渡方式

4. **产品植入分析**：
   - 首次视觉出现时间
   - 首次口播提及时间
   - 植入风格（软植入/硬广/场景化）
   - 画面与口播的同步程度
   - 自然度评分（1-10）

5. **总结**：
   - 画面与语音配合的优缺点
   - 内容节奏评价
   - 改进建议（2-3条）

请以JSON格式输出：
{{
    "frame_analyses": [
        {{
            "frame_index": 0,
            "timestamp": 0.0,
            "visual_content": "画面描述",
            "speech_content": "对应时段的语音内容",
            "visual_speech_relation": "配合关系",
            "content_type": "内容类型",
            "product_visible": false,
            "product_mentioned": false,
            "engagement_score": 7
        }}
    ],
    "content_structure": "开场吸引(0-5s) → 干货内容(5-20s) → 产品展示(20-30s) → 结尾(30-35s)",
    "transitions": [
        {{
            "timestamp": 5.0,
            "from_type": "开场吸引",
            "to_type": "干货内容",
            "transition_method": "自然过渡",
            "smoothness_score": 8
        }}
    ],
    "product_placement": {{
        "first_visual_time": 20.0,
        "first_mention_time": 18.0,
        "placement_style": "场景化植入",
        "visual_speech_sync": "先口播后展示",
        "naturalness_score": 8
    }},
    "visual_speech_summary": "整体配合度评价",
    "content_rhythm": "内容节奏评价",
    "recommendations": ["建议1", "建议2"]
}}
"""

# 简化版Prompt：只分析关键帧
FRAME_ASR_KEY_FRAMES_PROMPT = """
你是短视频分析专家。请分析以下视频的关键帧与语音配合情况。

【视频标题】{title}
【视频时长】{duration:.1f}秒

【关键帧时间轴】
{frame_timeline}

【完整语音转录】
{full_transcript}

请重点分析：
1. 每个关键帧的画面内容及对应时段的语音
2. 画面与语音如何配合
3. 产品何时出现在画面中、何时被口播提及
4. 内容结构是否合理
5. 给出2-3条改进建议

输出JSON格式：
{{
    "frame_analyses": [...],
    "content_structure": "...",
    "product_placement": {{...}},
    "visual_speech_summary": "...",
    "recommendations": [...]
}}
"""

# 帧时间轴格式化模板
FRAME_TIMELINE_TEMPLATE = "帧{index}（{timestamp:.1f}s）：[图片{index}]"

# 语音-帧匹配说明
SPEECH_MATCH_NOTE = """
注意：每帧的"对应语音"应该是该时间戳前后2-3秒内的语音内容。
例如帧在10.0s，则匹配8.0s-12.0s之间的语音。
"""
