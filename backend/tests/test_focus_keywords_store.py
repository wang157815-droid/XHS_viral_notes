"""FocusKeywordsStore 单测（阶段 4.α 补丁）。

覆盖规范化 / 保序 / 大小写不敏感去重 / 长度上限 / 损坏文件降级。
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def store(tmp_path):
    from backend.app.services.focus_keywords_store import FocusKeywordsStore

    return FocusKeywordsStore(store_file=str(tmp_path / "focus_keywords.json"))


@pytest.mark.asyncio
async def test_empty_file_returns_empty_list(store):
    assert await store.get() == []


@pytest.mark.asyncio
async def test_replace_normalizes_and_persists(store, tmp_path):
    result = await store.replace(["  巧克力  ", "巧克力", "咖啡", "", None, "Fazer"])
    assert result == ["巧克力", "咖啡", "Fazer"]

    # 第二个实例读同路径,验证落盘
    from backend.app.services.focus_keywords_store import FocusKeywordsStore

    other = FocusKeywordsStore(store_file=str(tmp_path / "focus_keywords.json"))
    assert await other.get() == ["巧克力", "咖啡", "Fazer"]


@pytest.mark.asyncio
async def test_case_insensitive_dedup_preserves_first_casing(store):
    result = await store.replace(["Fazer", "fazer", "FAZER", "Coffee"])
    assert result == ["Fazer", "Coffee"]


@pytest.mark.asyncio
async def test_max_items_enforced(tmp_path):
    from backend.app.services.focus_keywords_store import FocusKeywordsStore

    small = FocusKeywordsStore(store_file=str(tmp_path / "focus.json"), max_items=3)
    result = await small.replace(["a", "b", "c", "d", "e"])
    assert result == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_too_long_keyword_gets_truncated(store):
    long_kw = "x" * 80
    result = await store.replace([long_kw])
    assert len(result) == 1
    assert len(result[0]) <= 32


@pytest.mark.asyncio
async def test_add_idempotent(store):
    await store.replace(["a"])
    assert await store.add("b") == ["a", "b"]
    assert await store.add("B") == ["a", "b"]  # 大小写去重
    assert await store.add("  a  ") == ["a", "b"]  # 去空白后重复


@pytest.mark.asyncio
async def test_remove_case_insensitive(store):
    await store.replace(["Apple", "Banana", "Cherry"])
    assert await store.remove("banana") == ["Apple", "Cherry"]
    assert await store.remove("not-exists") == ["Apple", "Cherry"]
    assert await store.remove("") == ["Apple", "Cherry"]


@pytest.mark.asyncio
async def test_legacy_pure_list_format_supported(tmp_path):
    """兼容老格式：纯 list 而非 {items: [...]}"""
    from backend.app.services.focus_keywords_store import FocusKeywordsStore

    path = tmp_path / "focus.json"
    path.write_text(json.dumps(["old1", "old2"], ensure_ascii=False), encoding="utf-8")

    store = FocusKeywordsStore(store_file=str(path))
    assert await store.get() == ["old1", "old2"]


@pytest.mark.asyncio
async def test_corrupted_json_returns_empty(store, tmp_path):
    path = tmp_path / "focus_keywords.json"
    path.write_text("{ not valid", encoding="utf-8")

    assert await store.get() == []


@pytest.mark.asyncio
async def test_replace_writes_updated_at(store, tmp_path):
    await store.replace(["a", "b"])

    raw = json.loads((tmp_path / "focus_keywords.json").read_text(encoding="utf-8"))
    assert raw["items"] == ["a", "b"]
    assert "_updated_at" in raw
