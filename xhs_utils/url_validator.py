"""
视频URL验证工具
用于快速检查视频URL是否可访问
"""
import requests
from typing import Optional
from loguru import logger


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
