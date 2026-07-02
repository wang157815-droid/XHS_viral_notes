"""爆文任务第三方 Redbook API 采集分支单测。

覆盖：note_crawl_backend 开关读取降级、Redbook note → ViralNote 字段适配器、
_collect_one_dimension_via_redbook 排序与总量按关键词分摊、_collect_one_dimension
按开关正确分派到自研/第三方两条路径、industry 关键词数量不再被截断到 5。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.app.application.agents import crawler_agent as crawler_mod


# ---------- 1. 开关读取 / fail-open 降级 ----------

@pytest.mark.asyncio
async def test_resolve_note_crawl_backend_defaults_to_self_on_store_error(monkeypatch):
    class _BoomStore:
        async def get(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(
        "backend.app.services.system_settings_store.get_system_settings_store",
        lambda: _BoomStore(),
    )

    assert await crawler_mod._resolve_note_crawl_backend() == "self"


@pytest.mark.asyncio
async def test_resolve_note_crawl_backend_reads_redbook_api(monkeypatch):
    class _Store:
        async def get(self):
            return {"note_crawl_backend": "redbook_api"}

    monkeypatch.setattr(
        "backend.app.services.system_settings_store.get_system_settings_store",
        lambda: _Store(),
    )

    assert await crawler_mod._resolve_note_crawl_backend() == "redbook_api"


@pytest.mark.asyncio
async def test_resolve_note_crawl_backend_rejects_unknown_value(monkeypatch):
    class _Store:
        async def get(self):
            return {"note_crawl_backend": "not_a_real_backend"}

    monkeypatch.setattr(
        "backend.app.services.system_settings_store.get_system_settings_store",
        lambda: _Store(),
    )

    assert await crawler_mod._resolve_note_crawl_backend() == "self"


# ---------- 2. Redbook note dict → ViralNote 字段适配器 ----------

def test_redbook_note_to_viral_note_fields_maps_video_note():
    note = {
        "note_id": "n1",
        "url": "https://xhs/n1?xsec_token=tok",
        "note_type": "video",
        "user_id": "u1",
        "nickname": "作者",
        "title": "标题",
        "desc": "正文",
        "tags": ["标签1"],
        "likes": 100,
        "collects": 20,
        "comments": 10,
        "share_count": 5,
        "image_urls": [],
        "video_url": "https://cdn/v.mp4",
        "cover_url": "https://cdn/cover.jpg",
        "video_urls": [{"url": "https://cdn/v.mp4", "type": "h264"}],
        "source_keyword": "钢琴",
        "publish_time": "2026-01-01",
    }

    fields = crawler_mod._redbook_note_to_viral_note_fields(note)

    assert fields["note_type"] == "视频"
    assert fields["video_addr"] == "https://cdn/v.mp4"
    assert fields["video_cover"] == "https://cdn/cover.jpg"
    assert fields["video_urls"] == [{"url": "https://cdn/v.mp4", "type": "h264"}]
    assert fields["source_keywords"] == ["钢琴"]
    assert fields["liked_count"] == 100
    assert fields["collected_count"] == 20
    assert fields["comment_count"] == 10


def test_redbook_note_to_viral_note_fields_maps_image_note_type():
    note = {"note_id": "n2", "url": "", "note_type": "normal", "title": "t"}

    fields = crawler_mod._redbook_note_to_viral_note_fields(note)

    assert fields["note_type"] == "图集"
    assert fields["video_addr"] is None
    assert fields["source_keywords"] == []


# ---------- 3. 三方采集分支：排序 + 总量按关键词分摊 + 归一化兼容 ----------

def test_per_keyword_target_for_redbook_matches_self_developed_formula():
    # 2 词 × 总量 30 → 每词 15（与 ViralNoteCollector 一致）
    assert crawler_mod._per_keyword_target_for_redbook(["a", "b"], 30) == 15
    # 单关键词时总量即 per-keyword 目标
    assert crawler_mod._per_keyword_target_for_redbook(["solo"], 30) == 30


@pytest.mark.asyncio
async def test_collect_one_dimension_via_redbook_sorts_by_interaction_and_normalizes(monkeypatch):
    raw_notes = [
        {
            "note_id": "low", "url": "https://xhs/low", "note_type": "normal",
            "title": "low", "desc": "", "tags": [], "likes": 10, "collects": 0, "comments": 0,
            "share_count": 0, "image_urls": ["https://img/1"], "video_url": "", "video_urls": [],
            "cover_url": "https://img/1", "source_keyword": "kw", "publish_time": "2026-01-01",
            "user_id": "u1", "nickname": "n",
        },
        {
            "note_id": "high", "url": "https://xhs/high", "note_type": "video",
            "title": "high", "desc": "", "tags": [], "likes": 1000, "collects": 100, "comments": 50,
            "share_count": 10, "image_urls": [], "video_url": "https://cdn/v.mp4",
            "video_urls": [{"url": "https://cdn/v.mp4", "type": "h264"}],
            "cover_url": "https://cdn/cover.jpg", "source_keyword": "kw", "publish_time": "2026-01-02",
            "user_id": "u2", "nickname": "n2",
        },
    ]

    async def fake_search(keywords, *, sort, per_keyword_target, fetch_detail, **kwargs):
        assert sort == "popularity_descending"
        # 30 总量 ÷ 2 关键词 → 与自研 _calculate_target_per_keyword 一致为 15
        assert per_keyword_target == 15
        assert fetch_detail is True
        return list(raw_notes)

    monkeypatch.setattr(
        "backend.app.infrastructure.crawlers.redbook_note_search.search_notes_for_keywords",
        fake_search,
    )

    notes = await crawler_mod._collect_one_dimension_via_redbook(["kw1", "kw2"], 30)

    assert [n.note_id for n in notes] == ["high", "low"]  # 按互动量降序

    normalized = crawler_mod._normalize_note(notes[0], "industry", "kw1")
    assert normalized["media_type"] == "video"
    assert normalized["video_url"] == "https://cdn/v.mp4"
    assert normalized["cover_url"] == "https://cdn/cover.jpg"
    assert normalized["xsec_token"] == ""  # url 无 xsec_token 参数时应为空串不报错


@pytest.mark.asyncio
async def test_collect_one_dimension_via_redbook_skips_unconvertible_notes(monkeypatch):
    async def fake_search(keywords, **kwargs):
        return [{"note_id": "ok", "url": "", "note_type": "normal", "title": "t", "likes": 5}, None]

    monkeypatch.setattr(
        "backend.app.infrastructure.crawlers.redbook_note_search.search_notes_for_keywords",
        fake_search,
    )

    notes = await crawler_mod._collect_one_dimension_via_redbook(["kw"], 10)

    assert len(notes) == 1
    assert notes[0].note_id == "ok"


# ---------- 4. _collect_one_dimension 分派逻辑 ----------

@pytest.mark.asyncio
async def test_collect_one_dimension_dispatches_to_redbook_when_enabled(monkeypatch):
    monkeypatch.setattr(crawler_mod, "_resolve_note_crawl_backend", AsyncMock(return_value="redbook_api"))

    called = {}

    async def fake_via_redbook(keywords, target_count):
        called["keywords"] = keywords
        called["target_count"] = target_count
        return ["fake-note"]

    monkeypatch.setattr(crawler_mod, "_collect_one_dimension_via_redbook", fake_via_redbook)

    result = await crawler_mod._collect_one_dimension("cookie=1", ["kw1", "kw2"], 30, {})

    assert result == ["fake-note"]
    assert called == {"keywords": ["kw1", "kw2"], "target_count": 30}


@pytest.mark.asyncio
async def test_collect_one_dimension_uses_self_developed_path_by_default(monkeypatch):
    monkeypatch.setattr(crawler_mod, "_resolve_note_crawl_backend", AsyncMock(return_value="self"))

    class _FakeCollector:
        received_kwargs = None

        def __init__(self, cookies_str, owner_user_id=None):
            self.tier1_filter = None
            self.tier2_filter = None

        async def search_viral_notes_multi_keywords(self, **kwargs):
            _FakeCollector.received_kwargs = kwargs
            return ["self-note"]

    import viral_agent.services.core.viral_collector as vc_mod

    monkeypatch.setattr(vc_mod, "ViralNoteCollector", _FakeCollector)

    result = await crawler_mod._collect_one_dimension(
        "cookie=1", ["kw1", "kw2", "kw3"], 45, {"note_type": 1}
    )

    assert result == ["self-note"]
    # 自研路径不再重复截断关键词（由调用方按维度截断好）
    assert _FakeCollector.received_kwargs["keywords"] == ["kw1", "kw2", "kw3"]


# ---------- 5. 维度关键词上限 ----------

def test_kw_cap_for_dim_industry_is_relaxed_others_stay_capped():
    assert crawler_mod._kw_cap_for_dim("industry") == crawler_mod._MAX_KWS_INDUSTRY
    assert crawler_mod._kw_cap_for_dim("industry") > crawler_mod._MAX_KWS_PER_DIM
    assert crawler_mod._kw_cap_for_dim("competitor") == crawler_mod._MAX_KWS_PER_DIM
    assert crawler_mod._kw_cap_for_dim("brand") == crawler_mod._MAX_KWS_PER_DIM


@pytest.mark.asyncio
async def test_industry_keywords_beyond_five_are_not_truncated(monkeypatch):
    from backend.app.application.agents.base import AgentContext
    from backend.app.application.task_service import task_service
    from backend.app.domain.task_context import task_context_store, TaskContextWriter
    from tests.test_agents_real import FakeGateway, FakeBus

    res = task_service.create_task(
        owner_user_id="owner-kw-cap", raw_input="x", keywords=["kw"], idempotency_key=None,
    )
    tid = res.record.task_id
    ctx = task_context_store.require(tid)
    many_kws = [f"关键词{i}" for i in range(8)]
    TaskContextWriter(ctx).write(
        "input_spec",
        {"parsed": {"keywords": many_kws, "dimensions": {"industry": many_kws, "competitor": [], "brand": []}}},
        agent_id="test",
        merge=True,
    )

    monkeypatch.setattr(crawler_mod, "_resolve_cookies_str", lambda _u: "fake")
    monkeypatch.setattr(crawler_mod, "_INTER_GROUP_SLEEP", 0)
    monkeypatch.setattr(crawler_mod, "_ENRICH_ENABLED", False)
    monkeypatch.setattr(crawler_mod, "_CRAWLER_COMMENT_HW_ENABLED", False)

    seen_keywords = []

    async def fake_collect(cookies, keywords, target_count, runtime_cfg, owner_user_id=None):
        seen_keywords.append(list(keywords))
        return []

    monkeypatch.setattr(crawler_mod, "_collect_one_dimension", fake_collect)

    agent = crawler_mod.CrawlerAgent(model_gateway_instance=FakeGateway([]), event_bus=FakeBus())
    await agent.run(AgentContext(task_id=tid, task_context=ctx))

    assert seen_keywords, "collect 未被调用"
    assert len(seen_keywords[0]) == 8  # 8 个关键词全部保留，不再截断到 5
