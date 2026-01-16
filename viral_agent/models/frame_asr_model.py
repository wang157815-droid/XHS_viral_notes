"""
帧+ASR联合分析数据模型
定义帧与语音联合分析的结果结构
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime


@dataclass
class FrameMomentAnalysis:
    """单帧时刻的语义分析"""
    frame_index: int           # 帧序号
    timestamp: float           # 时间戳（秒）
    visual_content: str        # 画面内容描述
    speech_content: str        # 对应时段的语音内容
    visual_speech_relation: str  # 画面与语音的配合关系
    content_type: str          # 内容类型（开场/干货/产品展示/结尾）
    product_visible: bool = False   # 产品是否可见
    product_mentioned: bool = False  # 产品是否被口播提及
    engagement_score: int = 5       # 预估吸引力（1-10）


@dataclass
class ContentTransition:
    """内容转折点"""
    timestamp: float
    from_type: str             # 转折前内容类型
    to_type: str               # 转折后内容类型
    transition_method: str     # 转折方式（自然过渡/硬切/引导语）
    smoothness_score: int = 5  # 平滑度（1-10）


@dataclass
class ProductPlacementAnalysis:
    """产品植入分析"""
    first_visual_time: Optional[float] = None  # 首次视觉出现
    first_mention_time: Optional[float] = None  # 首次口播提及
    placement_style: str = "unknown"            # 植入风格
    visual_speech_sync: str = "unknown"         # 画面与口播的同步程度
    naturalness_score: int = 5                  # 自然度（1-10）


@dataclass
class FrameASRAnalysisResult:
    """帧+ASR联合分析完整结果"""
    note_id: str
    video_url: str = ""
    frame_count: int = 0
    video_duration: float = 0.0
    full_transcript: str = ""       # 完整语音转录

    # 逐帧分析
    frame_analyses: List[FrameMomentAnalysis] = field(default_factory=list)

    # 结构分析
    content_structure: str = ""     # 内容结构描述
    transitions: List[ContentTransition] = field(default_factory=list)

    # 产品植入分析
    product_placement: Optional[ProductPlacementAnalysis] = None

    # AI总结
    visual_speech_summary: str = ""  # 画面与语音配合总结
    content_rhythm: str = ""         # 内容节奏评价
    recommendations: List[str] = field(default_factory=list)

    # 状态
    status: str = "pending"          # pending/success/failed/partial
    error_message: str = ""
    process_time: float = 0.0
    analysis_time: str = field(
        default_factory=lambda: datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    )

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'note_id': self.note_id,
            'video_url': self.video_url,
            'frame_count': self.frame_count,
            'video_duration': self.video_duration,
            'full_transcript': self.full_transcript[:500] if self.full_transcript else "",
            'frame_analyses': [self._frame_to_dict(f) for f in self.frame_analyses],
            'content_structure': self.content_structure,
            'transitions': [self._transition_to_dict(t) for t in self.transitions],
            'product_placement': self._placement_to_dict() if self.product_placement else None,
            'visual_speech_summary': self.visual_speech_summary,
            'content_rhythm': self.content_rhythm,
            'recommendations': self.recommendations,
            'status': self.status,
            'error_message': self.error_message,
            'process_time': self.process_time,
            'analysis_time': self.analysis_time
        }

    def _frame_to_dict(self, frame: FrameMomentAnalysis) -> Dict[str, Any]:
        """帧分析转字典"""
        return {
            'frame_index': frame.frame_index,
            'timestamp': frame.timestamp,
            'visual_content': frame.visual_content,
            'speech_content': frame.speech_content,
            'visual_speech_relation': frame.visual_speech_relation,
            'content_type': frame.content_type,
            'product_visible': frame.product_visible,
            'product_mentioned': frame.product_mentioned,
            'engagement_score': frame.engagement_score
        }

    def _transition_to_dict(self, trans: ContentTransition) -> Dict[str, Any]:
        """转折点转字典"""
        return {
            'timestamp': trans.timestamp,
            'from_type': trans.from_type,
            'to_type': trans.to_type,
            'transition_method': trans.transition_method,
            'smoothness_score': trans.smoothness_score
        }

    def _placement_to_dict(self) -> Dict[str, Any]:
        """产品植入分析转字典"""
        if not self.product_placement:
            return {}
        p = self.product_placement
        return {
            'first_visual_time': p.first_visual_time,
            'first_mention_time': p.first_mention_time,
            'placement_style': p.placement_style,
            'visual_speech_sync': p.visual_speech_sync,
            'naturalness_score': p.naturalness_score
        }

    def get_summary(self) -> Dict[str, Any]:
        """获取简要摘要"""
        return {
            'note_id': self.note_id,
            'status': self.status,
            'frame_count': self.frame_count,
            'content_structure': self.content_structure,
            'visual_speech_summary': self.visual_speech_summary[:100] if self.visual_speech_summary else "",
            'product_naturalness': (
                self.product_placement.naturalness_score
                if self.product_placement else None
            ),
            'recommendations_count': len(self.recommendations)
        }
