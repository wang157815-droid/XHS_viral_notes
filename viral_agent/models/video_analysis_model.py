"""
视频分析数据模型
定义视频分析结果的详细结构
"""
from typing import Optional, Dict, List, Any, TYPE_CHECKING
from dataclasses import dataclass, field, asdict
import json

# 避免循环导入
if TYPE_CHECKING:
    from viral_agent.models.av_sync_model import AVSyncResult
    from viral_agent.models.frame_asr_model import FrameASRAnalysisResult


@dataclass
class VideoCoverAnalysis:
    """视频封面分析结果"""
    main_category: str  # 主分类
    sub_category: str   # 子分类
    image_type: str     # 图片类型（单图/拼图/截图）
    raw_result: str     # 原始结果
    confidence: float = 0.0  # 置信度


@dataclass
class VideoTitleAnalysis:
    """视频标题分析结果"""
    main_category: str  # 主分类（问题解决/干货指导等）
    sub_category: str   # 子分类
    keywords: List[str] = field(default_factory=list)  # 关键词
    length: int = 0     # 标题长度
    has_emoji: bool = False  # 是否包含emoji
    has_number: bool = False  # 是否包含数字
    # 新增：标题元素标记
    has_age: bool = False      # 是否包含年龄/状态
    has_problem: bool = False  # 是否包含问题描述
    has_product: bool = False  # 是否包含产品词
    has_effect: bool = False   # 是否包含效果描述


@dataclass
class VideoTimelineAnalysis:
    """视频时间轴分析结果"""
    # 7个关键数据点
    product_appear_time: str  # A: 产品出现时间
    product_use_time: str     # B: 产品使用时间
    content_start_time: str   # C: 干货开始时间
    content_type: str         # D: 大类-类型
    entry_point: str          # E: 内容切入点
    product_intro_way: str    # F: 产品引出方式
    product_embed_way: str    # G: 产品植入方式

    # 提取的特征
    has_product: bool = False
    product_timing: Optional[int] = None  # 产品出现时间（秒）
    has_content: bool = False
    content_timing: Optional[int] = None  # 内容开始时间（秒）
    content_category: Optional[str] = None
    content_subcategory: Optional[str] = None


