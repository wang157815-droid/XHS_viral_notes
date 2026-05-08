"""
临时文件清理任务（阶段 4.α）。

cron 每天凌晨 3 点跑,清理:
- datas/video_cache/*        超过 3 天的视频文件
- datas/av_sync_cache/*      超过 3 天的帧/音频中间产物
- browser_data/*/Cache/*     Playwright 浏览器 HTTP 缓存（不删整个 user_data_dir！）

保护措施:
- 不碰 cookies.json / datas/users/*/viral_analysis/ 这些用户数据
- 大小日志化,便于观测
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from loguru import logger


_BROWSER_DATA_ROOT = "browser_data"
_BROWSER_CACHE_RELATIVE_DIRS = [
    Path("Default") / "Cache",
    Path("Default") / "Code Cache",
    Path("Default") / "GPUCache",
    Path("Default") / "Service Worker" / "ScriptCache",
    Path("Default") / "Shared Dictionary" / "cache",
    Path("Default") / "DawnGraphiteCache",
    Path("Default") / "DawnWebGPUCache",
    Path("ShaderCache"),
    Path("GrShaderCache"),
    Path("GraphiteDawnCache"),
    Path("component_crx_cache"),
    Path("extensions_crx_cache"),
]
_BROWSER_CACHE_RETENTION_DAYS = 7

_CACHE_TARGETS: Dict[str, Dict[str, Any]] = {
    "video_cache": {
        "retention_days": 3,
        "paths": ["datas/video_cache"],
    },
    "analysis_cache": {
        "retention_days": 3,
        "paths": [
            "datas/av_sync_cache",
            "datas/cover_cache",
            "datas/users/*/cover_cache",
        ],
    },
}


def get_repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _is_within(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _expand_path_specs(repo_root: Path, specs: Iterable[str]) -> List[Path]:
    paths: List[Path] = []
    for spec in specs:
        if "*" in spec:
            candidates = repo_root.glob(spec)
        else:
            candidates = [repo_root / spec]
        for path in candidates:
            if _is_within(repo_root, path):
                paths.append(path)
    return paths


def _browser_cache_dirs(repo_root: Path) -> List[Path]:
    browser_root = repo_root / _BROWSER_DATA_ROOT
    if not browser_root.exists() or not browser_root.is_dir():
        return []
    paths: List[Path] = []
    for user_dir in browser_root.iterdir():
        if not user_dir.is_dir() or not _is_within(repo_root, user_dir):
            continue
        for rel in _BROWSER_CACHE_RELATIVE_DIRS:
            paths.append(user_dir / rel)
    return paths


def get_cache_target_paths(target: str, repo_root: Optional[Path] = None) -> List[Path]:
    root = repo_root or get_repo_root()
    if target == "browser_data":
        return _browser_cache_dirs(root)
    config = _CACHE_TARGETS.get(target)
    if not config:
        raise ValueError(f"未知清理目标: {target}")
    return _expand_path_specs(root, config["paths"])


def _dir_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except Exception:
            return 0
    total = 0
    try:
        for entry in path.rglob("*"):
            try:
                if entry.is_file():
                    total += entry.stat().st_size
            except Exception:
                continue
    except Exception:
        return total
    return total


def get_maintenance_stats_snapshot(repo_root: Optional[Path] = None) -> Dict[str, int]:
    root = repo_root or get_repo_root()
    return {
        "video_cache_bytes": sum(_dir_size_bytes(p) for p in get_cache_target_paths("video_cache", root)),
        "analysis_cache_bytes": sum(_dir_size_bytes(p) for p in get_cache_target_paths("analysis_cache", root)),
        "browser_data_bytes": sum(_dir_size_bytes(p) for p in get_cache_target_paths("browser_data", root)),
    }


async def clean_cache_target(
    target: str,
    repo_root: Optional[Path] = None,
    retention_days: Optional[int] = 0,
) -> Dict[str, Any]:
    root = repo_root or get_repo_root()
    if target == "browser_data":
        default_retention = _BROWSER_CACHE_RETENTION_DAYS
    else:
        config = _CACHE_TARGETS.get(target)
        if not config:
            raise ValueError(f"未知清理目标: {target}")
        default_retention = int(config["retention_days"])

    days = default_retention if retention_days is None else retention_days
    details: List[Dict[str, Any]] = []
    total_deleted_count = 0
    total_deleted_bytes = 0
    for path in get_cache_target_paths(target, root):
        if not _is_within(root, path):
            continue
        stats = await _clean_dir(path, days)
        try:
            stats["path"] = str(path.relative_to(root))
        except ValueError:
            stats["path"] = str(path)
        details.append(stats)
        total_deleted_count += int(stats.get("deleted_count", 0) or 0)
        total_deleted_bytes += int(stats.get("deleted_bytes", 0) or 0)

    return {
        "target": target,
        "deleted_count": total_deleted_count,
        "deleted_bytes": total_deleted_bytes,
        "details": details,
        "retention_days": days,
    }


async def cleanup_tmp(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """ARQ cron：每日临时文件清理。"""
    start = datetime.now(timezone.utc)
    logger.info(f"[arq.cleanup_tmp] 开始,时间={start.isoformat()}")

    details: List[Dict[str, Any]] = []
    total_deleted_bytes = 0

    repo_root = get_repo_root()

    for target in ("video_cache", "analysis_cache", "browser_data"):
        stats = await clean_cache_target(target, repo_root=repo_root, retention_days=None)
        details.extend(stats.get("details", []))
        total_deleted_bytes += int(stats.get("deleted_bytes", 0) or 0)

    end = datetime.now(timezone.utc)
    summary = {
        "started_at": start.isoformat(),
        "finished_at": end.isoformat(),
        "duration_sec": (end - start).total_seconds(),
        "total_deleted_bytes": total_deleted_bytes,
        "total_deleted_mb": round(total_deleted_bytes / 1024 / 1024, 2),
        "details": details,
    }
    await _write_last_cleanup(summary)
    logger.info(f"[arq.cleanup_tmp] 完成: 释放 {summary['total_deleted_mb']}MB")
    return summary


async def _clean_dir(path: Path, retention_days: int) -> Dict[str, Any]:
    """删除 path 下 mtime 超过 retention_days 的文件,返回统计。"""
    if not path.exists() or not path.is_dir():
        return {"exists": False, "deleted_count": 0, "deleted_bytes": 0}

    threshold = time.time() - retention_days * 86400
    deleted_count = 0
    deleted_bytes = 0

    for entry in path.rglob("*"):
        try:
            if not entry.is_file():
                continue
            stat = entry.stat()
            if stat.st_mtime > threshold:
                continue  # 还在保留期
            size = stat.st_size
            entry.unlink()
            deleted_count += 1
            deleted_bytes += size
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[cleanup] 删除 {entry} 失败: {exc}")

    return {
        "exists": True,
        "deleted_count": deleted_count,
        "deleted_bytes": deleted_bytes,
        "retention_days": retention_days,
    }


async def _write_last_cleanup(summary: Dict[str, Any]) -> None:
    try:
        from ...cache.redis_client import get_redis

        client = await get_redis()
        await client.setex(
            "maintenance:last_cleanup",
            86400 * 7,
            json.dumps(summary, ensure_ascii=False),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[cleanup._write] 写 Redis 失败: {exc}")


async def read_last_cleanup() -> Optional[Dict[str, Any]]:
    try:
        from ...cache.redis_client import get_redis

        client = await get_redis()
        raw = await client.get("maintenance:last_cleanup")
        if not raw:
            return None
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
