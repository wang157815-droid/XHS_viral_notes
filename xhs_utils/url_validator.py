"""
视频URL验证工具
用于快速检查视频URL是否可访问

功能：
- validate_video_url: 验证单个URL可访问性
- get_best_video_url: 获取第一个可访问的URL
- get_best_video_url_for_ai: 为AI分析选择最优URL（优先压缩版）
- try_get_compressed_url: 尝试获取压缩版URL（_259 → _130）
"""
import requests
from typing import Optional, Dict
from loguru import logger


# ============================================================================
# 压缩版URL缓存（避免重复HEAD请求）
# 格式: {原始URL: 压缩版URL 或 None}
# ============================================================================
_compressed_url_cache: Dict[str, Optional[str]] = {}


def validate_video_url(url: str, timeout: int = 3) -> bool:
    """
    验证视频URL是否可访问

    Args:
        url: 视频URL
        timeout: 超时时间（秒）

    Returns:
        True表示URL可访问，False表示不可访问
    """
    if not url or not url.startswith('http'):
        return False

    try:
        # 使用HEAD请求，快速检查URL是否可访问
        headers = {
            'Referer': 'https://www.xiaohongshu.com/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        response = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)

        # 200（正常）和206（部分内容）都算成功
        if response.status_code in [200, 206]:
            return True

        # 记录失败原因（便于调试）
        if response.status_code in [403, 404, 410]:
            logger.debug(f"URL不可访问: {url} - 状态码 {response.status_code}")

        return False

    except requests.exceptions.Timeout:
        logger.debug(f"URL验证超时: {url}")
        return False
    except requests.exceptions.RequestException as e:
        logger.debug(f"URL验证失败: {url} - {e}")
        return False


def try_get_compressed_url(
    url: str,
    max_size_mb: float = 7.0,
    use_cache: bool = True
) -> Optional[str]:
    """
    尝试获取压缩版URL（_259.mp4 → _130.mp4）

    小红书CDN的URL结构：
    - 原版: http://sns-video-hw.xhscdn.com/stream/79/110/259/xxx_259.mp4
    - 压缩: http://sns-video-hw.xhscdn.com/stream/79/110/130/xxx_130.mp4
                                               ^^^           ^^^
                                             路径替换       后缀替换

    Args:
        url: 原始视频URL（通常是 _259.mp4）
        max_size_mb: 最大允许大小（MB），压缩版约4MB，若限制更严则跳过
        use_cache: 是否使用缓存（仅缓存成功结果，避免临时失败永久跳过）

    Returns:
        压缩版URL（如果可访问且符合大小限制），否则返回 None
    """
    # 压缩版预估大小约 4MB
    COMPRESSED_SIZE_MB = 4.0

    if not url or '_259.mp4' not in url:
        return None

    # 检查大小限制：如果 max_size_mb < 4MB，压缩版也不符合要求
    if max_size_mb < COMPRESSED_SIZE_MB:
        logger.debug(f"⏭️ 压缩版(~{COMPRESSED_SIZE_MB}MB)超过限制({max_size_mb}MB)，跳过")
        return None

    # 检查缓存（仅缓存成功结果）
    if use_cache and url in _compressed_url_cache:
        cached = _compressed_url_cache[url]
        logger.debug(f"📦 使用缓存的压缩版URL: {cached[:60]}...")
        return cached

    # 构造压缩版URL（同时替换路径和文件名）
    compressed_url = url.replace('/259/', '/130/').replace('_259.mp4', '_130.mp4')

    # 验证是否可访问
    logger.debug(f"🔍 尝试压缩版URL: {compressed_url[:60]}...")

    if validate_video_url(compressed_url):
        logger.info(f"✅ 发现可用压缩版URL（~4MB）: {compressed_url[:60]}...")
        if use_cache:
            _compressed_url_cache[url] = compressed_url
        return compressed_url
    else:
        # 不缓存失败结果，避免临时403导致永久跳过
        logger.debug(f"❌ 压缩版URL不可用，使用原版")
        return None


def clear_compressed_url_cache():
    """清空压缩版URL缓存"""
    global _compressed_url_cache
    _compressed_url_cache.clear()
    logger.debug("压缩版URL缓存已清空")


def get_best_video_url(video_urls: list, max_attempts: int = 3) -> Optional[str]:
    """
    从URL列表中找出第一个可访问的URL

    Args:
        video_urls: URL列表，按优先级排序
        max_attempts: 最大尝试次数

    Returns:
        第一个可访问的URL，如果都不可访问则返回None
    """
    if not video_urls:
        return None

    for i, url_info in enumerate(video_urls[:max_attempts]):
        # 兼容两种格式：字典或字符串
        url = url_info.get('url') if isinstance(url_info, dict) else url_info

        if validate_video_url(url):
            logger.info(f"找到可用URL（优先级{i+1}）: {url[:80]}...")
            return url

    logger.warning(f"尝试了{min(max_attempts, len(video_urls))}个URL，都不可访问")
    return None


def get_best_video_url_for_ai(
    video_urls: list,
    max_size_mb: float = 7.0,
    prefer_h264: bool = True,
    try_compressed: bool = True
) -> Optional[str]:
    """
    为 AI 分析选择最优的视频 URL（优先选择小尺寸压缩版）

    选择策略（按优先级）：
    1. 尝试获取压缩版 _130.mp4（从 _259.mp4 URL 转换）
    2. 选择符合大小限制的 URL
    3. 回退选择最小的可用 URL

    小红书视频 URL 后缀含义：
    - _130.mp4: H.264 压缩版，约 3-4MB（最适合 AI 分析）
    - _259.mp4: H.264 720p，约 5-10MB
    - _114.mp4: H.265 720p，约 20-30MB
    - _115.mp4: H.265 1080p，约 20-30MB（最大）

    Args:
        video_urls: URL 列表
        max_size_mb: 最大允许大小（MB），默认 7MB，超过此大小的URL会被跳过
        prefer_h264: 是否优先选择 H.264 编码（兼容性更好）
        try_compressed: 是否尝试获取压缩版URL（默认True）

    Returns:
        最优的视频 URL，如果都不合适则返回原始第一个
    """
    if not video_urls:
        return None

    # 定义 URL 后缀信息（优先级、预估大小、是否H.264）
    suffix_info = {
        '_130.mp4': {'priority': 1, 'size_mb': 4.0, 'is_h264': True},
        '_259.mp4': {'priority': 2, 'size_mb': 8.0, 'is_h264': True},
        '_114.mp4': {'priority': 3, 'size_mb': 25.0, 'is_h264': False},
        '_115.mp4': {'priority': 4, 'size_mb': 25.0, 'is_h264': False},
    }

    def get_url_string(url_info) -> Optional[str]:
        """安全提取 URL 字符串"""
        if isinstance(url_info, dict):
            return url_info.get('url')
        elif isinstance(url_info, str):
            return url_info
        return None

    def get_suffix_key(url: str) -> Optional[str]:
        """获取 URL 的后缀类型"""
        if not url:
            return None
        for suffix in suffix_info:
            if suffix in url:
                return suffix
        return None

    def get_priority(url_info) -> int:
        """计算 URL 优先级（考虑大小限制和编码偏好）"""
        url = get_url_string(url_info)
        suffix = get_suffix_key(url) if url else None

        if not suffix:
            return 99  # 未知后缀，最低优先级

        info = suffix_info[suffix]

        # 超过大小限制的，优先级大幅降低
        if info['size_mb'] > max_size_mb:
            return 50 + info['priority']

        # prefer_h264=True 时，H.265 优先级降低
        if prefer_h264 and not info['is_h264']:
            return 10 + info['priority']

        return info['priority']

    # ========================================================================
    # 第0步：尝试获取压缩版URL（_259 → _130）
    # 压缩版约4MB，若符合 max_size_mb 限制，可使用base64模式（时间戳精确）
    # ========================================================================
    if try_compressed:
        for url_info in video_urls:
            url = get_url_string(url_info)
            if url and '_259.mp4' in url:
                # 传入 max_size_mb，确保压缩版也符合大小限制
                compressed = try_get_compressed_url(url, max_size_mb=max_size_mb)
                if compressed:
                    # 压缩版可用且符合大小限制，直接返回
                    return compressed
        # 如果所有 _259.mp4 都没有压缩版（或不符合大小限制），继续原有逻辑
        logger.debug("未找到可用的压缩版URL，继续尝试原版")

    # 按优先级排序
    sorted_urls = sorted(video_urls, key=get_priority)

    # 尝试找到第一个符合条件的 URL
    for url_info in sorted_urls:
        url = get_url_string(url_info)
        if not url:
            continue

        suffix = get_suffix_key(url)
        if not suffix:
            continue

        info = suffix_info[suffix]

        # 硬性大小限制：跳过超过限制的 URL
        if info['size_mb'] > max_size_mb:
            logger.debug(f"跳过超大URL: {suffix} (~{info['size_mb']}MB > {max_size_mb}MB)")
            continue

        # 验证 URL 可访问性
        if validate_video_url(url):
            logger.info(f"✅ AI分析选择URL（{suffix}, ~{info['size_mb']}MB）: {url[:80]}...")
            return url

    # 如果没有符合大小限制的可用 URL，按预估大小排序选择最小的
    # （而不是按原始顺序，避免选到更大的高清版）
    logger.warning(f"⚠️ 无符合大小限制({max_size_mb}MB)的URL可用，选择预估最小的URL")

    # 按预估大小排序（已排序的 sorted_urls 就是按大小优先级排的）
    for url_info in sorted_urls:
        url = get_url_string(url_info)
        if url and validate_video_url(url):
            suffix = get_suffix_key(url)
            size = suffix_info.get(suffix, {}).get('size_mb', '?')
            logger.info(f"🔄 回退选择URL（{suffix}, ~{size}MB）: {url[:80]}...")
            return url

    # 最后兜底：返回列表中第一个有效URL
    for url_info in video_urls:
        url = get_url_string(url_info)
        if url:
            logger.warning(f"⚠️ 兜底选择第一个URL: {url[:80]}...")
            return url

    return None


def estimate_video_size_from_url(url: str) -> Optional[float]:
    """
    根据 URL 后缀估算视频大小（MB）

    Args:
        url: 视频 URL

    Returns:
        估算的大小（MB），无法估算返回 None
    """
    # 基于实测数据的估算
    size_estimates = {
        '_130.mp4': 4.0,    # H.264 压缩版
        '_259.mp4': 8.0,    # H.264 720p
        '_114.mp4': 25.0,   # H.265 720p
        '_115.mp4': 25.0,   # H.265 1080p
    }

    for suffix, size in size_estimates.items():
        if suffix in url:
            return size

    return None


def extract_url_info(url: str) -> dict:
    """
    从URL中提取信息（用于日志和调试）

    Args:
        url: 视频URL

    Returns:
        包含URL信息的字典
    """
    info = {
        'url': url,
        'domain': '',
        'stream_type': '',
        'is_backup': False
    }

    if not url:
        return info

    # 提取域名
    if 'sns-video-bd.xhscdn.com' in url:
        info['domain'] = 'primary'
    elif 'sns-bak-v' in url:
        info['domain'] = 'backup'
        info['is_backup'] = True

    # 提取流类型（从URL路径中推断）
    if '_259.mp4' in url:
        info['stream_type'] = 'H.264_720p'
    elif '_114.mp4' in url:
        info['stream_type'] = 'H.265_720p'
    elif '_115.mp4' in url:
        info['stream_type'] = 'H.265_1080p'
    elif 'sns-video-bd.xhscdn.com/' in url and '/' not in url.split('xhscdn.com/')[1]:
        info['stream_type'] = 'origin_key'

    return info
