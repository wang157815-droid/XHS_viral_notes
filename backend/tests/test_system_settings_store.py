"""SystemSettingsStore 单测（阶段 4.α 补丁）。

验证：
- 空文件 → 返回默认值
- update 局部更新 + 深合并
- replace 完整替换
- get_crawler_schedule 快捷方法
- 损坏的 JSON → 优雅返回默认值
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def store(tmp_path):
    from backend.app.services.system_settings_store import SystemSettingsStore

    return SystemSettingsStore(store_file=str(tmp_path / "system_settings.json"))


@pytest.mark.asyncio
async def test_empty_file_returns_defaults(store):
    data = await store.get()
    assert data["text_model"] == "deepseek-chat"
    assert data["crawler_schedule"]["enabled"] is True
    assert data["crawler_schedule"]["interval_hours"] == 6
    assert "_updated_at" not in data


@pytest.mark.asyncio
async def test_update_persists_and_merges(store):
    await store.update({"text_model": "qwen-max"})
    await store.update({"crawler_schedule": {"enabled": False}})

    data = await store.get()
    assert data["text_model"] == "qwen-max"
    assert data["crawler_schedule"]["enabled"] is False
    assert data["crawler_schedule"]["interval_hours"] == 6
    assert data["crawler_schedule"]["hot_keywords_top_n"] == 50


@pytest.mark.asyncio
async def test_replace_overwrites_fully(store):
    await store.update({"text_model": "qwen-max"})
    await store.replace(
        {
            "text_model": "deepseek-chat",
            "vision_model": "qwen3-vl-plus",
            "embedding_model": "text-embedding-v4",
            "video_analysis_enabled": False,
            "crawler_schedule": {"enabled": False, "interval_hours": 12, "hot_keywords_top_n": 20},
        }
    )

    data = await store.get()
    assert data["video_analysis_enabled"] is False
    assert data["crawler_schedule"]["enabled"] is False
    assert data["crawler_schedule"]["interval_hours"] == 12
    assert data["crawler_schedule"]["hot_keywords_top_n"] == 20


@pytest.mark.asyncio
async def test_get_crawler_schedule_coerces_types(store):
    await store.update({"crawler_schedule": {"enabled": False, "interval_hours": "24", "hot_keywords_top_n": 10}})

    schedule = await store.get_crawler_schedule()
    assert schedule["enabled"] is False
    assert schedule["interval_hours"] == 24
    assert schedule["hot_keywords_top_n"] == 10


@pytest.mark.asyncio
async def test_corrupted_json_returns_defaults(store, tmp_path):
    path = tmp_path / "system_settings.json"
    path.write_text("{ not valid json", encoding="utf-8")

    data = await store.get()
    assert data["text_model"] == "deepseek-chat"
    assert data["crawler_schedule"]["enabled"] is True


@pytest.mark.asyncio
async def test_persists_across_instances(tmp_path):
    from backend.app.services.system_settings_store import SystemSettingsStore

    first = SystemSettingsStore(store_file=str(tmp_path / "sys.json"))
    await first.update({"crawler_schedule": {"enabled": False, "interval_hours": 24}})

    second = SystemSettingsStore(store_file=str(tmp_path / "sys.json"))
    data = await second.get()
    assert data["crawler_schedule"]["enabled"] is False
    assert data["crawler_schedule"]["interval_hours"] == 24


@pytest.mark.asyncio
async def test_update_writes_updated_at(store, tmp_path):
    await store.update({"text_model": "qwen-max"})

    raw = json.loads((tmp_path / "system_settings.json").read_text(encoding="utf-8"))
    assert "_updated_at" in raw
    assert raw["text_model"] == "qwen-max"
