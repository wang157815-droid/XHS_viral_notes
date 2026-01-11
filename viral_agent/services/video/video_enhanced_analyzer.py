"""
增强的视频分析器
集成所有视频分析功能，提供统一的分析接口
支持音画同步分析（可选）
支持视频下载共享（避免重复下载）
"""
import os
import asyncio
from typing import Dict, List, Any, Optional
from loguru import logger
from datetime import datetime

from viral_agent.services.video.video_cover_classifier import VideoCoverClassifier
from viral_agent.services.video.video_title_classifier import VideoTitleClassifier
from viral_agent.services.video.video_timeline_analyzer import VideoTimelineAnalyzer
from viral_agent.services.video.video_ai_analyzer import VideoAIAnalyzer
from viral_agent.models.video_analysis_model import (
    VideoAnalysisResult,
    VideoCoverAnalysis,
    VideoTitleAnalysis,
    VideoTimelineAnalysis,
    VideoAnalysisBatch
)

# 视频下载管理器（共享下载）
try:
    from viral_agent.services.download import VideoDownloadManager
    DOWNLOAD_MANAGER_AVAILABLE = True
except ImportError:
    DOWNLOAD_MANAGER_AVAILABLE = False
    logger.warning("视频下载管理器未加载，将使用独立下载模式")

# 音画同步分析模块（可选加载）
try:
    from viral_agent.services.av_sync import AVSyncAnalyzer
    AV_SYNC_AVAILABLE = True
except ImportError:
    AV_SYNC_AVAILABLE = False
    logger.warning("音画同步模块未加载，请检查依赖是否完整")


