"""
音画同步分析数据模型
定义视频帧、语音转录、同步时刻等数据结构
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime


@dataclass
class WordInfo:
    """词级别信息"""
    word: str
    start_time: float  # 开始时间（秒）
    end_time: float    # 结束时间（秒）
    confidence: float = 0.0


@dataclass
class VideoFrame:
    """视频帧数据"""
    timestamp: float       # 时间戳（秒）
    frame_path: str        # 帧图片本地路径
    frame_index: int       # 帧序号
    scene_description: str = ""  # AI场景描述（可选）


@dataclass
class TranscriptSegment:
    """语音转录片段"""
    start_time: float      # 开始时间（秒）
    end_time: float        # 结束时间（秒）
    text: str              # 转录文本
    confidence: float = 0.0
    words: List[WordInfo] = field(default_factory=list)


@dataclass
class SyncedMoment:
    """同步时刻（帧 + 语音）"""
    timestamp: float
    frame: Optional[VideoFrame] = None
    transcript_segment: Optional[TranscriptSegment] = None
    keywords: List[str] = field(default_factory=list)
    event_type: str = ""  # product_mention/content_start/product_use 等


@dataclass
class TimelineSummary:
    """时间轴摘要"""
    total_duration: float = 0.0
    product_first_mention: Optional[float] = None
    product_use_time: Optional[float] = None
    content_start_time: Optional[float] = None
    key_events: List[Dict[str, Any]] = field(default_factory=list)
    full_transcript: str = ""


@dataclass
class AVSyncResult:
    """音画同步分析结果"""
    note_id: str
    video_url: str
    video_duration: float = 0.0

    # 帧数据
    frames: List[VideoFrame] = field(default_factory=list)
    frame_count: int = 0
    frame_interval: float = 5.0

    # 转录数据
    transcript: List[TranscriptSegment] = field(default_factory=list)
    full_text: str = ""
    asr_provider: str = ""

    # 同步数据
    synced_timeline: List[SyncedMoment] = field(default_factory=list)
    timeline_summary: Optional[TimelineSummary] = None

    # 分析状态
    status: str = "pending"  # pending/processing/success/failed/disabled
    error_message: str = ""
    process_time: float = 0.0
    analysis_time: str = field(default_factory=lambda: datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'note_id': self.note_id,
            'video_url': self.video_url,
            'video_duration': self.video_duration,
            'frame_count': self.frame_count,
            'frame_interval': self.frame_interval,
            'full_text': self.full_text,
            'asr_provider': self.asr_provider,
            'status': self.status,
            'error_message': self.error_message,
            'process_time': self.process_time,
            'analysis_time': self.analysis_time,
            'timeline_summary': self._summary_to_dict() if self.timeline_summary else None
        }

    def _summary_to_dict(self) -> Dict[str, Any]:
        """时间轴摘要转字典"""
        if not self.timeline_summary:
            return {}
        return {
            'total_duration': self.timeline_summary.total_duration,
            'product_first_mention': self.timeline_summary.product_first_mention,
            'product_use_time': self.timeline_summary.product_use_time,
            'content_start_time': self.timeline_summary.content_start_time,
            'key_events': self.timeline_summary.key_events,
            'full_transcript': self.timeline_summary.full_transcript[:500]
        }
