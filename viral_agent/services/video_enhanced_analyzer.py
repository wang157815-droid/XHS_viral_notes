"""
增强的视频分析器
集成所有视频分析功能，提供统一的分析接口
"""
import asyncio
from typing import Dict, List, Any, Optional
from loguru import logger
from datetime import datetime

from viral_agent.services.video_cover_classifier import VideoCoverClassifier
from viral_agent.services.video_title_classifier import VideoTitleClassifier
from viral_agent.services.video_timeline_analyzer import VideoTimelineAnalyzer
from viral_agent.services.video_ai_analyzer import VideoAIAnalyzer
from viral_agent.models.video_analysis_model import (
    VideoAnalysisResult,
    VideoCoverAnalysis,
    VideoTitleAnalysis,
    VideoTimelineAnalysis,
    VideoAnalysisBatch
)


class VideoEnhancedAnalyzer:
    """增强的视频分析器，集成所有视频分析功能"""

    def __init__(self, enable_ai: bool = True):
        """
        初始化增强分析器

        Args:
            enable_ai: 是否启用AI分析
        """
        self.enable_ai = enable_ai

        # 初始化AI分析器（如果启用）
        self.ai_analyzer = VideoAIAnalyzer() if enable_ai else None

        # 初始化各个专门的分析器
        self.cover_classifier = VideoCoverClassifier(self.ai_analyzer)
        self.title_classifier = VideoTitleClassifier(self.ai_analyzer)
        self.timeline_analyzer = VideoTimelineAnalyzer(self.ai_analyzer)

        logger.info(f"视频增强分析器初始化完成，AI分析: {enable_ai}")

    async def analyze_single_video(
        self,
        note: Dict[str, Any],
        analyze_cover: bool = True,
        analyze_title: bool = True,
        analyze_timeline: bool = True
    ) -> VideoAnalysisResult:
        """
        分析单个视频笔记

        Args:
            note: 视频笔记数据
            analyze_cover: 是否分析封面
            analyze_title: 是否分析标题
            analyze_timeline: 是否分析时间轴

        Returns:
            视频分析结果
        """
        # 创建结果对象
        result = VideoAnalysisResult(
            note_id=note.get('note_id', 'unknown'),
            video_url=note.get('video_addr', ''),
            title=note.get('title', ''),
            analysis_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )

        try:
            # 并发执行三个分析任务（优化：从串行改为并发，节省约60%时间）
            tasks = []
            task_names = []

            # 准备封面分析任务
            if analyze_cover and note.get('video_cover'):
                logger.debug(f"准备封面分析: {note['note_id']}")
                tasks.append(self.cover_classifier.classify_cover(
                    note['video_cover'],
                    note.get('title')
                ))
                task_names.append('cover')

            # 准备标题分析任务
            if analyze_title and note.get('title'):
                logger.debug(f"准备标题分析: {note['note_id']}")
                tasks.append(self.title_classifier.classify_title(
                    note['title']
                ))
                task_names.append('title')

            # 准备时间轴分析任务
            if analyze_timeline and note.get('video_addr'):
                logger.debug(f"准备时间轴分析: {note['note_id']}")
                tasks.append(self.timeline_analyzer.analyze_timeline(
                    note['video_addr'],
                    note.get('title'),
                    note.get('desc')
                ))
                task_names.append('timeline')

            # 并发执行所有任务
            if tasks:
                results_list = await asyncio.gather(*tasks, return_exceptions=True)

                # 处理结果
                for task_name, task_result in zip(task_names, results_list):
                    if isinstance(task_result, Exception):
                        logger.warning(f"{task_name}分析失败: {task_result}")
                        continue

                    if task_name == 'cover':
                        result.cover_analysis = VideoCoverAnalysis(
                            main_category=task_result.get('main_category', 'unknown'),
                            sub_category=task_result.get('sub_category', 'unknown'),
                            image_type=task_result.get('image_type', 'unknown'),
                            raw_result=task_result.get('raw_result', '')
                        )

                    elif task_name == 'title':
                        # 获取分类结果，确保有有效值
                        main_cat = task_result.get('main_category', '')
                        sub_cat = task_result.get('sub_category', '')

                        # 如果分类为空，使用默认值
                        if not main_cat:
                            main_cat = '通用内容类'
                        if not sub_cat:
                            sub_cat = '待细分'

                        result.title_analysis = VideoTitleAnalysis(
                            main_category=main_cat,
                            sub_category=sub_cat,
                            keywords=task_result.get('keywords', []),  # 添加关键词
                            length=len(note['title']),
                            has_emoji=any(ord(c) > 0x1F300 for c in note['title']),
                            has_number=any(c.isdigit() for c in note['title'])
                        )

                    elif task_name == 'timeline':
                        features = task_result.get('features', {})
                        result.timeline_analysis = VideoTimelineAnalysis(
                            product_appear_time=task_result.get('product_appear_time', '/'),
                            product_use_time=task_result.get('product_use_time', '/'),
                            content_start_time=task_result.get('content_start_time', '/'),
                            content_type=task_result.get('content_type', 'unknown'),
                            entry_point=task_result.get('entry_point', 'unknown'),
                            product_intro_way=task_result.get('product_intro_way', 'unknown'),
                            product_embed_way=task_result.get('product_embed_way', 'unknown'),
                            has_product=features.get('has_product', False),
                            product_timing=features.get('product_timing'),
                            has_content=features.get('has_content', False),
                            content_timing=features.get('content_timing'),
                            content_category=features.get('content_category'),
                            content_subcategory=features.get('content_subcategory')
                        )
                        # 保存原始AI分析结果（用于导出显示）
                        raw_ai_analysis = task_result.get('raw_ai_analysis', '')
                        if raw_ai_analysis:
                            result.ai_raw_response = {'analysis': raw_ai_analysis}

                # 并发完成后统一延迟一次（代替每步后的1.5秒延迟）
                await asyncio.sleep(1.0)

            result.analysis_status = 'success'

        except Exception as e:
            logger.error(f"视频分析失败 {note.get('note_id')}: {e}")
            result.analysis_status = 'failed'
            result.error_message = str(e)

        return result

    async def analyze_batch_videos(
        self,
        notes: List[Dict[str, Any]],
        batch_id: Optional[str] = None,
        max_concurrent: int = 2  # 降低默认并发数，避免API限流
    ) -> VideoAnalysisBatch:
        """
        批量分析视频笔记

        Args:
            notes: 视频笔记列表
            batch_id: 批次ID
            max_concurrent: 最大并发数（智谱API限制严格，建议不超过2）

        Returns:
            批量分析结果
        """
        if not batch_id:
            batch_id = datetime.now().strftime('%Y%m%d_%H%M%S')

        logger.info(f"开始批量分析 {len(notes)} 个视频，批次ID: {batch_id}")

        # 创建批次结果对象
        batch_result = VideoAnalysisBatch(
            batch_id=batch_id,
            total_videos=len(notes)
        )

        # 筛选视频笔记
        video_notes = [n for n in notes if n.get('note_type') == '视频']
        logger.info(f"找到 {len(video_notes)} 个视频笔记")

        # 分批处理，避免并发过多
        for i in range(0, len(video_notes), max_concurrent):
            # 批次间添加延迟，避免API限流
            if i > 0:
                await asyncio.sleep(3.0)

            batch = video_notes[i:i + max_concurrent]
            tasks = [self.analyze_single_video(note) for note in batch]
            results = await asyncio.gather(*tasks)

            for result in results:
                batch_result.add_result(result)

            logger.info(f"已完成 {min(i + max_concurrent, len(video_notes))}/{len(video_notes)}")

        # 生成统计数据
        self._generate_batch_statistics(batch_result, video_notes)

        logger.success(f"批量分析完成: 成功{batch_result.success_count}, 失败{batch_result.failed_count}")

        return batch_result

    def _generate_batch_statistics(
        self,
        batch_result: VideoAnalysisBatch,
        notes: List[Dict[str, Any]]
    ):
        """
        生成批量统计数据

        Args:
            batch_result: 批量结果对象
            notes: 原始笔记数据
        """
        # 封面统计
        batch_result.cover_stats = self.cover_classifier.classify_covers_batch(notes)

        # 标题统计
        batch_result.title_stats = self.title_classifier.classify_titles_batch(notes)

        # 时间轴统计
        batch_result.timeline_stats = self.timeline_analyzer.analyze_timelines_batch(notes)

        # 生成综合建议
        batch_result.recommendations = self._generate_recommendations(batch_result)

    def _generate_recommendations(self, batch_result: VideoAnalysisBatch) -> List[str]:
        """
        生成综合建议

        Args:
            batch_result: 批量分析结果

        Returns:
            建议列表
        """
        recommendations = []

        # 基于封面分析的建议
        if batch_result.cover_stats.get('recommendations'):
            recommendations.extend(batch_result.cover_stats['recommendations'][:2])

        # 基于标题分析的建议
        if batch_result.title_stats.get('recommendations'):
            recommendations.extend(batch_result.title_stats['recommendations'][:2])

        # 基于时间轴分析的建议
        if batch_result.timeline_stats.get('recommendations'):
            recommendations.extend(batch_result.timeline_stats['recommendations'][:2])

        # 添加综合建议
        success_rate = (
            batch_result.success_count / batch_result.total_videos * 100
            if batch_result.total_videos > 0 else 0
        )

        if success_rate < 80:
            recommendations.append(
                f"分析成功率{success_rate:.1f}%，建议检查视频URL和网络连接"
            )

        return recommendations

    def get_video_features(self, notes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        提取视频特征（同步接口，供viral_analyzer调用）

        Args:
            notes: 笔记列表

        Returns:
            视频特征字典
        """
        # 筛选视频笔记
        video_notes = [n for n in notes if n.get('note_type') == '视频']

        if not video_notes:
            return {
                'enabled': False,
                'message': '没有找到视频笔记'
            }

        logger.info(f"开始提取 {len(video_notes)} 个视频的特征...")

        # 使用异步运行批量分析 - 兼容嵌套事件循环
        async def run_batch_analysis():
            return await self.analyze_batch_videos(video_notes)

        try:
            # 检查是否已有运行中的事件循环
            try:
                loop = asyncio.get_running_loop()
                # 如果已有运行中的循环，使用 nest_asyncio 支持的方式
                import nest_asyncio
                nest_asyncio.apply()
                batch_result = loop.run_until_complete(run_batch_analysis())
            except RuntimeError:
                # 没有运行中的循环，创建新的
                batch_result = asyncio.run(run_batch_analysis())

            # 构建特征字典
            features = {
                'enabled': True,
                'total_videos': len(video_notes),
                'analysis_success_rate': (
                    batch_result.success_count / batch_result.total_videos * 100
                    if batch_result.total_videos > 0 else 0
                ),
                'cover_analysis': batch_result.cover_stats,
                'title_analysis': batch_result.title_stats,
                'timeline_analysis': batch_result.timeline_stats,
                'recommendations': batch_result.recommendations,
                'sample_results': [
                    r.get_summary() for r in batch_result.results[:5]
                ]
            }

            return features

        except Exception as e:
            logger.error(f"视频特征提取失败: {e}")
            return {
                'enabled': False,
                'message': f'视频分析失败: {str(e)}'
            }