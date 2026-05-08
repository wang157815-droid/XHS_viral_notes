"""scheduled_warmup 门控逻辑单测（阶段 4.α-patch2）。

双入口设计（同一函数 + force 参数）：
- cron 触发(force=False): 受双门控(开关 + interval)限制
- 用户"立即采集"(force=True): 仅受开关限制,穿透 interval

覆盖的 6 个核心场景:
1. 开关关 + cron → skip/admin_disabled
2. 开关关 + 立即采集 → skip/admin_disabled(总开关,不能绕过)
3. 开关开 + cron + interval 未到 → skip/interval_not_reached
4. 开关开 + cron + interval 已到 → 执行
5. 开关开 + 立即采集 + interval 未到 → 执行(穿透)
6. 开关开 + 立即采集连点两次 → 两次都执行
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def fake_store(tmp_path, monkeypatch):
    """用 tmp 文件的 SystemSettingsStore 替换全局单例。"""
    from backend.app.services import system_settings_store as mod

    store = mod.SystemSettingsStore(store_file=str(tmp_path / "system_settings.json"))
    monkeypatch.setattr(mod, "_default_store", store, raising=False)
    monkeypatch.setattr(mod, "get_system_settings_store", lambda: store)
    return store


def _patch_collect(monkeypatch, warmup, keywords):
    async def _c():
        return list(keywords)

    monkeypatch.setattr(warmup, "_collect_warmup_keywords", _c)


def _patch_warm_one(monkeypatch, warmup, crawled_count=3):
    captured = []

    async def _w(kw, target=30):
        captured.append(kw)
        return crawled_count

    monkeypatch.setattr(warmup, "_warmup_one_keyword", _w)
    return captured


def _patch_no_sleep(monkeypatch, warmup):
    async def _n(_):
        return None

    monkeypatch.setattr(warmup.asyncio, "sleep", _n)


def _patch_write_last_run(monkeypatch, warmup):
    async def _w(_):
        return None

    monkeypatch.setattr(warmup, "_write_last_run", _w)


# ---------------------------------------------------------------------------
# 场景 1: 开关关 + cron 触发
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cron_skipped_when_disabled(fake_store, monkeypatch):
    from backend.app.infrastructure.queue.tasks import warmup

    await fake_store.update({"crawler_schedule": {"enabled": False}})

    collect_called = {"count": 0}

    async def _c():
        collect_called["count"] += 1
        return []

    monkeypatch.setattr(warmup, "_collect_warmup_keywords", _c)

    result = await warmup.scheduled_warmup({}, force=False)

    assert result["status"] == "skipped"
    assert result["reason"] == "admin_disabled"
    assert result["trigger"] == "scheduled"
    assert collect_called["count"] == 0


# ---------------------------------------------------------------------------
# 场景 2: 开关关 + 立即采集 → 总开关优先,仍然 skip
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_manual_respects_enabled_flag(fake_store, monkeypatch):
    """force=True 也不能绕过开关（enabled 是总开关）。"""
    from backend.app.infrastructure.queue.tasks import warmup

    await fake_store.update({"crawler_schedule": {"enabled": False}})

    collect_called = {"count": 0}

    async def _c():
        collect_called["count"] += 1
        return ["kw1"]

    monkeypatch.setattr(warmup, "_collect_warmup_keywords", _c)

    result = await warmup.scheduled_warmup({}, force=True)

    assert result["status"] == "skipped"
    assert result["reason"] == "admin_disabled"
    assert result["trigger"] == "manual"
    assert collect_called["count"] == 0


# ---------------------------------------------------------------------------
# 场景 3: cron + interval 未到 → skip
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cron_respects_interval(fake_store, monkeypatch):
    from backend.app.infrastructure.queue.tasks import warmup

    await fake_store.update({"crawler_schedule": {"enabled": True, "interval_hours": 6}})

    recent_finish = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    async def _last_run():
        return {"status": "ok", "finished_at": recent_finish, "ok_count": 3}

    monkeypatch.setattr(warmup, "read_last_run", _last_run)

    collect_called = {"count": 0}

    async def _c():
        collect_called["count"] += 1
        return ["kw"]

    monkeypatch.setattr(warmup, "_collect_warmup_keywords", _c)

    result = await warmup.scheduled_warmup({}, force=False)

    assert result["status"] == "skipped"
    assert result["reason"] == "interval_not_reached"
    assert result["trigger"] == "scheduled"
    assert result["interval_hours"] == 6
    assert collect_called["count"] == 0


# ---------------------------------------------------------------------------
# 场景 4: cron + interval 已到 → 执行
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cron_runs_when_interval_reached(fake_store, monkeypatch):
    from backend.app.infrastructure.queue.tasks import warmup

    await fake_store.update({"crawler_schedule": {"enabled": True, "interval_hours": 6, "hot_keywords_top_n": 2}})

    old_finish = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()

    async def _last_run():
        return {"status": "ok", "finished_at": old_finish}

    monkeypatch.setattr(warmup, "read_last_run", _last_run)
    _patch_collect(monkeypatch, warmup, ["kw1", "kw2", "kw3"])
    captured = _patch_warm_one(monkeypatch, warmup, crawled_count=5)
    _patch_no_sleep(monkeypatch, warmup)
    _patch_write_last_run(monkeypatch, warmup)

    result = await warmup.scheduled_warmup({}, force=False)

    assert result["status"] == "ok"
    assert result["trigger"] == "scheduled"
    assert result["ok_count"] == 2
    assert captured == ["kw1", "kw2"]  # top_n=2


# ---------------------------------------------------------------------------
# 场景 5: 立即采集 + interval 未到 → 穿透执行 ⭐(核心修复)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_manual_bypasses_interval(fake_store, monkeypatch):
    """立即采集必须穿透 interval 节流,不受上次执行时间影响。"""
    from backend.app.infrastructure.queue.tasks import warmup

    await fake_store.update({"crawler_schedule": {"enabled": True, "interval_hours": 6, "hot_keywords_top_n": 3}})

    # 哪怕上次刚跑完 1 分钟,立即采集也必须执行
    very_recent = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

    async def _last_run():
        return {"status": "ok", "finished_at": very_recent}

    monkeypatch.setattr(warmup, "read_last_run", _last_run)
    _patch_collect(monkeypatch, warmup, ["kw1", "kw2"])
    captured = _patch_warm_one(monkeypatch, warmup)
    _patch_no_sleep(monkeypatch, warmup)
    _patch_write_last_run(monkeypatch, warmup)

    result = await warmup.scheduled_warmup({}, force=True)

    assert result["status"] == "ok"
    assert result["trigger"] == "manual"
    assert result["ok_count"] == 2
    assert captured == ["kw1", "kw2"]


# ---------------------------------------------------------------------------
# 场景 6: 立即采集连点 → 两次都执行
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_manual_back_to_back_both_run(fake_store, monkeypatch):
    from backend.app.infrastructure.queue.tasks import warmup

    await fake_store.update({"crawler_schedule": {"enabled": True, "interval_hours": 6, "hot_keywords_top_n": 1}})

    run_count = {"value": 0}

    async def _last_run():
        if run_count["value"] == 0:
            return None
        # 第二次触发时,上次刚刚写 last_run 1 秒前
        return {
            "status": "ok",
            "finished_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        }

    monkeypatch.setattr(warmup, "read_last_run", _last_run)
    _patch_collect(monkeypatch, warmup, ["kw1"])
    _patch_warm_one(monkeypatch, warmup)
    _patch_no_sleep(monkeypatch, warmup)

    async def _w(_):
        run_count["value"] += 1

    monkeypatch.setattr(warmup, "_write_last_run", _w)

    r1 = await warmup.scheduled_warmup({}, force=True)
    r2 = await warmup.scheduled_warmup({}, force=True)

    assert r1["status"] == "ok"
    assert r1["trigger"] == "manual"
    assert r2["status"] == "ok"
    assert r2["trigger"] == "manual"


# ---------------------------------------------------------------------------
# 辅助场景: 无关键词时 skip（通用,不涉及 force）
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_skip_when_no_keywords(fake_store, monkeypatch):
    from backend.app.infrastructure.queue.tasks import warmup

    await fake_store.update({"crawler_schedule": {"enabled": True}})

    async def _last_run():
        return None

    monkeypatch.setattr(warmup, "read_last_run", _last_run)
    _patch_collect(monkeypatch, warmup, [])
    _patch_write_last_run(monkeypatch, warmup)

    result = await warmup.scheduled_warmup({}, force=True)

    assert result["status"] == "skipped"
    assert result["reason"] == "no_keywords"


# ---------------------------------------------------------------------------
# 辅助场景: cron 默认 force=False（确保参数默认值）
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_default_force_is_false(fake_store, monkeypatch):
    """不传 force 参数时,默认走 cron 模式(受 interval 限制)。"""
    from backend.app.infrastructure.queue.tasks import warmup

    await fake_store.update({"crawler_schedule": {"enabled": True, "interval_hours": 6}})

    recent = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()

    async def _last_run():
        return {"status": "ok", "finished_at": recent}

    monkeypatch.setattr(warmup, "read_last_run", _last_run)

    async def _c():
        return ["kw"]

    monkeypatch.setattr(warmup, "_collect_warmup_keywords", _c)

    result = await warmup.scheduled_warmup({})  # 不传 force

    assert result["status"] == "skipped"
    assert result["reason"] == "interval_not_reached"
    assert result["trigger"] == "scheduled"
