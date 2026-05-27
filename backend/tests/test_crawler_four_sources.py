"""4.3pre.2: CrawlerAgent 三源视图(category_top / competitor / top_interaction)。

不真实采集 — 直接测试 `_build_four_source_view()` 纯函数 + run() 集成。
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest


# ---------- 1. 纯函数测试 ----------

def test_four_source_view_maps_dimensions_to_sources():
    from backend.app.application.agents.crawler_agent import _build_four_source_view

    samples_by_dim = {
        "industry": [
            {
                "note_id": "i1",
                "title": "行业 1",
                "interaction_score": 1000,
                "likes": 500,
                "published_at": "2024-05-01",
            },
            {
                "note_id": "i2",
                "title": "行业 2",
                "interaction_score": 800,
                "likes": 400,
                "published_at": "2025-01-01",
            },
        ],
        "competitor": [
            {"note_id": "c1", "title": "竞品 1", "interaction_score": 5000, "likes": 2000},
        ],
        "brand": [
            {"note_id": "b1", "title": "本品 1", "interaction_score": 3000, "likes": 1500},
        ],
    }
    all_notes = [
        samples_by_dim["industry"][0],
        samples_by_dim["industry"][1],
        samples_by_dim["competitor"][0],
        samples_by_dim["brand"][0],
    ]

    sources, all_notes_with_hits = _build_four_source_view(all_notes, samples_by_dim)

    # 3 个源都存在
    assert set(sources.keys()) == {"category_top", "competitor", "top_interaction"}
    # industry → category_top
    assert {n["note_id"] for n in sources["category_top"]} == {"i1", "i2"}
    # competitor 维度 → competitor 源
    assert {n["note_id"] for n in sources["competitor"]} == {"c1"}
    # top_interaction 来自 brand 维度 → b1 互动 3000 排第一
    assert sources["top_interaction"][0]["note_id"] == "b1"
    c1 = next(n for n in all_notes_with_hits if n["note_id"] == "c1")
    assert "competitor" in c1["sources_hit"]
    assert "top_interaction" not in c1["sources_hit"]


def test_four_source_view_top_interaction_sorts_by_score():
    from backend.app.application.agents.crawler_agent import _build_four_source_view

    samples_by_dim = {
        "industry": [
            {"note_id": "low", "interaction_score": 100, "likes": 50, "published_at": "2025-03-01"},
            {"note_id": "high", "interaction_score": 9999, "likes": 5000, "published_at": "2024-01-01"},
            {"note_id": "mid", "interaction_score": 1000, "likes": 500, "published_at": "2025-06-01"},
        ],
        "competitor": [],
        "brand": [],
    }
    all_notes = list(samples_by_dim["industry"])

    sources, _ = _build_four_source_view(all_notes, samples_by_dim)
    # brand 为空 → 回退全库互动 Top，第一条 high
    assert sources["top_interaction"][0]["note_id"] == "high"


def test_four_source_view_handles_empty_input():
    from backend.app.application.agents.crawler_agent import _build_four_source_view

    sources, all_notes = _build_four_source_view([], {"industry": [], "competitor": [], "brand": []})
    assert all(sources[k] == [] for k in sources)
    assert all_notes == []


def test_per_dim_sources_prefers_comp_cache_for_competitor_when_kw_tuple_collides():
    """ti==tc 时旧逻辑会让 competitor 维吃到 main 池；导出 Sheet3/4 会雷同。应拆成两侧缓存。"""
    from backend.app.application.agents.crawler_agent import (
        _build_four_source_view,
        _per_dim_sources_for_dual_cache,
    )

    main_notes = [{"note_id": "m1", "interaction_score": 100, "likes": 10, "published_at": "2025-01-01"}]
    comp_notes = [{"note_id": "x1", "interaction_score": 200, "likes": 20, "published_at": "2025-02-01"}]
    main_res = (main_notes, "L2")
    comp_res = (comp_notes, "L2")
    # 行业/竞品关键词归一化后 tuple 相同（例如都曾回落到同一主词），但缓存行仍分两侧存储；
    # brand 用与行业一致的本品词，避免与竞品 tuple 重合。
    dims_keywords = {
        "industry": ["抗老精华"],
        "competitor": ["抗老精华"],
        "brand": ["PMPM"],
    }
    active_dims = ["industry", "competitor", "brand"]
    per_dim = _per_dim_sources_for_dual_cache(dims_keywords, active_dims, main_res, comp_res)
    assert per_dim["industry"] is main_notes
    assert per_dim["competitor"] is comp_notes
    assert per_dim["brand"] is main_notes

    merged = main_notes + comp_notes
    sources, _ = _build_four_source_view(merged, per_dim)
    assert {n["note_id"] for n in sources["category_top"]} == {"m1"}
    assert {n["note_id"] for n in sources["competitor"]} == {"x1"}


# ---------- 4. run() 集成: sources 字段确实写入 crawler_output ----------

@pytest.mark.asyncio
async def test_crawler_run_writes_sources_field(monkeypatch):
    """run() 完成后,crawler_output 必须有 sources + all_notes 两个新字段。"""
    from backend.app.application.agents import crawler_agent as crawler_mod
    from backend.app.application.agents.base import AgentContext
    from backend.app.application.task_service import task_service
    from backend.app.domain.task_context import task_context_store, TaskContextWriter

    res = task_service.create_task(
        owner_user_id="owner-four-src",
        raw_input="x",
        keywords=["test_kw"],
        idempotency_key=None,
    )
    tid = res.record.task_id
    ctx = task_context_store.require(tid)
    TaskContextWriter(ctx).write(
        "input_spec",
        {
            "parsed": {
                "keywords": ["kw"],
                "dimensions": {"industry": ["a"], "competitor": ["b"], "brand": ["c"]},
            }
        },
        agent_id="test",
        merge=True,
    )

    monkeypatch.setattr(crawler_mod, "_resolve_cookies_str", lambda _u: "fake")
    monkeypatch.setattr(crawler_mod, "_INTER_GROUP_SLEEP", 0)
    monkeypatch.setattr(crawler_mod, "_ENRICH_ENABLED", False)
    monkeypatch.setattr(crawler_mod, "_CRAWLER_COMMENT_HW_ENABLED", False)

    class _FakeNote:
        def __init__(self, nid, score):
            self.note_id = nid
            self.note_url = f"https://x/{nid}"
            self.note_type = "图集"
            self.title = nid
            self.desc = ""
            self.liked_count = score
            self.comment_count = 10
            self.collected_count = 10
            self.interaction_score = score
            self.image_list = ["https://img"]
            self.video_cover = None
            self.video_addr = None
            self.nickname = "u"
            self.source_keywords = []

    counter = {"i": 0}

    async def fake_collect(cookies, keywords, target_count, runtime_cfg):
        counter["i"] += 1
        return [_FakeNote(f"n{counter['i']}_1", 1000), _FakeNote(f"n{counter['i']}_2", 500)]

    monkeypatch.setattr(crawler_mod, "_collect_one_dimension", fake_collect)

    from backend.app.application.agents.crawler_agent import CrawlerAgent
    from tests.test_agents_real import FakeGateway, FakeBus  # 复用既有 fake

    agent = CrawlerAgent(model_gateway_instance=FakeGateway([]), event_bus=FakeBus())
    await agent.run(AgentContext(task_id=tid, task_context=ctx))

    out = ctx.get("crawler_output")
    # 新字段
    assert "sources" in out
    assert "all_notes" in out
    # 3 个源都在
    assert set(out["sources"].keys()) == {"category_top", "competitor", "top_interaction"}
    # 每条 note 的 sources_hit 至少有一项
    for note in out["all_notes"]:
        assert isinstance(note.get("sources_hit"), list)
        assert len(note["sources_hit"]) >= 1
