"""
音画同步分析模块
提供视频帧抽取、语音识别、音画同步等功能
"""
import os
import time
import asyncio
from typing import Dict, Any, List, Optional
from loguru import logger
from dotenv import load_dotenv

from .video_processor import VideoProcessor, VideoDownloadError, FrameExtractionError
from .audio_extractor import AudioExtractor, AudioExtractionError
from .qwen_asr import QwenASRService, ASRError
from .sync_analyzer import SyncAnalyzer

from viral_agent.models.av_sync_model import AVSyncResult, VideoFrame, TranscriptSegment

load_dotenv()


class AVSyncAnalyzer:
    """音画同步分析器（总入口）"""

    def __init__(
        self,
        cache_dir: str = None,
        download_manager: 'VideoDownloadManager' = None
    ):
        """
        初始化分析器

        Args:
            cache_dir: 缓存目录
            download_manager: 视频下载管理器（可选，用于共享下载避免重复）
        """
        self.cache_dir = cache_dir or os.path.join("datas", "av_sync_cache")
        self.cleanup_enabled = os.getenv('CLEANUP_TEMP_FILES', 'true').lower() == 'true'

        # 视频下载管理器（共享下载，避免重复）
        self.download_manager = download_manager

        # 读取配置
        self.frame_interval = float(os.getenv('FRAME_INTERVAL', '5.0'))
        self.max_frames = int(os.getenv('MAX_FRAMES_PER_VIDEO', '20'))

        # 初始化子模块
        self.video_processor = VideoProcessor(self.cache_dir)
        self.audio_extractor = AudioExtractor(self.cache_dir)
        self.asr_service = QwenASRService()
        self.sync_analyzer = SyncAnalyzer()

        logger.info(f"音画同步分析器初始化: 帧间隔={self.frame_interval}s, 最大帧数={self.max_frames}")

    async def analyze(
        self,
        video_url: str,
        video_urls: List[Dict] = None,
        note_id: str = "",
        title: str = "",
        description: str = ""
    ) -> AVSyncResult:
        """
        执行完整的音画同步分析

        Args:
            video_url: 视频URL
            video_urls: 备选视频URL列表
            note_id: 笔记ID
            title: 视频标题
            description: 视频描述

        Returns:
            AVSyncResult 分析结果
        """
        result = AVSyncResult(
            note_id=note_id,
            video_url=video_url,
            status="processing"
        )

        start_time = time.time()
        video_path = None
        audio_path = None
        video_from_manager = False  # 标记视频是否来自共享管理器

        try:
            # Step 1: 下载视频
            logger.info("Step 1/5: 下载视频...")
            if self.download_manager:
                # 使用共享下载管理器（避免重复下载）
                video_path, video_from_manager = await self._download_via_manager(
                    video_url, video_urls, note_id
                )
            else:
                # 回退到独立下载
                video_path = await self.video_processor.download_video(
                    video_url,
                    backup_urls=video_urls
                )

            # Step 2: 并行执行帧抽取和音频提取
            logger.info("Step 2/5: 帧抽取 + 音频提取（并行）...")
            frames_task = self.video_processor.extract_frames(
                video_path,
                interval=self.frame_interval,
                max_frames=self.max_frames
            )
            audio_task = self.audio_extractor.extract_audio(video_path)

            frames, audio_path = await asyncio.gather(frames_task, audio_task)

            result.frames = frames
            result.frame_count = len(frames)
            result.frame_interval = self.frame_interval

            # 获取视频时长
            if frames:
                result.video_duration = frames[-1].timestamp + self.frame_interval

            # Step 3: ASR 语音识别
            logger.info("Step 3/5: ASR 语音识别...")
            if self.asr_service.available:
                transcript = await self.asr_service.transcribe(audio_path)
                result.transcript = transcript
                result.full_text = self.asr_service.get_full_text(transcript)
                result.asr_provider = "qwen3-asr-flash"
            else:
                logger.warning("ASR 服务不可用，跳过语音识别")
                result.transcript = []
                result.full_text = ""
                result.asr_provider = "none"

            # Step 4: 音画同步
            logger.info("Step 4/5: 音画同步对齐...")
            synced_timeline = self.sync_analyzer.align(frames, result.transcript)
            result.synced_timeline = synced_timeline

            # Step 5: 关键事件检测
            logger.info("Step 5/5: 关键事件检测...")
            key_moments = self.sync_analyzer.detect_key_moments(
                synced_timeline,
                title=title,
                description=description
            )

            # 生成时间轴摘要
            result.timeline_summary = self.sync_analyzer.generate_timeline_summary(
                synced_timeline,
                key_moments
            )

            result.status = "success"
            result.process_time = time.time() - start_time

            logger.success(f"✅ 音画同步分析完成，耗时 {result.process_time:.1f}s")

        except VideoDownloadError as e:
            result.status = "failed"
            result.error_message = f"视频下载失败: {e}"
            logger.error(result.error_message)

        except FrameExtractionError as e:
            result.status = "failed"
            result.error_message = f"帧抽取失败: {e}"
            logger.error(result.error_message)

        except AudioExtractionError as e:
            result.status = "failed"
            result.error_message = f"音频提取失败: {e}"
            logger.error(result.error_message)

        except ASRError as e:
            # ASR 失败时降级为仅帧分析
            result.status = "partial"
            result.error_message = f"ASR失败（降级为仅帧分析）: {e}"
            result.asr_provider = "none"
            logger.warning(result.error_message)

        except Exception as e:
            result.status = "failed"
            result.error_message = f"未知错误: {e}"
            logger.error(result.error_message)
            import traceback
            logger.error(traceback.format_exc())

        finally:
            # 清理临时文件
            if self.cleanup_enabled:
                # 如果视频来自共享管理器，不清理视频（由管理器负责）
                self._cleanup(
                    video_path if not video_from_manager else None,
                    audio_path,
                    result.frames
                )
            # 释放管理器引用（即使清理被禁用也要释放）
            if video_from_manager and self.download_manager:
                self.download_manager.release(video_url, note_id)

        return result

    async def _download_via_manager(
        self,
        video_url: str,
        video_urls: List[Dict] = None,
        note_id: str = ""
    ) -> tuple:
        """
        通过共享下载管理器获取视频（P1-download）

        使用 VideoDownloadManager 实现下载共享，避免同一视频被重复下载。
        当 VideoAIAnalyzer 也在分析同一视频时，两者共用同一份下载文件。

        Args:
            video_url: 视频URL
            video_urls: 备选URL列表
            note_id: 笔记ID

        Returns:
            (视频文件路径, 是否来自管理器)
        """
        try:
            # 通过管理器获取视频（require_file=True 表示需要本地文件路径）
            handle = await self.download_manager.acquire(
                url=video_url,
                backup_urls=video_urls,
                require_file=True,    # AVSync 需要本地文件供 ffmpeg 处理
                require_bytes=False,  # 不需要 bytes 数据
                note_id=note_id
            )

            if handle.local_path:
                logger.info(
                    f"✅ 通过共享管理器获取视频: {handle.size_bytes/1024/1024:.1f}MB, "
                    f"缓存={handle.is_cached}"
                )
                return handle.local_path, True
            else:
                raise Exception("下载管理器返回空路径")

        except Exception as e:
            logger.warning(f"共享管理器下载失败: {e}，回退到独立下载")
            # 回退到独立下载
            video_path = await self.video_processor.download_video(
                video_url,
                backup_urls=video_urls
            )
            return video_path, False

    async def analyze_with_local_video(
        self,
        video_path: str,
        note_id: str = "",
        title: str = "",
        description: str = "",
        video_url: str = ""
    ) -> AVSyncResult:
        """
        使用本地视频文件执行音画同步分析（跳过下载步骤）

        P1-download-fix: 支持 VideoEnhancedAnalyzer 传入预下载的视频

        Args:
            video_path: 本地视频文件路径
            note_id: 笔记ID
            title: 视频标题
            description: 视频描述
            video_url: 原始视频URL（用于记录）

        Returns:
            AVSyncResult 分析结果
        """
        result = AVSyncResult(
            note_id=note_id,
            video_url=video_url,
            status="processing"
        )

        start_time = time.time()
        audio_path = None

        try:
            # Step 1: 跳过下载，直接使用本地视频
            logger.info(f"Step 1/5: 使用预下载的视频: {video_path}")

            # Step 2: 并行执行帧抽取和音频提取
            logger.info("Step 2/5: 帧抽取 + 音频提取（并行）...")
            frames_task = self.video_processor.extract_frames(
                video_path,
                interval=self.frame_interval,
                max_frames=self.max_frames
            )
            audio_task = self.audio_extractor.extract_audio(video_path)

            frames, audio_path = await asyncio.gather(frames_task, audio_task)

            result.frames = frames
            result.frame_count = len(frames)
            result.frame_interval = self.frame_interval

            # 获取视频时长
            if frames:
                result.video_duration = frames[-1].timestamp + self.frame_interval

            # Step 3: ASR 语音识别
            logger.info("Step 3/5: ASR 语音识别...")
            if self.asr_service.available:
                transcript = await self.asr_service.transcribe(audio_path)
                result.transcript = transcript
                result.full_text = self.asr_service.get_full_text(transcript)
                result.asr_provider = "qwen3-asr-flash"
            else:
                logger.warning("ASR 服务不可用，跳过语音识别")
                result.transcript = []
                result.full_text = ""
                result.asr_provider = "none"

            # Step 4: 音画同步
            logger.info("Step 4/5: 音画同步对齐...")
            synced_timeline = self.sync_analyzer.align(frames, result.transcript)
            result.synced_timeline = synced_timeline

            # Step 5: 关键事件检测
            logger.info("Step 5/5: 关键事件检测...")
            key_moments = self.sync_analyzer.detect_key_moments(
                synced_timeline,
                title=title,
                description=description
            )

            # 生成时间轴摘要
            result.timeline_summary = self.sync_analyzer.generate_timeline_summary(
                synced_timeline,
                key_moments
            )

            result.status = "success"
            result.process_time = time.time() - start_time

            logger.success(f"✅ 音画同步分析完成（使用预下载视频），耗时 {result.process_time:.1f}s")

        except FrameExtractionError as e:
            result.status = "failed"
            result.error_message = f"帧抽取失败: {e}"
            logger.error(result.error_message)

        except AudioExtractionError as e:
            result.status = "failed"
            result.error_message = f"音频提取失败: {e}"
            logger.error(result.error_message)

        except ASRError as e:
            # ASR 失败时降级为仅帧分析
            result.status = "partial"
            result.error_message = f"ASR失败（降级为仅帧分析）: {e}"
            result.asr_provider = "none"
            logger.warning(result.error_message)

        except Exception as e:
            result.status = "failed"
            result.error_message = f"未知错误: {e}"
            logger.error(result.error_message)
            import traceback
            logger.error(traceback.format_exc())

        finally:
            # 清理临时文件（注意：不清理视频，因为是外部提供的）
            if self.cleanup_enabled:
                self._cleanup(None, audio_path, result.frames)

        return result

    def _cleanup(
        self,
        video_path: Optional[str],
        audio_path: Optional[str],
        frames: List[VideoFrame]
    ):
        """清理临时文件"""
        if video_path:
            self.video_processor.cleanup_video(video_path)
        if audio_path:
            self.audio_extractor.cleanup_audio(audio_path)
        if frames:
            self.video_processor.cleanup_frames(frames)


# 导出
__all__ = [
    'AVSyncAnalyzer',
    'VideoProcessor',
    'AudioExtractor',
    'QwenASRService',
    'SyncAnalyzer',
    'VideoDownloadError',
    'FrameExtractionError',
    'AudioExtractionError',
    'ASRError'
]
