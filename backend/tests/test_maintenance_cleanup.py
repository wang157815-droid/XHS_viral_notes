from __future__ import annotations

import os
import time
from pathlib import Path

import pytest


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_maintenance_stats_scan_real_cache_targets(tmp_path: Path):
    from backend.app.infrastructure.queue.tasks.cleanup import get_maintenance_stats_snapshot

    _write(tmp_path / "datas" / "video_cache" / "v.mp4", b"v" * 10)
    _write(tmp_path / "datas" / "av_sync_cache" / "audio" / "a.wav", b"a" * 20)
    _write(tmp_path / "datas" / "cover_cache" / "c.jpg", b"c" * 30)
    _write(tmp_path / "datas" / "users" / "u1" / "cover_cache" / "u.jpg", b"u" * 40)
    _write(tmp_path / "browser_data" / "xhs_u1" / "Default" / "Cache" / "Cache_Data" / "b", b"b" * 50)
    _write(tmp_path / "browser_data" / "xhs_u1" / "Default" / "Cookies", b"cookie" * 10)

    stats = get_maintenance_stats_snapshot(tmp_path)

    assert stats["video_cache_bytes"] == 10
    assert stats["analysis_cache_bytes"] == 90
    assert stats["browser_data_bytes"] == 50


@pytest.mark.asyncio
async def test_clean_browser_cache_keeps_login_state_files(tmp_path: Path):
    from backend.app.infrastructure.queue.tasks.cleanup import clean_cache_target

    cache_file = tmp_path / "browser_data" / "xhs_u1" / "Default" / "Cache" / "Cache_Data" / "old"
    cookie_file = tmp_path / "browser_data" / "xhs_u1" / "Default" / "Cookies"
    _write(cache_file, b"cache")
    _write(cookie_file, b"cookie")

    old = time.time() - 10 * 86400
    os.utime(cache_file, (old, old))
    os.utime(cookie_file, (old, old))

    stats = await clean_cache_target("browser_data", repo_root=tmp_path, retention_days=0)

    assert stats["deleted_count"] == 1
    assert not cache_file.exists()
    assert cookie_file.exists()