class VideoEnhancedAnalyzer:
    """增强的视频分析器，集成所有视频分析功能"""

    def __init__(
        self,
        enable_ai: bool = True,
        enable_av_sync: bool = None,
        video_source_mode: Optional[str] = None
    ):
        """
        初始化增强分析器

        Args:
            enable_ai: 是否启用AI分析
            enable_av_sync: 是否启用音画同步分析（None表示读取环境变量）
            video_source_mode: 视频源模式 ("url"=URL直传, "proxy"=本地下载)，None使用环境变量
        """
        self.enable_ai = enable_ai

        # P1-download-fix: 保存实际模式（优先使用参数，否则使用环境变量）
        self.video_source_mode = video_source_mode or os.getenv('VIDEO_SOURCE_MODE', 'url')

        # 读取音画同步配置
        if enable_av_sync is None:
            self.enable_av_sync = os.getenv('ENABLE_AV_SYNC', 'false').lower() == 'true'
        else:
            self.enable_av_sync = enable_av_sync

        # P1-download: 创建共享的视频下载管理器
        # 只有在需要下载的场景（proxy模式 或 音画同步）才创建
        self.download_manager = None
        need_download = self.video_source_mode == 'proxy' or self.enable_av_sync

        if need_download and DOWNLOAD_MANAGER_AVAILABLE:
            try:
                self.download_manager = VideoDownloadManager(
                    cache_dir=os.path.join("datas", "video_cache"),
                    max_size_mb=int(os.getenv('VIDEO_MAX_SIZE_MB', '50')),
                    download_timeout=int(os.getenv('VIDEO_DOWNLOAD_TIMEOUT', '60')),
                    max_concurrent=int(os.getenv('VIDEO_MAX_CONCURRENT', '2'))
                )
                logger.info("✅ 视频下载管理器已创建（共享下载模式）")
            except Exception as e:
                logger.warning(f"视频下载管理器创建失败，将使用独立下载: {e}")
                self.download_manager = None

        # 初始化AI分析器（如果启用），传递实际视频源模式和下载管理器
        self.ai_analyzer = VideoAIAnalyzer(
            video_source_mode=self.video_source_mode,  # P1-download-fix: 使用解析后的实际模式
            download_manager=self.download_manager
        ) if enable_ai else None

        # 初始化各个专门的分析器
        self.cover_classifier = VideoCoverClassifier(self.ai_analyzer)
        self.title_classifier = VideoTitleClassifier(self.ai_analyzer)
        self.timeline_analyzer = VideoTimelineAnalyzer(self.ai_analyzer)

        # 初始化音画同步分析器（可选），传递下载管理器
        self.av_sync_analyzer = None
        if self.enable_av_sync and AV_SYNC_AVAILABLE:
            self.av_sync_analyzer = AVSyncAnalyzer(
                download_manager=self.download_manager
            )
            logger.info("✅ 音画同步分析器已启用")
        elif self.enable_av_sync and not AV_SYNC_AVAILABLE:
            logger.warning("音画同步分析已启用但模块不可用，请检查依赖")

        logger.info(f"视频增强分析器初始化完成，AI分析: {enable_ai}, 音画同步: {self.enable_av_sync}")

    async def analyze_single_video(
        self,
        note: Dict[str, Any],
        analyze_cover: bool = True,
        analyze_title: bool = True,
        analyze_timeline: bool = True,
        analyze_av_sync: bool = None
    ) -> VideoAnalysisResult:
        """
        分析单个视频笔记

        Args:
            note: 视频笔记数据
            analyze_cover: 是否分析封面
            analyze_title: 是否分析标题
            analyze_timeline: 是否分析时间轴
            analyze_av_sync: 是否分析音画同步（None表示使用实例默认配置）

        Returns:
            视频分析结果
        """
        # 确定是否启用音画同步分析
        do_av_sync = analyze_av_sync if analyze_av_sync is not None else self.enable_av_sync
        # 创建结果对象
        result = VideoAnalysisResult(
            note_id=note.get('note_id', 'unknown'),
            video_url=note.get('video_addr', ''),
            title=note.get('title', ''),
            analysis_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )

        # P1-download-fix: 在顶层统一管理视频下载，确保跨子任务共享
        video_url = note.get('video_addr')
        video_urls = note.get('video_urls', [])
        note_id = note.get('note_id', '')
        video_handle = None
        need_video_download = (
            self.download_manager and
            video_url and
            (do_av_sync or (analyze_timeline and self.video_source_mode == 'proxy'))
        )

        try:
            # 如果需要视频下载，在顶层统一 acquire（避免子任务各自下载）
            if need_video_download:
                logger.info(f"🎬 顶层统一下载视频: {note_id}")
                try:
                    video_handle = await self.download_manager.acquire(
                        url=video_url,
                        backup_urls=video_urls,
                        require_file=do_av_sync,      # AVSync 需要文件路径
                        require_bytes=self.video_source_mode == 'proxy',  # AI需要bytes
                        note_id=note_id
                    )
                    logger.info(f"✅ 视频下载完成: {video_handle.size_bytes/1024/1024:.1f}MB")
                except Exception as e:
                    logger.warning(f"顶层视频下载失败，子任务将独立下载: {e}")
                    video_handle = None

            # 并发执行所有分析任务（包括AVSync，实现真正的并行+共享）
            tasks = []
            task_names = []

            # 准备封面分析任务
            if analyze_cover and note.get('video_cover'):
                logger.debug(f"准备封面分析: {note_id}")
                tasks.append(self.cover_classifier.classify_cover(
                    note['video_cover'],
                    note.get('title')
                ))
                task_names.append('cover')

            # 准备标题分析任务
            if analyze_title and note.get('title'):
                logger.debug(f"准备标题分析: {note_id}")
                tasks.append(self.title_classifier.classify_title(
                    note['title']
                ))
                task_names.append('title')

            # 准备时间轴分析任务
            if analyze_timeline and video_url:
                logger.debug(f"准备时间轴分析: {note_id}")
                tasks.append(self.timeline_analyzer.analyze_timeline(
                    video_url,
                    note.get('title'),
                    note.get('desc'),
                    video_urls=video_urls,  # P0-4: 透传备选URL实现多源兜底
                    note_id=note_id  # P1-download: 透传note_id实现下载共享
                ))
                task_names.append('timeline')

            # 准备音画同步分析任务（现在与其他任务并行，共享已下载的视频）
            if do_av_sync and self.av_sync_analyzer and video_url:
                logger.debug(f"准备音画同步分析: {note_id}")
                tasks.append(self._run_av_sync_analysis(
                    note, video_handle
                ))
                task_names.append('av_sync')

            # 并发执行所有任务
            # P1-fix-1: 收集子任务失败信息
            failed_tasks = []

            if tasks:
                results_list = await asyncio.gather(*tasks, return_exceptions=True)

                # 处理结果
                for task_name, task_result in zip(task_names, results_list):
                    if isinstance(task_result, Exception):
                        logger.warning(f"{task_name}分析失败: {task_result}")
                        # P1-fix-1: 记录失败的子任务
                        failed_tasks.append(f"{task_name}:{type(task_result).__name__}")
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
                        # P1-fix-1 补充：检查 timeline 是否返回 error 字段（非异常的失败）
                        if task_result.get('error'):
                            logger.warning(f"timeline分析返回错误: {task_result.get('error')}")
                            failed_tasks.append(f"timeline:{task_result.get('error')[:30]}")
                            continue

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

                    elif task_name == 'av_sync':
                        # P1-download-fix: 处理并发执行的 AVSync 结果
                        result.av_sync_result = task_result
                        logger.info(f"音画同步分析完成: {task_result.status}")

                # 并发完成后统一延迟一次（代替每步后的1.5秒延迟）
                await asyncio.sleep(1.0)

            # P1-fix-1: 根据子任务结果设置正确的状态
            if failed_tasks:
                # 有子任务失败：判断是部分成功还是全部失败
                success_count = len(task_names) - len(failed_tasks)
                if success_count > 0:
                    result.analysis_status = 'partial'
                    result.error_message = f"部分分析失败: {', '.join(failed_tasks)}"
                    logger.warning(f"视频分析部分成功 {note.get('note_id')}: {result.error_message}")
                else:
                    result.analysis_status = 'failed'
                    result.error_message = f"所有子任务失败: {', '.join(failed_tasks)}"
                    logger.error(f"视频分析全部失败 {note.get('note_id')}: {result.error_message}")
            else:
                result.analysis_status = 'success'

        except Exception as e:
            logger.error(f"视频分析失败 {note.get('note_id')}: {e}")
            result.analysis_status = 'failed'
            result.error_message = str(e)

        finally:
            # P1-download-fix: 统一释放视频引用（所有子任务完成后）
            if video_handle and self.download_manager:
                self.download_manager.release(video_url, note_id)
                logger.debug(f"顶层释放视频引用: {note_id}")

        return result

    async def _run_av_sync_analysis(
        self,
        note: Dict[str, Any],
        video_handle: 'VideoHandle' = None
    ):
        """
        执行音画同步分析的辅助方法

        P1-download-fix: 支持使用预下载的视频，避免重复下载

        Args:
            note: 笔记数据
            video_handle: 预下载的视频句柄（可选）

        Returns:
            AVSyncResult
        """
        try:
            # 如果有预下载的视频且有本地路径，直接使用
            if video_handle and video_handle.local_path:
                logger.info(f"🔗 AVSync 使用预下载的视频: {video_handle.local_path}")
                # 调用 AVSync 的内部分析方法（跳过下载步骤）
                return await self.av_sync_analyzer.analyze_with_local_video(
                    video_path=video_handle.local_path,
                    note_id=note.get('note_id', ''),
                    title=note.get('title', ''),
                    description=note.get('desc', ''),
                    video_url=note.get('video_addr', '')
                )
            else:
                # 回退到标准流程（自行下载）
                return await self.av_sync_analyzer.analyze(
                    video_url=note.get('video_addr', ''),
                    video_urls=note.get('video_urls', []),
                    note_id=note.get('note_id', ''),
                    title=note.get('title', ''),
                    description=note.get('desc', '')
                )
        except Exception as e:
            logger.warning(f"音画同步分析失败（降级处理）: {e}")
            raise

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

    def cleanup(self):
        """
        清理资源（包括视频下载缓存）

        建议在分析完成后调用，以释放磁盘空间。
        """
        if self.download_manager:
            self.download_manager.cleanup_all()
            logger.info("✅ 视频下载缓存已清理")

    def get_download_stats(self) -> Dict[str, Any]:
        """
        获取下载管理器统计信息

        Returns:
            统计信息字典
        """
        if self.download_manager:
            return self.download_manager.get_stats()
        return {
            'cached_count': 0,
            'total_size_mb': 0,
            'active_refs': {},
            'in_progress': 0,
            'manager_available': False
        }