"""
音频提取器
从视频中分离音频轨道
"""
import os
import asyncio
import subprocess
from pathlib import Path
from loguru import logger


class AudioExtractionError(Exception):
    """音频提取错误"""
    pass


class AudioExtractor:
    """音频提取器"""

    def __init__(self, cache_dir: str = None):
        """
        初始化音频提取器

        Args:
            cache_dir: 缓存目录
        """
        self.cache_dir = cache_dir or os.path.join("datas", "av_sync_cache")
        self.ffmpeg_available = self._check_ffmpeg()
        os.makedirs(os.path.join(self.cache_dir, "audio"), exist_ok=True)

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

    async def extract_audio(
        self,
        video_path: str,
        sample_rate: int = 16000,
        channels: int = 1
    ) -> str:
        """
        从视频提取音频

        Args:
            video_path: 视频文件路径
            sample_rate: 采样率（16kHz 适合大多数 ASR）
            channels: 声道数（单声道）

        Returns:
            音频文件路径

        Raises:
            AudioExtractionError: 提取失败
        """
        if not self.ffmpeg_available:
            raise AudioExtractionError("ffmpeg 不可用")

        if not os.path.exists(video_path):
            raise AudioExtractionError(f"视频文件不存在: {video_path}")

        audio_dir = os.path.join(self.cache_dir, "audio")
        video_name = Path(video_path).stem
        audio_path = os.path.join(audio_dir, f"{video_name}.wav")

        # 如果已存在且有效，直接返回
        if os.path.exists(audio_path) and os.path.getsize(audio_path) > 1000:
            logger.debug(f"使用缓存音频: {audio_path}")
            return audio_path

        cmd = [
            'ffmpeg', '-y',
            '-i', video_path,
            '-vn',                    # 不处理视频
            '-acodec', 'pcm_s16le',   # PCM 格式
            '-ar', str(sample_rate),  # 采样率
            '-ac', str(channels),     # 声道数
            audio_path
        ]

        logger.info(f"正在提取音频: {video_path}")

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=60)

            if process.returncode != 0:
                error_msg = stderr.decode()[:200] if stderr else "未知错误"
                raise AudioExtractionError(f"音频提取失败: {error_msg}")

            if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 100:
                raise AudioExtractionError("音频文件生成失败或为空")

            logger.success(f"✅ 音频提取成功: {audio_path}")
            return audio_path

        except asyncio.TimeoutError:
            raise AudioExtractionError("音频提取超时")

    def cleanup_audio(self, audio_path: str):
        """清理音频文件"""
        try:
            if os.path.exists(audio_path):
                os.remove(audio_path)
                logger.debug(f"已清理音频: {audio_path}")
        except Exception as e:
            logger.warning(f"清理音频失败: {e}")
