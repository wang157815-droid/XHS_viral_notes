"""爆文任务发布时间窗口过滤（业务需求：仅分析 2025-10~2026-03 的笔记）。

覆盖纯函数 `_apply_note_time_range_filter` 的边界情况，以及 `CrawlerAgent.run()`
集成后对 `notes_image`/`notes_video`/`all_notes(_with_hits)` 的过滤效果，
并确认 stub 占位路径不受影响（stub 假数据本身就落在窗口外）。
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from backend.app.application.agents import crawler_agent as crawler_mod


def _note(published_at: str, **extra: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "note_id": extra.pop("note_id", "n"),
        "media_type": "image",
        "interaction_score": 100,
        "published_at": published_at,
    }
    base.update(extra)
    return base


# ---------- 1. 纯函数边界测试 ----------

def test_apply_note_time_range_filter_keeps_notes_within_window():
    notes = [_note("2025-11-01", note_id="in_range"), _note("2026-02-15 08:00:00", note_id="in_range2")]
    kept = crawler_mod._apply_note_time_range_filter(notes)
    assert {n["note_id"] for n in kept} == {"in_range", "in_range2"}


def test_apply_note_time_range_filter_excludes_before_window():
    notes = [_note("2025-09-30", note_id="too_early"), _note("2024-01-01", note_id="way_too_early")]
    assert crawler_mod._apply_note_time_range_filter(notes) == []


def test_apply_note_time_range_filter_excludes_after_window():
    notes = [_note("2026-04-01", note_id="too_late"), _note("2026-12-31", note_id="way_too_late")]
    assert crawler_mod._apply_note_time_range_filter(notes) == []


def test_apply_note_time_range_filter_boundary_inclusive_start_exclusive_end():
    # 起点 2025-10-01 含边界；终点 2026-04-01 为开区间不含，2026-03-31 全天仍保留
    notes = [
        _note("2025-10-01", note_id="start_boundary"),
        _note("2026-03-31 23:59:59", note_id="end_of_march"),
        _note("2026-04-01 00:00:00", note_id="april_first_excluded"),
    ]
    kept_ids = {n["note_id"] for n in crawler_mod._apply_note_time_range_filter(notes)}
    assert kept_ids == {"start_boundary", "end_of_march"}


def test_apply_note_time_range_filter_excludes_unparseable_or_missing_published_at():
    notes = [
        _note("", note_id="empty"),
        _note("未知时间", note_id="garbled"),
        {"note_id": "missing_field", "media_type": "image", "interaction_score": 1},
    ]
    assert crawler_mod._apply_note_time_range_filter(notes) == []


# ---------- 2. CrawlerAgent.run() 集成 ----------

@pytest.mark.asyncio
async def test_crawler_run_filters_notes_outside_time_window(monkeypatch):
    """真实采集路径：窗口外笔记应从 note_summary/all_notes/notes_image/notes_video 中剔除。"""
    from backend.app.application.agents.base import AgentContext
    from backend.app.application.task_service import task_service
    from backend.app.domain.task_context import task_context_store, TaskContextWriter

    res = task_service.create_task(
        owner_user_id="owner-time-filter", raw_input="x", keywords=["kw"], idempotency_key=None,
    )
    tid = res.record.task_id
    ctx = task_context_store.require(tid)
    TaskContextWriter(ctx).write(
        "input_spec",
        {"parsed": {"keywords": ["kw"], "dimensions": {"industry": ["kw"], "competitor": [], "brand": []}}},
        agent_id="test",
        merge=True,
    )

    monkeypatch.setattr(crawler_mod, "_resolve_cookies_str", lambda _u: "fake=1")
    monkeypatch.setattr(crawler_mod, "_INTER_GROUP_SLEEP", 0)
    monkeypatch.setattr(crawler_mod, "_ENRICH_ENABLED", False)
    monkeypatch.setattr(crawler_mod, "_CRAWLER_COMMENT_HW_ENABLED", False)

    class _FakeNote:
        def __init__(self, nid, upload_time, note_type="图集"):
            self.note_id = nid
            self.note_url = f"https://x/{nid}"
            self.note_type = note_type
            self.title = nid
            self.desc = ""
            self.liked_count = 100
            self.comment_count = 5
            self.collected_count = 5
            self.interaction_score = 100
            self.image_list = ["https://img"]
            self.video_cover = None
            self.video_addr = "https://cdn/v.mp4" if note_type == "视频" else None
            self.nickname = "u"
            self.source_keywords = []
            self.upload_time = upload_time

    async def fake_collect(cookies, keywords, target_count, runtime_cfg, owner_user_id=None):
        return [
            _FakeNote("in_window_image", "2025-12-01 10:00:00", note_type="图集"),
            _FakeNote("in_window_video", "2026-03-01 10:00:00", note_type="视频"),
            _FakeNote("before_window", "2025-06-01 10:00:00"),
            _FakeNote("after_window", "2026-05-01 10:00:00"),
        ]

    monkeypatch.setattr(crawler_mod, "_collect_one_dimension", fake_collect)

    from tests.test_agents_real import FakeGateway, FakeBus

    agent = crawler_mod.CrawlerAgent(model_gateway_instance=FakeGateway([]), event_bus=FakeBus())
    await agent.run(AgentContext(task_id=tid, task_context=ctx))

    out = ctx.get("crawler_output")
    kept_ids = {n["note_id"] for n in out["all_notes"]}
    assert kept_ids == {"in_window_image", "in_window_video"}
    assert {n["note_id"] for n in out["notes_image"]} == {"in_window_image"}
    assert {n["note_id"] for n in out["notes_video"]} == {"in_window_video"}
    assert out["sample_count"] == 2


@pytest.mark.asyncio
async def test_crawler_run_stub_path_not_affected_by_time_filter(monkeypatch):
    """三维采集全部失败 → stub 占位数据；stub 时间戳固定在窗口外，但不应被时间过滤清空。"""
    from backend.app.application.agents.base import AgentContext
    from backend.app.application.task_service import task_service
    from backend.app.domain.task_context import task_context_store, TaskContextWriter

    res = task_service.create_task(
        owner_user_id="owner-stub-time", raw_input="x", keywords=["kw"], idempotency_key=None,
    )
    tid = res.record.task_id
    ctx = task_context_store.require(tid)
    TaskContextWriter(ctx).write(
        "input_spec",
        {"parsed": {"keywords": ["kw"], "dimensions": {"industry": ["kw"], "competitor": [], "brand": []}}},
        agent_id="test",
        merge=True,
    )

    # 无 cookies → 直接落入 stub 分支
    monkeypatch.setattr(crawler_mod, "_resolve_cookies_str", lambda _u: "")
    monkeypatch.setattr(crawler_mod, "_INTER_GROUP_SLEEP", 0)

    from tests.test_agents_real import FakeGateway, FakeBus

    agent = crawler_mod.CrawlerAgent(model_gateway_instance=FakeGateway([]), event_bus=FakeBus())
    await agent.run(AgentContext(task_id=tid, task_context=ctx))

    out = ctx.get("crawler_output")
    assert out["source"] == "stub"
    assert len(out["all_notes"]) > 0