@dataclass
class VideoAnalysisResult:
    """完整的视频分析结果"""
    note_id: str
    video_url: str
    title: str

    # 三大分析维度
    cover_analysis: Optional[VideoCoverAnalysis] = None
    title_analysis: Optional[VideoTitleAnalysis] = None
    timeline_analysis: Optional[VideoTimelineAnalysis] = None

    # 音画同步分析结果（可选）
    av_sync_result: Optional[Any] = None  # 实际类型是 AVSyncResult

    # 帧+ASR联合分析结果（可选）
    frame_asr_analysis: Optional[Any] = None  # 实际类型是 FrameASRAnalysisResult

    # 统计数据
    analysis_time: str = ""
    analysis_status: str = "pending"  # pending/success/failed
    error_message: Optional[str] = None

    # AI原始响应（可选）
    ai_raw_response: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = asdict(self)

        # 处理嵌套的dataclass
        if self.cover_analysis:
            result['cover_analysis'] = asdict(self.cover_analysis)
        if self.title_analysis:
            result['title_analysis'] = asdict(self.title_analysis)
        if self.timeline_analysis:
            result['timeline_analysis'] = asdict(self.timeline_analysis)
        if self.av_sync_result:
            # 安全处理 av_sync_result（可能是 dataclass 或 dict）
            from dataclasses import is_dataclass
            if is_dataclass(self.av_sync_result):
                result['av_sync_result'] = asdict(self.av_sync_result)
            elif hasattr(self.av_sync_result, 'to_dict'):
                result['av_sync_result'] = self.av_sync_result.to_dict()
            elif isinstance(self.av_sync_result, dict):
                result['av_sync_result'] = self.av_sync_result
            else:
                result['av_sync_result'] = str(self.av_sync_result)

        # 处理 frame_asr_analysis
        if self.frame_asr_analysis:
            from dataclasses import is_dataclass
            if is_dataclass(self.frame_asr_analysis):
                result['frame_asr_analysis'] = asdict(self.frame_asr_analysis)
            elif hasattr(self.frame_asr_analysis, 'to_dict'):
                result['frame_asr_analysis'] = self.frame_asr_analysis.to_dict()
            elif isinstance(self.frame_asr_analysis, dict):
                result['frame_asr_analysis'] = self.frame_asr_analysis
            else:
                result['frame_asr_analysis'] = str(self.frame_asr_analysis)

        return result

    def to_json(self) -> str:
        """转换为JSON字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def get_summary(self) -> Dict[str, Any]:
        """获取分析摘要"""
        summary = {
            'note_id': self.note_id,
            'title': self.title,
            'status': self.analysis_status
        }

        if self.cover_analysis:
            summary['cover'] = f"{self.cover_analysis.main_category}-{self.cover_analysis.sub_category}"

        if self.title_analysis:
            summary['title_type'] = f"{self.title_analysis.main_category}-{self.title_analysis.sub_category}"

        if self.timeline_analysis:
            summary['content_type'] = self.timeline_analysis.content_type
            summary['product_timing'] = self.timeline_analysis.product_appear_time
            summary['entry_point'] = self.timeline_analysis.entry_point

        # 音画同步摘要
        if self.av_sync_result:
            av = self.av_sync_result
            summary['av_sync'] = {
                'status': av.status,
                'frame_count': av.frame_count,
                'video_duration': av.video_duration,
                'asr_provider': av.asr_provider,
                'full_text': av.full_text[:100] + '...' if len(av.full_text) > 100 else av.full_text
            }
            if av.timeline_summary:
                summary['av_sync']['product_first_mention'] = av.timeline_summary.product_first_mention
                summary['av_sync']['content_start_time'] = av.timeline_summary.content_start_time

        # 帧+ASR联合分析摘要
        if self.frame_asr_analysis:
            fa = self.frame_asr_analysis
            # 兼容 dataclass 和 dict 两种情况
            if isinstance(fa, dict):
                vs_summary = fa.get('visual_speech_summary', '')
                summary['frame_asr'] = {
                    'status': fa.get('status', ''),
                    'frame_count': fa.get('frame_count', 0),
                    'content_structure': fa.get('content_structure', ''),
                    'visual_speech_summary': (
                        vs_summary[:100] + '...' if vs_summary and len(vs_summary) > 100
                        else vs_summary
                    )
                }
                pp = fa.get('product_placement')
                if pp:
                    summary['frame_asr']['product_naturalness'] = (
                        pp.get('naturalness_score') if isinstance(pp, dict)
                        else pp.naturalness_score
                    )
            else:
                summary['frame_asr'] = {
                    'status': fa.status,
                    'frame_count': fa.frame_count,
                    'content_structure': fa.content_structure,
                    'visual_speech_summary': (
                        fa.visual_speech_summary[:100] + '...'
                        if fa.visual_speech_summary and len(fa.visual_speech_summary) > 100
                        else fa.visual_speech_summary
                    )
                }
                if fa.product_placement:
                    summary['frame_asr']['product_naturalness'] = fa.product_placement.naturalness_score

        return summary


@dataclass
class VideoAnalysisBatch:
    """批量视频分析结果"""
    batch_id: str
    total_videos: int
    success_count: int = 0
    failed_count: int = 0

    # 分析结果列表
    results: List[VideoAnalysisResult] = field(default_factory=list)

    # 统计数据
    cover_stats: Dict[str, Any] = field(default_factory=dict)
    title_stats: Dict[str, Any] = field(default_factory=dict)
    timeline_stats: Dict[str, Any] = field(default_factory=dict)

    # PRD合规性统计（新增）
    prd_compliance: Dict[str, Any] = field(default_factory=dict)

    # 切入方式统计（新增）
    entry_point_stats: Dict[str, Any] = field(default_factory=dict)

    # 植入方式统计（新增）
    embed_way_stats: Dict[str, Any] = field(default_factory=dict)

    # 最佳植入策略（新增）
    top3_embed_strategies: List[Dict[str, Any]] = field(default_factory=list)

    # 建议
    recommendations: List[str] = field(default_factory=list)

    def add_result(self, result: VideoAnalysisResult):
        """添加分析结果"""
        self.results.append(result)
        if result.analysis_status == 'success':
            self.success_count += 1
        else:
            self.failed_count += 1

    def generate_stats(self):
        """生成统计数据"""
        # TODO: 实现统计逻辑
        pass

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'batch_id': self.batch_id,
            'total_videos': self.total_videos,
            'success_count': self.success_count,
            'failed_count': self.failed_count,
            'results': [r.to_dict() for r in self.results],
            'cover_stats': self.cover_stats,
            'title_stats': self.title_stats,
            'timeline_stats': self.timeline_stats,
            'prd_compliance': self.prd_compliance,
            'entry_point_stats': self.entry_point_stats,
            'embed_way_stats': self.embed_way_stats,
            'top3_embed_strategies': self.top3_embed_strategies,
            'recommendations': self.recommendations
        }

    def to_json(self) -> str:
        """转换为JSON字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)