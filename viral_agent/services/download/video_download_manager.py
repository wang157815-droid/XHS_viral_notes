"""
视频下载统一管理器

提供统一的视频下载、缓存和生命周期管理，避免重复下载。
支持两种使用场景：
- VideoAIAnalyzer: 需要 bytes 数据转 base64
- AVSyncAnalyzer: 需要本地文件路径供 ffmpeg 处理
"""

import os
import asyncio
import hashlib
import aiohttp
import aiofiles
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse
from contextlib import asynccontextmanager
from loguru import logger


class VideoDownloadError(Exception):
    """视频下载错误"""
    pass


class VideoTooLargeError(VideoDownloadError):
    """视频过大错误"""
    pass


@dataclass
class VideoHandle:
    """视频资源句柄"""
    url: str
    local_path: Optional[str] = None
    bytes_data: Optional[bytes] = None
    size_bytes: int = 0
    is_cached: bool = False
    note_id: Optional[str] = None


class VideoDownloadManager:
    """
    视频下载统一管理器

    特性：
    1. 下载去重：同一 URL 只下载一次，并发请求共享结果
    2. 引用计数：安全清理，只有所有消费者释放后才删除
    3. 按需下载：根据 require_file/require_bytes 决定是否下载
    4. 大小限制：预检 + 流式检查，考虑 base64 膨胀
    5. 原子写入：临时文件 + 重命名，避免读取半文件
    """

    # 小红书 CDN 需要的 Headers
    DEFAULT_HEADERS = {
        'Referer': 'https://www.xiaohongshu.com/',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }

    def __init__(
        self,
        cache_dir: str = None,
        max_size_mb: int = 50,
        download_timeout: int = 60,
        max_concurrent: int = 2
    ):
        """
        初始化下载管理器

        Args:
            cache_dir: 缓存目录，默认 datas/video_cache
            max_size_mb: 最大视频大小（MB），考虑 base64 膨胀后的实际限制
            download_timeout: 下载超时时间（秒）
            max_concurrent: 最大并发下载数
        """
        self.cache_dir = cache_dir or os.path.join("datas", "video_cache")
        self.max_size_mb = max_size_mb
        # P2-fix: 移除内部 0.75 乘法，调用者已计算好最终限制值
        # 调用者应传入考虑 base64 膨胀后的实际限制（如 15MB）
        self.effective_max_bytes = int(max_size_mb * 1024 * 1024)
        self.download_timeout = download_timeout

        # 并发控制
        self._download_semaphore = asyncio.Semaphore(max_concurrent)
        self._locks: Dict[str, asyncio.Lock] = {}
        self._in_progress: Dict[str, asyncio.Future] = {}

        # 引用计数和缓存
        self._ref_counts: Dict[str, int] = {}
        self._cache: Dict[str, str] = {}  # cache_key -> local_path

        # 确保缓存目录存在
        os.makedirs(self.cache_dir, exist_ok=True)

        logger.info(
            f"VideoDownloadManager 初始化: "
            f"缓存目录={self.cache_dir}, "
            f"大小限制={max_size_mb}MB, "
            f"并发={max_concurrent}"
        )

    def _get_cache_key(self, url: str, note_id: str = None) -> str:
        """
        生成稳定的缓存键

        策略：优先用 note_id，否则用 URL 的 path 部分 hash
        """
        if note_id:
            return f"video_{note_id}"

        # 解析 URL，只取 path 部分（忽略 query params 变化）
        parsed = urlparse(url)
        path_hash = hashlib.md5(parsed.path.encode()).hexdigest()[:12]
        return f"video_{path_hash}"

    def _get_cache_path(self, cache_key: str) -> str:
        """获取缓存文件路径"""
        return os.path.join(self.cache_dir, f"{cache_key}.mp4")

    async def acquire(
        self,
        url: str,
        backup_urls: List[Dict[str, Any]] = None,
        require_file: bool = False,
        require_bytes: bool = False,
        note_id: str = None
    ) -> VideoHandle:
        """
        获取视频资源句柄（引用计数 +1）

        Args:
            url: 主视频 URL
            backup_urls: 备选 URL 列表
            require_file: 是否需要本地文件（AVSync 场景）
            require_bytes: 是否需要 bytes 数据（AI base64 场景）
            note_id: 笔记 ID（用于生成稳定的缓存键）

        Returns:
            VideoHandle 视频资源句柄
        """
        cache_key = self._get_cache_key(url, note_id)

        # 不需要下载的情况
        if not require_file and not require_bytes:
            logger.debug(f"不需要下载，直接返回 URL: {cache_key}")
            return VideoHandle(url=url, note_id=note_id)

        # 需要下载
        local_path = await self._ensure_downloaded(url, backup_urls, cache_key)

        # 增加引用计数
        self._ref_counts[cache_key] = self._ref_counts.get(cache_key, 0) + 1
        logger.debug(f"引用计数 +1: {cache_key} = {self._ref_counts[cache_key]}")

        # 构建结果
        handle = VideoHandle(
            url=url,
            local_path=local_path if require_file else None,
            size_bytes=os.path.getsize(local_path) if local_path else 0,
            is_cached=cache_key in self._cache,
            note_id=note_id
        )

        # 如果需要 bytes，从文件读取
        if require_bytes and local_path:
            async with aiofiles.open(local_path, 'rb') as f:
                handle.bytes_data = await f.read()
            handle.local_path = local_path  # bytes 场景也保留 path

        return handle

    def release(self, url: str, note_id: str = None):
        """
        释放视频资源（引用计数 -1，归零时清理）

        Args:
            url: 视频 URL
            note_id: 笔记 ID
        """
        cache_key = self._get_cache_key(url, note_id)

        if cache_key not in self._ref_counts:
            return

        self._ref_counts[cache_key] -= 1
        logger.debug(f"引用计数 -1: {cache_key} = {self._ref_counts[cache_key]}")

        if self._ref_counts[cache_key] <= 0:
            # 所有消费者都释放了，可以安全清理
            self._cleanup_file(cache_key)
            del self._ref_counts[cache_key]

    @asynccontextmanager
    async def video_context(
        self,
        url: str,
        backup_urls: List[Dict[str, Any]] = None,
        require_file: bool = False,
        require_bytes: bool = False,
        note_id: str = None
    ):
        """
        上下文管理器，自动管理视频生命周期

        用法：
            async with manager.video_context(url, require_file=True) as handle:
                # 使用 handle.local_path
                pass
            # 退出时自动 release
        """
        handle = await self.acquire(url, backup_urls, require_file, require_bytes, note_id)
        try:
            yield handle
        finally:
            self.release(url, note_id)

    async def _ensure_downloaded(
        self,
        url: str,
        backup_urls: List[Dict[str, Any]],
        cache_key: str
    ) -> str:
        """
        确保视频已下载（去重 + 并发控制）

        Returns:
            本地文件路径
        """
        cache_path = self._get_cache_path(cache_key)

        # 1. 检查缓存
        if cache_key in self._cache and os.path.exists(cache_path):
            size = os.path.getsize(cache_path)
            if size > 1000:  # 有效文件
                logger.debug(f"缓存命中: {cache_key} ({size/1024:.1f}KB)")
                return cache_path

        # 2. 检查是否正在下载
        if cache_key in self._in_progress:
            logger.debug(f"等待正在进行的下载: {cache_key}")
            return await self._in_progress[cache_key]

        # 3. 获取 URL 级别锁
        if cache_key not in self._locks:
            self._locks[cache_key] = asyncio.Lock()

        async with self._locks[cache_key]:
            # 双重检查
            if cache_key in self._in_progress:
                return await self._in_progress[cache_key]

            # 再次检查缓存（可能其他任务刚刚下载完成）
            if os.path.exists(cache_path) and os.path.getsize(cache_path) > 1000:
                self._cache[cache_key] = cache_path
                return cache_path

            # 4. 创建下载任务
            # P2-fix: 使用 asyncio.Task 替代 Future，避免 "Future exception was never retrieved" 警告
            # Task 会自动记录未处理的异常，而不是在被垃圾回收时抛出警告
            async def _do_download():
                async with self._download_semaphore:
                    return await self._download_with_retry(url, backup_urls, cache_path)

            download_task = asyncio.create_task(_do_download())
            self._in_progress[cache_key] = download_task

            try:
                result = await download_task
                self._cache[cache_key] = result
                return result
            except Exception as e:
                logger.warning(f"下载任务失败: {cache_key}, {type(e).__name__}: {e}")
                raise
            finally:
                self._in_progress.pop(cache_key, None)

    async def _download_with_retry(
        self,
        url: str,
        backup_urls: List[Dict[str, Any]],
        target_path: str
    ) -> str:
        """
        带多源重试的下载

        Returns:
            本地文件路径
        """
        # 构建 URL 列表
        urls_to_try = [url]
        if backup_urls:
            for item in backup_urls[:2]:  # 最多尝试 2 个备选
                if isinstance(item, dict) and item.get('url'):
                    urls_to_try.append(item['url'])
                elif isinstance(item, str):
                    urls_to_try.append(item)

        last_error = None
        for i, try_url in enumerate(urls_to_try):
            try:
                logger.info(f"下载视频 [{i+1}/{len(urls_to_try)}]: {try_url[:60]}...")
                await self._download_single(try_url, target_path)
                logger.success(f"下载成功: {os.path.getsize(target_path)/1024/1024:.1f}MB")
                return target_path
            except Exception as e:
                last_error = e
                logger.warning(f"下载失败 [{i+1}]: {e}")
                continue

        raise VideoDownloadError(f"所有 URL 下载失败: {last_error}")

    async def _download_single(self, url: str, target_path: str):
        """
        下载单个视频（带大小检查和原子写入）
        """
        temp_path = target_path + ".tmp"
        timeout = aiohttp.ClientTimeout(total=self.download_timeout)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # 1. 预检大小（HEAD 请求）
                try:
                    async with session.head(url, headers=self.DEFAULT_HEADERS) as resp:
                        content_length = int(resp.headers.get('Content-Length', 0))
                        if content_length > self.effective_max_bytes:
                            raise VideoTooLargeError(
                                f"视频过大: {content_length/1024/1024:.1f}MB > "
                                f"{self.effective_max_bytes/1024/1024:.1f}MB"
                            )
                except aiohttp.ClientError:
                    pass  # HEAD 失败不影响下载

                # 2. 流式下载
                async with session.get(url, headers=self.DEFAULT_HEADERS) as resp:
                    if resp.status != 200:
                        raise VideoDownloadError(f"HTTP {resp.status}")

                    downloaded = 0
                    async with aiofiles.open(temp_path, 'wb') as f:
                        async for chunk in resp.content.iter_chunked(8192):
                            downloaded += len(chunk)
                            if downloaded > self.effective_max_bytes:
                                raise VideoTooLargeError(
                                    f"下载中超出限制: {downloaded/1024/1024:.1f}MB"
                                )
                            await f.write(chunk)

                # 3. 原子重命名
                os.rename(temp_path, target_path)

        except Exception:
            # 清理临时文件
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    def _cleanup_file(self, cache_key: str):
        """清理缓存文件"""
        cache_path = self._get_cache_path(cache_key)
        if os.path.exists(cache_path):
            try:
                os.remove(cache_path)
                logger.debug(f"清理缓存文件: {cache_key}")
            except OSError as e:
                logger.warning(f"清理缓存文件失败: {cache_key} - {e}")

        if cache_key in self._cache:
            del self._cache[cache_key]

    def cleanup_all(self):
        """清理所有缓存"""
        for cache_key in list(self._cache.keys()):
            self._cleanup_file(cache_key)
        self._ref_counts.clear()
        logger.info("已清理所有视频缓存")

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        total_size = 0
        for cache_key, path in self._cache.items():
            if os.path.exists(path):
                total_size += os.path.getsize(path)

        return {
            'cached_count': len(self._cache),
            'total_size_mb': total_size / 1024 / 1024,
            'active_refs': dict(self._ref_counts),
            'in_progress': len(self._in_progress)
        }
