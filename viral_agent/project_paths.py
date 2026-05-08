"""
仓库与数据目录解析（不依赖进程 cwd）

避免从子目录启动（如 backend/）、或 cwd 变化时读到另一套空的 datas/，
表现为「换国际版账号后历史全没了」——实为数据路径漂移。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

_REPO_ROOT: Optional[Path] = None
_DATA_ROOT: Optional[Path] = None


def get_repo_root() -> Path:
    global _REPO_ROOT
    if _REPO_ROOT is None:
        _REPO_ROOT = Path(__file__).resolve().parent.parent
    return _REPO_ROOT


def get_data_root() -> Path:
    """数据根目录：默认 <repo>/datas；可用 XHS_DATA_ROOT 或 DATA_ROOT 覆盖为绝对路径。"""
    global _DATA_ROOT
    if _DATA_ROOT is not None:
        return _DATA_ROOT
    env = (os.environ.get("XHS_DATA_ROOT") or os.environ.get("DATA_ROOT") or "").strip()
    if env:
        _DATA_ROOT = Path(env).expanduser().resolve()
    else:
        _DATA_ROOT = (get_repo_root() / "datas").resolve()
    return _DATA_ROOT


def reset_data_root_cache_for_tests() -> None:
    """仅测试用：切换环境变量后清空缓存。"""
    global _DATA_ROOT
    _DATA_ROOT = None
