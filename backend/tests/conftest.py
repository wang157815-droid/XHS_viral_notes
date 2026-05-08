"""
pytest 配置：
- 把 repo root 加入 sys.path,使 `backend.app` 可导入
- 自动在测试环境禁用 Redis/pgvector 缓存层（没有 Redis/Postgres 服务时避免连接超时拖慢测试）
- 如果单测里需要测缓存命中,显式 monkeypatch `_CACHE_ENABLED=True` 再启用
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# 测试环境默认禁用 Redis/pgvector 缓存层（避免连不上服务时每个测试都超时 5-10 秒）
# 需要测缓存命中行为的用例在自己 fixture 里 monkeypatch 开启
os.environ.setdefault("CRAWLER_CACHE_ENABLED", "false")
os.environ.setdefault("BACKLOG_BACKEND", "memory")
os.environ.setdefault("TASK_RUNNER", "inprocess")
os.environ.setdefault("CRAWLER_ENRICH_DETAILS", "false")  # 测试 mock 不真调详情接口
os.environ.setdefault("REDMUSE_SKIP_STARTUP_CHECKS", "true")


@pytest.fixture(autouse=False)
def enable_cache_for_test(monkeypatch):
    """需要验证缓存命中的测试显式使用此 fixture。"""
    from backend.app.application.agents import crawler_agent

    monkeypatch.setattr(crawler_agent, "_CACHE_ENABLED", True)
    yield


@pytest.fixture(autouse=True)
def isolate_system_settings_store(tmp_path_factory, monkeypatch):
    """自动把 SystemSettingsStore 单例隔离到 tmp 路径,防测试读写真实 datas/config/system_settings.json。

    真实文件可能被开发环境(前端 Toggle)改成 video_analysis_enabled=False,导致
    VideoAnalysisAgent 的测试误走 disabled 分支。本 fixture 保证测试隔离。
    """
    try:
        from backend.app.services import system_settings_store as mod
    except Exception:
        yield
        return

    tmp_dir = tmp_path_factory.mktemp("sysset")
    isolated = mod.SystemSettingsStore(store_file=str(tmp_dir / "system_settings.json"))
    monkeypatch.setattr(mod, "_default_store", isolated, raising=False)
    monkeypatch.setattr(mod, "get_system_settings_store", lambda: isolated)
    yield


@pytest.fixture(autouse=True)
def isolate_focus_keywords_store(tmp_path_factory, monkeypatch):
    """同理隔离 FocusKeywordsStore,避免读 datas/config/focus_keywords.json 串味。"""
    try:
        from backend.app.services import focus_keywords_store as mod
    except Exception:
        yield
        return

    tmp_dir = tmp_path_factory.mktemp("fockw")
    isolated = mod.FocusKeywordsStore(store_file=str(tmp_dir / "focus_keywords.json"))
    monkeypatch.setattr(mod, "_default_store", isolated, raising=False)
    monkeypatch.setattr(mod, "get_focus_keywords_store", lambda: isolated)
    yield


@pytest.fixture(autouse=True)
def isolate_viral_taxonomy_loader(tmp_path_factory, monkeypatch):
    """4.3pre.1: 同理隔离 ViralTaxonomyLoader。"""
    try:
        from backend.app.services import viral_taxonomy_loader as mod
    except Exception:
        yield
        return

    tmp_dir = tmp_path_factory.mktemp("taxonomy")
    isolated = mod.ViralTaxonomyLoader(store_file=str(tmp_dir / "viral_taxonomy.json"))
    monkeypatch.setattr(mod, "_default_loader", isolated, raising=False)
    monkeypatch.setattr(mod, "get_viral_taxonomy_loader", lambda: isolated)
    yield
