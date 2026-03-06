"""媒体文件下载工具（支持并行下载）

复用 cover_analyzer.py 的 ThreadPoolExecutor + as_completed 模式。
并发度：
  - 笔记内图片：max_workers=5（图集最多 9 张）
  - 多笔记并行：max_workers=3（保守，避免 CDN 压力）
  - 视频不并行（带宽瓶颈，流式下载并行收益小）
"""

import json
import os

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from loguru import logger
from retry import retry

from xhs_utils.data_util import norm_str, check_and_create_path, save_note_detail

# ── 并发配置 ──────────────────────────────────
MAX_IMAGES_WORKERS = 5
MAX_NOTES_WORKERS = 3


# ── 单文件下载（带 retry）──────────────────────

@retry(tries=3, delay=1, backoff=2)
def download_media(path: str, name: str, url: str, media_type: str) -> bool:
    """下载单个媒体文件。

    Args:
        path: 保存目录
        name: 文件名（不含扩展名）
        url: 下载 URL
        media_type: 'image' 或 'video'

    Returns:
        True 表示成功
    """
    if media_type == 'image':
        resp = requests.get(url, timeout=(5, 30))
        resp.raise_for_status()
        content = resp.content
        with open(os.path.join(path, f'{name}.jpg'), mode="wb") as f:
            f.write(content)
    elif media_type == 'video':
        res = requests.get(url, stream=True, timeout=(5, 60))
        res.raise_for_status()
        chunk_size = 1024 * 1024
        with open(os.path.join(path, f'{name}.mp4'), mode="wb") as f:
            for data in res.iter_content(chunk_size=chunk_size):
                f.write(data)
    return True


# ── 图片并行下载 ──────────────────────────────

def _download_images_parallel(
    save_path: str,
    image_list: list[str],
    max_workers: int = MAX_IMAGES_WORKERS,
) -> int:
    """并行下载图片列表，返回成功数量。"""
    if not image_list:
        return 0

    success_count = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(download_media, save_path, f'image_{i}', url, 'image'): i
            for i, url in enumerate(image_list)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                future.result()
                success_count += 1
            except Exception as e:
                logger.warning(f"图片 {idx} 下载失败: {e}")

    return success_count


# ── 单笔记下载（图片并行）──────────────────────

def download_note(note_info: dict, path: str, save_choice: str) -> str:
    """下载单个笔记的所有媒体（图片并行下载）。

    Returns:
        笔记保存路径
    """
    note_id = note_info['note_id']
    user_id = note_info['user_id']
    title = norm_str(note_info['title'])[:40]
    nickname = norm_str(note_info['nickname'])[:20]
    if title.strip() == '':
        title = '无标题'

    save_path = f'{path}/{nickname}_{user_id}/{title}_{note_id}'
    check_and_create_path(save_path)

    with open(f'{save_path}/info.json', mode='w', encoding='utf-8') as f:
        f.write(json.dumps(note_info) + '\n')

    note_type = note_info['note_type']
    save_note_detail(note_info, save_path)

    if note_type == '图集' and save_choice in ['media', 'media-image', 'all']:
        count = _download_images_parallel(save_path, note_info['image_list'])
        total = len(note_info['image_list'])
        logger.info(f"[{note_id}] 图集下载完成: {count}/{total}")

    elif note_type == '视频' and save_choice in ['media', 'media-video', 'all']:
        try:
            download_media(save_path, 'cover', note_info['video_cover'], 'image')
        except Exception as e:
            logger.warning(f"[{note_id}] 封面下载失败: {e}")
        try:
            download_media(save_path, 'video', note_info['video_addr'], 'video')
        except Exception as e:
            logger.warning(f"[{note_id}] 视频下载失败: {e}")

    return save_path


# ── 多笔记并行下载 ────────────────────────────

def download_notes_parallel(
    note_list: list[dict],
    path: str,
    save_choice: str,
    max_workers: int = MAX_NOTES_WORKERS,
) -> list[str]:
    """并行下载多个笔记的媒体文件。

    Returns:
        成功保存的路径列表
    """
    if not note_list:
        return []
    if save_choice not in ['all', 'media', 'media-video', 'media-image']:
        return []

    logger.info(f"并行下载 {len(note_list)} 个笔记（并发数: {max_workers}）...")

    saved_paths = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_note = {
            executor.submit(download_note, info, path, save_choice): info
            for info in note_list
        }
        completed = 0
        for future in as_completed(future_to_note):
            info = future_to_note[future]
            completed += 1
            try:
                result_path = future.result()
                saved_paths.append(result_path)
            except Exception as e:
                logger.warning(f"笔记 {info.get('note_id', '?')} 下载失败: {e}")
            if completed % 5 == 0 or completed == len(note_list):
                logger.info(f"下载进度: {completed}/{len(note_list)}")

    logger.info(f"下载完成: 成功 {len(saved_paths)}/{len(note_list)}")
    return saved_paths
