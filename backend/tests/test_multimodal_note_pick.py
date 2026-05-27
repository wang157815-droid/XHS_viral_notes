"""multimodal_note_pick: 四源可见笔记优先于全池 Top-N。"""

from __future__ import annotations

from backend.app.application.agents.multimodal_note_pick import (
    pick_multimodal_notes,
    visible_note_ids_for_media,
)


def _v(nid: str, score: int, media: str = "video") -> dict:
    return {
        "note_id": nid,
        "media_type": media,
        "interaction_score": score,
        "likes": 0,
        "video_url": "https://example.com/x.mp4" if media == "video" else "",
        "cover_url": "https://example.com/c.jpg" if media == "image" else "",
    }


def test_visible_ids_dedupe_across_buckets() -> None:
    crawler = {
        "sources": {
            "category_top": [_v("a", 1), _v("b", 2)],
            "competitor": [_v("a", 1)],
            "top_interaction": [],
        }
    }
    assert visible_note_ids_for_media(crawler, "video") == ["a", "b"]


def test_pick_prioritizes_all_visible_then_rest_by_score() -> None:
    pool = [
        _v("low", 1),
        _v("high", 999),
        _v("mid", 50),
        _v("sheet_only", 5),
    ]
    crawler = {
        "sources": {
            "category_top": [_v("sheet_only", 5)],
            "competitor": [_v("low", 1)],
            "top_interaction": [],
        }
    }
    # floor=2, visible unique = low + sheet_only → len=2, target=max(2,2)=2
    picked = pick_multimodal_notes(
        pool, crawler, media_type="video", floor_n=2, hard_max=10
    )
    ids = [p["note_id"] for p in picked]
    assert set(ids) == {"low", "sheet_only"}


def test_floor_extends_beyond_visible_with_high_scorers() -> None:
    pool = [_v("v1", 10), _v("v2", 9), _v("v3", 8), _v("v4", 7), _v("v5", 6), _v("v6", 5)]
    crawler = {"sources": {k: [] for k in ("category_top", "competitor", "top_interaction")}}
    picked = pick_multimodal_notes(
        pool, crawler, media_type="video", floor_n=4, hard_max=99
    )
    assert [p["note_id"] for p in picked] == ["v1", "v2", "v3", "v4"]


def test_hard_max_truncates_visible_sorted_by_score() -> None:
    pool = [_v("a", 1), _v("b", 100), _v("c", 50)]
    crawler = {
        "sources": {
            "category_top": [_v("a", 1), _v("b", 100), _v("c", 50)],
            "competitor": [],
            "top_interaction": [],
        }
    }
    # visible 3, floor 2 → target=min(2, max(2,3))=2 — wait hard_max=2
    # target = min(2, max(2, 3)) = min(2, 3) = 2
    picked = pick_multimodal_notes(
        pool, crawler, media_type="video", floor_n=2, hard_max=2
    )
    assert [p["note_id"] for p in picked] == ["b", "c"]
