"""
视频处理器
负责视频下载和帧抽取
"""
import os
import asyncio
import subprocess
import tempfile
from typing import List, Optional, Tuple
from pathlib import Path
from loguru import logger
import aiohttp

from viral_agent.models.av_sync_model import VideoFrame


class VideoDownloadError(Exception):
    """视频下载错误"""
    pass


class FrameExtractionError(Exception):
    """帧抽取错误"""
    pass


class VideoProcessor:
    """视频处理器"""

    def __init__(self, cache_dir: str = None):
        """
        初始化视频处理器

        Args:
            cache_dir: 缓存目录，默认使用 datas/av_sync_cache
        """
        self.cache_dir = cache_dir or os.path.join("datas", "av_sync_cache")
        self.ffmpeg_available = self._check_ffmpeg()
        os.makedirs(self.cache_dir, exist_ok=True)
        os.makedirs(os.path.join(self.cache_dir, "videos"), exist_ok=True)
        os.makedirs(os.path.join(self.cache_dir, "frames"), exist_ok=True)

        if self.ffmpeg_available:
            logger.info("✅ ffmpeg 可用")
        else:
            logger.warning("⚠️ ffmpeg 不可用，帧抽取功能将受限")

    def _check_ffmpeg(self) -> bool:
        """检查 ffmpeg 是否可用"""
        try:
            result = subprocess.run(
                ['ffmpeg', '-version'],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    async def download_video(
        self,
        video_url: str,
        backup_urls: List[dict] = None,
        timeout: int = 60
    ) -> str:
        """
        下载视频到本地

        Args:
            video_url: 主视频URL
            backup_urls: 备选URL列表
            timeout: 下载超时时间（秒）

        Returns:
            本地视频文件路径

        Raises:
            VideoDownloadError: 下载失败
        """
        urls_to_try = [video_url]
        if backup_urls:
            for url_info in backup_urls:
                url = url_info.get('url') if isinstance(url_info, dict) else url_info
                if url and url != video_url:
                    urls_to_try.append(url)

        logger.info(f"准备下载视频，共 {len(urls_to_try)} 个URL可用")

        for i, url in enumerate(urls_to_try[:3]):
            try:
                logger.debug(f"尝试下载 URL {i+1}: {url[:80]}...")
                video_path = await self._download_single(url, timeout)
                if video_path and os.path.exists(video_path):
                    logger.success(f"✅ 视频下载成功: {video_path}")
                    return video_path
            except Exception as e:
                logger.warning(f"URL {i+1} 下载失败: {e}")
                continue

        raise VideoDownloadError("所有视频URL均下载失败")

    async def _download_single(self, url: str, timeout: int) -> str:
        """下载单个视频"""
        video_dir = os.path.join(self.cache_dir, "videos")
        video_name = f"video_{hash(url) % 100000}.mp4"
        video_path = os.path.join(video_dir, video_name)

        # 如果已存在且文件有效，直接返回
        if os.path.exists(video_path) and os.path.getsize(video_path) > 1000:
            logger.debug(f"使用缓存视频: {video_path}")
            return video_path

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as response:
                if response.status != 200:
                    raise VideoDownloadError(f"HTTP {response.status}")

                with open(video_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(8192):
                        f.write(chunk)

        return video_path

    async def extract_frames(
        self,
        video_path: str,
        interval: float = 5.0,
        max_frames: int = 20
    ) -> List[VideoFrame]:
        """
        从视频中抽取帧

        Args:
            video_path: 视频文件路径
            interval: 抽帧间隔（秒）
            max_frames: 最大帧数

        Returns:
            VideoFrame 列表

        Raises:
            FrameExtractionError: 帧抽取失败
        """
        if not self.ffmpeg_available:
            raise FrameExtractionError("ffmpeg 不可用")

        if not os.path.exists(video_path):
            raise FrameExtractionError(f"视频文件不存在: {video_path}")

        # 获取视频时长
        duration = await self._get_video_duration(video_path)
        if duration <= 0:
            raise FrameExtractionError("无法获取视频时长")

        logger.info(f"视频时长: {duration:.1f}秒，抽帧间隔: {interval}秒")

        # 计算抽帧时间点
        timestamps = []
        t = 0.0
        while t < duration and len(timestamps) < max_frames:
            timestamps.append(t)
            t += interval

        # 创建帧输出目录
        video_name = Path(video_path).stem
        frame_dir = os.path.join(self.cache_dir, "frames", video_name)
        os.makedirs(frame_dir, exist_ok=True)

        # 并行抽帧
        frames = []
        for i, ts in enumerate(timestamps):
            output_path = os.path.join(frame_dir, f"frame_{i:04d}.jpg")

            success = await self._extract_single_frame(video_path, ts, output_path)
            if success:
                frames.append(VideoFrame(
                    timestamp=ts,
                    frame_path=output_path,
                    frame_index=i
                ))

        logger.success(f"✅ 成功抽取 {len(frames)} 帧")
        return frames

    async def _extract_single_frame(
        self,
        video_path: str,
        timestamp: float,
        output_path: str
    ) -> bool:
        """抽取单帧"""
        cmd = [
            'ffmpeg', '-y',
            '-ss', str(timestamp),
            '-i', video_path,
            '-vframes', '1',
            '-q:v', '2',
            output_path
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await asyncio.wait_for(process.wait(), timeout=10)
            return os.path.exists(output_path) and os.path.getsize(output_path) > 0
        except asyncio.TimeoutError:
            logger.warning(f"帧抽取超时: {timestamp}s")
            return False
        except Exception as e:
            logger.warning(f"帧抽取失败: {e}")
            return False

    async def _get_video_duration(self, video_path: str) -> float:
        """获取视频时长"""
        cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            video_path
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
            return float(stdout.decode().strip())
        except (asyncio.TimeoutError, ValueError) as e:
            logger.warning(f"获取视频时长失败: {e}")
            return 0.0

    def cleanup_video(self, video_path: str):
        """清理视频文件"""
        try:
            if os.path.exists(video_path):
                os.remove(video_path)
                logger.debug(f"已清理视频: {video_path}")
        except Exception as e:
            logger.warning(f"清理视频失败: {e}")

    def cleanup_frames(self, frames: List[VideoFrame]):
        """清理帧文件"""
        for frame in frames:
            try:
                if os.path.exists(frame.frame_path):
                    os.remove(frame.frame_path)
            except Exception:
                pass
