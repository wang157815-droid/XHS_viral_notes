"""
视频下载统一管理模块

提供统一的视频下载、缓存和生命周期管理，避免重复下载。
支持 VideoAIAnalyzer（bytes）和 AVSyncAnalyzer（file path）两种使用场景。
"""

from .video_download_manager import (
    VideoDownloadManager,
    VideoHandle,
    VideoDownloadError,
    VideoTooLargeError
)

__all__ = [
    'VideoDownloadManager',
    'VideoHandle',
    'VideoDownloadError',
    'VideoTooLargeError'
]
