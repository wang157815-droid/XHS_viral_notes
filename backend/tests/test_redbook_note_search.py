"""backend/app/infrastructure/crawlers/redbook_note_search.py 共用采集函数单测。

覆盖：sort 参数透传、达到 per_keyword_target 即停、tier2_filter 淘汰、
exclude_note_ids 去重、log_hook 播报、fetch_detail 补全全文/标签。
"""

from __future__ import annotations

import pytest

from backend.app.infrastructure.crawlers.redbook_api_client import RedbookApiClient
from backend.app.infrastructure.crawlers import redbook_note_search as search_mod


def _search_response(notes):
    return {
        "err_no": 0,
        "message": "success",
        "data": {"data": {"items": [{"model_type": "note", "note": n} for n in notes]}},
    }


def _raw_note(note_id: str) -> dict:
    return {
        "id": note_id,
        "title": f"title-{note_id}",
        "desc": f"desc-{note_id}",
        "liked_count": 100,
        "collected_count": 10,
        "comments_count": 5,
        "shared_count": 1,
        "xsec_token": "tok",
        "type": "normal",
        "user": {"userid": "u1", "nickname": "n1"},
        "images_list": [{"url": "https://img/1"}],
    }


@pytest.mark.asyncio
async def test_search_notes_passes_sort_through_to_client(monkeypatch):
    seen_sorts = []

    def fake_search(self, keyword, page=1, sort="general", **kwargs):
        seen_sorts.append(sort)
        return _search_response([_raw_note("n1"), _raw_note("n2")]) if page == 1 else _search_response([])

    monkeypatch.setattr(RedbookApiClient, "search", fake_search)

    notes = await search_mod.search_notes_for_keywords(
        ["kw1"],
        sort="popularity_descending",
        per_keyword_target=2,
        fetch_detail=False,
        inter_page_sleep=(0, 0),
    )

    assert seen_sorts == ["popularity_descending"]
    assert {n["note_id"] for n in notes} == {"n1", "n2"}


@pytest.mark.asyncio
async def test_search_notes_stops_once_per_keyword_target_reached(monkeypatch):
    call_count = {"n": 0}

    def fake_search(self, keyword, page=1, sort="general", **kwargs):
        call_count["n"] += 1
        if page == 1:
            return _search_response([_raw_note("a"), _raw_note("b"), _raw_note("c")])
        return _search_response([_raw_note("d")])

    monkeypatch.setattr(RedbookApiClient, "search", fake_search)

    notes = await search_mod.search_notes_for_keywords(
        ["kw"],
        sort="time_descending",
        per_keyword_target=2,
        fetch_detail=False,
        inter_page_sleep=(0, 0),
    )

    assert len(notes) == 2
    assert call_count["n"] == 1  # 第一页已达标，不应翻到第二页


@pytest.mark.asyncio
async def test_search_notes_applies_tier2_filter(monkeypatch):
    def fake_search(self, keyword, page=1, sort="general", **kwargs):
        return _search_response([_raw_note("keep"), _raw_note("drop")]) if page == 1 else _search_response([])

    monkeypatch.setattr(RedbookApiClient, "search", fake_search)

    async def tier2(note_dict):
        return note_dict["note_id"] == "keep"

    notes = await search_mod.search_notes_for_keywords(
        ["kw"],
        sort="time_descending",
        per_keyword_target=5,
        fetch_detail=False,
        tier2_filter=tier2,
        inter_page_sleep=(0, 0),
    )

    assert [n["note_id"] for n in notes] == ["keep"]


@pytest.mark.asyncio
async def test_search_notes_skips_excluded_ids(monkeypatch):
    def fake_search(self, keyword, page=1, sort="general", **kwargs):
        return _search_response([_raw_note("old"), _raw_note("new")]) if page == 1 else _search_response([])

    monkeypatch.setattr(RedbookApiClient, "search", fake_search)

    notes = await search_mod.search_notes_for_keywords(
        ["kw"],
        sort="time_descending",
        per_keyword_target=5,
        fetch_detail=False,
        exclude_note_ids={"old"},
        inter_page_sleep=(0, 0),
    )

    assert [n["note_id"] for n in notes] == ["new"]


@pytest.mark.asyncio
async def test_search_notes_invokes_log_hook(monkeypatch):
    def fake_search(self, keyword, page=1, sort="general", **kwargs):
        return _search_response([_raw_note("x")]) if page == 1 else _search_response([])

    monkeypatch.setattr(RedbookApiClient, "search", fake_search)

    logs = []

    async def log_hook(msg):
        logs.append(msg)

    await search_mod.search_notes_for_keywords(
        ["kw"],
        sort="time_descending",
        per_keyword_target=1,
        fetch_detail=False,
        log_hook=log_hook,
        inter_page_sleep=(0, 0),
    )

    assert any("开始采集关键词" in m for m in logs)
    assert any("采集完毕" in m for m in logs)


@pytest.mark.asyncio
async def test_search_notes_fetch_detail_enriches_desc_and_tags(monkeypatch):
    def fake_search(self, keyword, page=1, sort="general", **kwargs):
        return _search_response([_raw_note("x")]) if page == 1 else _search_response([])

    def fake_get_detail(self, note_id, **kwargs):
        return {
            "err_no": 0,
            "data": {"data": [{"note_list": [
                {"desc": "完整正文", "hash_tag": [{"type": "topic", "name": "标签A"}]}
            ]}]},
        }

    monkeypatch.setattr(RedbookApiClient, "search", fake_search)
    monkeypatch.setattr(RedbookApiClient, "get_detail", fake_get_detail)

    notes = await search_mod.search_notes_for_keywords(
        ["kw"],
        sort="time_descending",
        per_keyword_target=1,
        fetch_detail=True,
        inter_page_sleep=(0, 0),
        inter_detail_sleep=(0, 0),
    )

    assert notes[0]["desc"] == "完整正文"
    assert notes[0]["tags"] == ["标签A"]


@pytest.mark.asyncio
async def test_search_notes_multiple_keywords_each_searched(monkeypatch):
    seen_keywords = []

    def fake_search(self, keyword, page=1, sort="general", **kwargs):
        seen_keywords.append((keyword, page))
        if page == 1:
            return _search_response([_raw_note(f"{keyword}-1")])
        return _search_response([])

    monkeypatch.setattr(RedbookApiClient, "search", fake_search)

    notes = await search_mod.search_notes_for_keywords(
        ["kw1", "kw2"],
        sort="time_descending",
        per_keyword_target=1,
        fetch_detail=False,
        inter_page_sleep=(0, 0),
    )

    assert {n["note_id"] for n in notes} == {"kw1-1", "kw2-1"}
    assert ("kw1", 1) in seen_keywords
    assert ("kw2", 1) in seen_keywords
