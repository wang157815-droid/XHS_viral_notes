"""
CoverImageFetcher: 异步并发下载 XHS 封面图 + Pillow 压缩,供 openpyxl XLImage 嵌入。

4.3pre.5 设计要点:
- Semaphore=10 限流(环境变量 EXCEL_IMG_CONCURRENCY 可覆盖)
- 单张 5s timeout(EXCEL_IMG_TIMEOUT 可覆盖)
- XHS CDN 对直链强 Referer 校验,统一带 Referer=https://www.xiaohongshu.com + UA
- 失败返回 None,caller 侧负责"留空"兜底
- 同 URL 去重(多个样本 sheet 里同一条 note 的封面只下一次)
- 图片 downscale 到 max_side=200 px,JPEG 质量 75,减小 xlsx 体积
"""

from __future__ import annotations

import asyncio
import io
import os
from typing import Dict, Iterable, Optional

import aiohttp
from loguru import logger


_DEFAULT_CONCURRENCY = int(os.getenv("EXCEL_IMG_CONCURRENCY", "10"))
_DEFAULT_TIMEOUT = float(os.getenv("EXCEL_IMG_TIMEOUT", "5.0"))
_DEFAULT_MAX_SIDE = int(os.getenv("EXCEL_IMG_MAX_SIDE", "200"))
_DEFAULT_JPEG_QUALITY = int(os.getenv("EXCEL_IMG_JPEG_QUALITY", "75"))

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.xiaohongshu.com/",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}


class CoverImageFetcher:
    """异步并发下载 + 压缩。

    典型用法:
        fetcher = CoverImageFetcher()
        cache = await fetcher.fetch_all(urls)
        # cache: Dict[url, BytesIO | None]
        # None 表示下载失败(caller 负责 cell 留空兜底)
    """

    def __init__(
        self,
        *,
        concurrency: int = _DEFAULT_CONCURRENCY,
        timeout: float = _DEFAULT_TIMEOUT,
        max_side: int = _DEFAULT_MAX_SIDE,
    ) -> None:
        self._concurrency = max(1, concurrency)
        self._timeout = max(1.0, timeout)
        self._max_side = max(64, max_side)

    async def fetch_all(
        self, urls: Iterable[str]
    ) -> Dict[str, Optional[io.BytesIO]]:
        """并发下载一批 URL。

        Returns:
            Dict[url, BytesIO | None]
                - BytesIO: 成功下载 + 压缩后的 JPEG 字节流(可直接喂给 openpyxl XLImage)
                - None:    下载或解码失败
        """
        unique_urls = [u for u in {u.strip() for u in urls if u and u.strip()}]
        if not unique_urls:
            return {}

        semaphore = asyncio.Semaphore(self._concurrency)
        timeout_cfg = aiohttp.ClientTimeout(total=self._timeout)

        # 一个 ClientSession 服务所有并发请求,连接池复用
        async with aiohttp.ClientSession(
            timeout=timeout_cfg, headers=_HEADERS
        ) as session:
            tasks = [
                self._fetch_one(session, semaphore, url) for url in unique_urls
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        cache: Dict[str, Optional[io.BytesIO]] = {}
        for url, result in zip(unique_urls, results):
            if isinstance(result, Exception):
                logger.debug(f"CoverImageFetcher 异常 url={url[:80]} err={result}")
                cache[url] = None
            else:
                cache[url] = result
        return cache

    async def _fetch_one(
        self,
        session: aiohttp.ClientSession,
        semaphore: asyncio.Semaphore,
        url: str,
    ) -> Optional[io.BytesIO]:
        async with semaphore:
            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.debug(
                            f"CoverImageFetcher 非 200 status={resp.status} url={url[:80]}"
                        )
                        return None
                    raw = await resp.read()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.debug(f"CoverImageFetcher HTTP 异常 url={url[:80]} err={exc}")
                return None

        if not raw:
            return None

        try:
            compressed = await asyncio.to_thread(
                self._downscale, raw, self._max_side
            )
        except Exception as exc:  # noqa: BLE001 - 压缩失败兜底原图
            logger.debug(f"CoverImageFetcher 压缩失败,使用原图 url={url[:80]} err={exc}")
            compressed = raw

        stream = io.BytesIO(compressed)
        stream.seek(0)
        return stream

    @staticmethod
    def _downscale(raw: bytes, max_side: int) -> bytes:
        """把原图压到 max_side 像素(长边),输出 JPEG 减小体积。

        Pillow 未安装时直接 return 原图(graceful degrade)。
        """
        try:
            from PIL import Image
        except ImportError:
            return raw

        try:
            img = Image.open(io.BytesIO(raw))
            img.load()
        except Exception:
            return raw

        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        width, height = img.size
        if max(width, height) > max_side:
            scale = max_side / float(max(width, height))
            new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
            img = img.resize(new_size, Image.LANCZOS)

        out = io.BytesIO()
        img.save(out, format="JPEG", quality=_DEFAULT_JPEG_QUALITY, optimize=True)
        return out.getvalue()


__all__ = ["CoverImageFetcher"]
