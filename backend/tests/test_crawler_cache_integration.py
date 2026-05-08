"""CrawlerAgent 三层缓存集成测试：L1 命中 / L2 命中 / L3 回写。

完全 mock Redis 和 pgvector,不依赖真实服务。
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from backend.app.application.agents.base import AgentContext
from backend.app.application.agents import crawler_agent as crawler_mod
from backend.app.application.task_service import task_service
from backend.app.domain.task_context import TaskContextWriter, task_context_store


# ========================= 简化的 Fake 实现 =========================


class FakeKeywordCache:
    def __init__(self):
        self.store: Dict[str, List[Dict[str, Any]]] = {}
        self.ops: List[tuple] = []

    def _key(self, keywords):
        return ",".join(sorted(keywords))

    async def get(self, keywords):
        self.ops.append(("get", tuple(sorted(keywords))))
        return self.store.get(self._key(keywords))

    async def set(self, keywords, notes):
        self.ops.append(("set", tuple(sorted(keywords)), len(notes)))
        self.store[self._key(keywords)] = list(notes)
        return True


class FakeNotesStore:
    def __init__(self, preload_rows: List[Dict[str, Any]] = None):
        self._rows = list(preload_rows or [])
        self.add_calls: List[List[Dict[str, Any]]] = []

    async def ensure_schema(self):
        pass

    async def search_notes(
        self, keywords, top_k=30, query_embedding=None, recent_days=None
    ):
        kws = set(keywords or [])
        matching = [
            r for r in self._rows if set(r.get("source_keywords") or []) & kws
        ]
        return matching[:top_k]

    async def add_notes(self, notes, embeddings=None):
        self.add_calls.append(list(notes))
        for n in notes:
            self._rows.append(dict(n))
        return len(notes)


# ========================= helpers =========================


def _setup_task(
    monkeypatch,
    raw_input: str = "防脱精华",
    keywords=None,
    dimensions=None,
    competitor_keywords=None,
):
    res = task_service.create_task(
        owner_user_id="owner-cache",
        raw_input=raw_input,
        keywords=keywords or [raw_input],
        idempotency_key=None,
    )
    tid = res.record.task_id
    ctx = task_context_store.require(tid)
    payload: Dict[str, Any] = {
        "parsed": {
            "keywords": keywords or [raw_input],
            "dimensions": dimensions
            or {"industry": [], "competitor": [], "brand": []},
        },
    }
    if competitor_keywords is not None:
        payload["competitor_keywords"] = list(competitor_keywords)
    TaskContextWriter(ctx).write(
        "input_spec",
        payload,
        agent_id="test",
        merge=True,
    )
    return tid, ctx


class FakeBus:
    def __init__(self):
        self.events = []

    async def publish_event(self, *, task_id, type, payload, branch_id=None):  # noqa: A002
        self.events.append({"type": type, "payload": payload})


class FakeGateway:
    async def chat(self, *args, **kwargs):
        raise RuntimeError("should not be called in cache path")

    async def embed(self, *args, **kwargs):
        raise RuntimeError("should not be called in cache path")


# ========================= 测试 =========================


@pytest.mark.asyncio
async def test_l1_miss_when_llm_competitor_not_in_source_keywords(monkeypatch, enable_cache_for_test):
    """dimensions.competitor 含推断/任意竞品词时,L1 笔记的 source_keywords 必须也能命中该词,否则 miss→L3。"""
    tid, ctx = _setup_task(
        monkeypatch,
        raw_input="防脱精华",
        keywords=["防脱精华"],
        dimensions={
            "industry": ["防脱精华"],
            "competitor": ["费列罗"],
            "brand": [],
        },
    )
    cache_key = ",".join(sorted(["费列罗", "防脱精华"]))
    fake_cache = FakeKeywordCache()
    fake_cache.store[cache_key] = [
        {
            "note_id": f"n{i}",
            "title": f"cached {i}",
            "likes": 1000,
            "comments": 10,
            "collects": 50,
            "source_keywords": ["防脱精华"],
            "media_type": "image",
            "dimension": "industry",
            "dimensions_hit": [],
            "url": "",
            "desc": "",
        }
        for i in range(5)
    ]
    from backend.app.infrastructure.cache import keyword_cache

    monkeypatch.setattr(keyword_cache, "get_keyword_cache", lambda: fake_cache)
    monkeypatch.setattr(crawler_mod, "_resolve_cookies_str", lambda _u: "fake=1")
    monkeypatch.setattr(crawler_mod, "_INTER_GROUP_SLEEP", 0)
    monkeypatch.setattr(crawler_mod, "_ENRICH_ENABLED", False)

    call_count = {"n": 0}

    async def fake_collect(cookies, keywords, target, runtime_cfg):
        call_count["n"] += 1
        return [
            _FakeViralNote(note_id=f"fresh_{i}", title=f"fresh {i}", note_type="图集")
            for i in range(3)
        ]

    monkeypatch.setattr(crawler_mod, "_collect_one_dimension", fake_collect)

    agent = crawler_mod.CrawlerAgent(model_gateway_instance=FakeGateway(), event_bus=FakeBus())
    await agent.run(AgentContext(task_id=tid, task_context=ctx))

    out = ctx.get("crawler_output")
    assert out["cache_source"] == "L3"
    assert call_count["n"] >= 1


@pytest.mark.asyncio
async def test_l1_hit_skips_real_collection(monkeypatch, enable_cache_for_test):
    tid, ctx = _setup_task(monkeypatch)

    fake_cache = FakeKeywordCache()
    cached_notes = [
        {"note_id": f"n{i}", "title": f"cached {i}", "likes": 1000, "comments": 10,
         "collects": 50, "source_keywords": ["防脱精华"], "media_type": "image",
         "dimension": "industry", "dimensions_hit": [], "url": "", "desc": ""}
        for i in range(5)
    ]
    fake_cache.store["防脱精华"] = cached_notes

    from backend.app.infrastructure.cache import keyword_cache

    monkeypatch.setattr(keyword_cache, "get_keyword_cache", lambda: fake_cache)
    # 保险：绝不应该调到 L2 / L3
    monkeypatch.setattr(crawler_mod, "_collect_one_dimension", _should_not_call)
    monkeypatch.setattr(crawler_mod, "_resolve_cookies_str", lambda _u: "fake=1")

    agent = crawler_mod.CrawlerAgent(model_gateway_instance=FakeGateway(), event_bus=FakeBus())
    await agent.run(AgentContext(task_id=tid, task_context=ctx))

    out = ctx.get("crawler_output")
    assert out["cache_source"] == "L1"
    assert out["source"] == "cache"
    assert out["sample_count"] == 5
    # L1 命中不应调 collect
    assert ("set", ("防脱精华",), 5) not in fake_cache.ops  # 不需要重写 L1
    assert ("get", ("防脱精华",)) in fake_cache.ops


@pytest.mark.asyncio
async def test_l2_hit_when_top_k_window_skews_to_main_keyword_only(
    monkeypatch, enable_cache_for_test
):
    """联合 L2 查询的 LIMIT 内可能只有主词行;库中另有竞品词行时,应按词补查后仍命中 L2。"""
    tid, ctx = _setup_task(
        monkeypatch,
        raw_input="防脱精华",
        keywords=["防脱精华"],
        dimensions={
            "industry": ["防脱精华"],
            "competitor": ["费列罗"],
            "brand": [],
        },
    )
    rows_main = [
        {
            "note_id": f"m{i}",
            "title": f"m{i}",
            "likes": 5000 - i,
            "comments": 50,
            "collects": 50,
            "source_keywords": ["防脱精华"],
            "media_type": "image",
            "url": "",
            "desc": "",
        }
        for i in range(15)
    ]
    rows_comp = [
        {
            "note_id": f"c{i}",
            "title": f"c{i}",
            "likes": 100 - i,
            "comments": 5,
            "collects": 5,
            "source_keywords": ["费列罗"],
            "media_type": "image",
            "url": "",
            "desc": "",
        }
        for i in range(15)
    ]
    fake_store = FakeNotesStore(preload_rows=rows_main + rows_comp)
    fake_cache = FakeKeywordCache()

    from backend.app.infrastructure.cache import keyword_cache
    from backend.app.infrastructure.storage import notes_vector_store as nvs_mod

    monkeypatch.setattr(keyword_cache, "get_keyword_cache", lambda: fake_cache)
    monkeypatch.setattr(nvs_mod, "get_notes_vector_store", lambda: fake_store)
    monkeypatch.setattr(crawler_mod, "_L2_TOP_K", 15)
    monkeypatch.setattr(crawler_mod, "_resolve_cookies_str", lambda _u: "fake=1")

    agent = crawler_mod.CrawlerAgent(model_gateway_instance=FakeGateway(), event_bus=FakeBus())
    await agent.run(AgentContext(task_id=tid, task_context=ctx))

    out = ctx.get("crawler_output")
    assert out["cache_source"] == "L2"
    assert out["sample_count"] >= 20


@pytest.mark.asyncio
async def test_l2_hit_when_l1_miss(monkeypatch, enable_cache_for_test):
    tid, ctx = _setup_task(monkeypatch)

    fake_cache = FakeKeywordCache()  # 空 L1
    # L2 预置 12 条命中数据（>= _L2_MIN_HIT=10）
    fake_store = FakeNotesStore(
        preload_rows=[
            {
                "note_id": f"hist_{i}",
                "title": f"hist {i}",
                "likes": 2000,
                "comments": 30,
                "collects": 80,
                "source_keywords": ["防脱精华"],
                "media_type": "image",
                "url": "",
                "desc": "",
            }
            for i in range(12)
        ]
    )

    from backend.app.infrastructure.cache import keyword_cache
    from backend.app.infrastructure.storage import notes_vector_store as nvs_mod

    monkeypatch.setattr(keyword_cache, "get_keyword_cache", lambda: fake_cache)
    monkeypatch.setattr(nvs_mod, "get_notes_vector_store", lambda: fake_store)
    monkeypatch.setattr(crawler_mod, "_collect_one_dimension", _should_not_call)

    agent = crawler_mod.CrawlerAgent(model_gateway_instance=FakeGateway(), event_bus=FakeBus())
    await agent.run(AgentContext(task_id=tid, task_context=ctx))

    out = ctx.get("crawler_output")
    assert out["cache_source"] == "L2"
    assert out["sample_count"] == 12
    # L2 命中应回填 L1
    assert any(op[0] == "set" for op in fake_cache.ops)


@pytest.mark.asyncio
async def test_cache_miss_when_competitor_kw_not_covered(monkeypatch, enable_cache_for_test):
    """行业词有 L1/L2 数据但竞品词无 source_keywords 覆盖时,不得走缓存,应落 L3。"""
    tid, ctx = _setup_task(
        monkeypatch,
        raw_input="防脱精华",
        keywords=["防脱精华"],
        dimensions={
            "industry": ["防脱精华"],
            "competitor": ["费列罗"],
            "brand": [],
        },
    )
    both = sorted(["费列罗", "防脱精华"])
    cache_key = ",".join(both)

    fake_cache = FakeKeywordCache()
    fake_cache.store[cache_key] = [
        {
            "note_id": f"n{i}",
            "title": f"c{i}",
            "likes": 100,
            "comments": 1,
            "collects": 1,
            "source_keywords": ["防脱精华"],
            "media_type": "image",
            "dimension": "industry",
            "dimensions_hit": [],
            "url": "",
            "desc": "",
        }
        for i in range(8)
    ]
    fake_store = FakeNotesStore(
        preload_rows=[
            {
                "note_id": f"hist_{i}",
                "title": "h",
                "likes": 10,
                "comments": 1,
                "collects": 1,
                "source_keywords": ["防脱精华"],
                "media_type": "image",
                "url": "",
                "desc": "",
            }
            for i in range(15)
        ]
    )

    from backend.app.infrastructure.cache import keyword_cache
    from backend.app.infrastructure.storage import notes_vector_store as nvs_mod

    monkeypatch.setattr(keyword_cache, "get_keyword_cache", lambda: fake_cache)
    monkeypatch.setattr(nvs_mod, "get_notes_vector_store", lambda: fake_store)
    monkeypatch.setattr(crawler_mod, "_resolve_cookies_str", lambda _u: "fake=1")
    monkeypatch.setattr(crawler_mod, "_INTER_GROUP_SLEEP", 0)
    monkeypatch.setattr(crawler_mod, "_ENRICH_ENABLED", False)

    call_count = {"n": 0}

    async def fake_collect(cookies, keywords, target, runtime_cfg):
        call_count["n"] += 1
        return [
            _FakeViralNote(note_id=f"fresh_{i}", title=f"fresh {i}", note_type="图集")
            for i in range(3)
        ]

    monkeypatch.setattr(crawler_mod, "_collect_one_dimension", fake_collect)

    agent = crawler_mod.CrawlerAgent(model_gateway_instance=FakeGateway(), event_bus=FakeBus())
    await agent.run(AgentContext(task_id=tid, task_context=ctx))

    out = ctx.get("crawler_output")
    assert out["cache_source"] == "L3"
    assert call_count["n"] >= 1


@pytest.mark.asyncio
async def test_l2_miss_when_hits_below_threshold(monkeypatch, enable_cache_for_test):
    """L2 历史只有 5 条同词,不足 10 条阈值,降级到 L3。"""
    tid, ctx = _setup_task(monkeypatch)

    fake_cache = FakeKeywordCache()
    fake_store = FakeNotesStore(
        preload_rows=[
            {
                "note_id": f"hist_{i}",
                "source_keywords": ["防脱精华"],
                "title": "x",
                "likes": 0, "comments": 0, "collects": 0,
                "media_type": "image", "url": "", "desc": "",
            }
            for i in range(5)  # < 10 个
        ]
    )

    from backend.app.infrastructure.cache import keyword_cache
    from backend.app.infrastructure.storage import notes_vector_store as nvs_mod

    monkeypatch.setattr(keyword_cache, "get_keyword_cache", lambda: fake_cache)
    monkeypatch.setattr(nvs_mod, "get_notes_vector_store", lambda: fake_store)

    # L3 mock：cookies + 采集
    monkeypatch.setattr(crawler_mod, "_resolve_cookies_str", lambda _u: "fake=1")
    monkeypatch.setattr(crawler_mod, "_INTER_GROUP_SLEEP", 0)
    monkeypatch.setattr(crawler_mod, "_ENRICH_ENABLED", False)

    call_count = {"n": 0}

    async def fake_collect(cookies, keywords, target, runtime_cfg):
        call_count["n"] += 1
        return [
            _FakeViralNote(note_id=f"fresh_{i}", title=f"fresh {i}", note_type="图集")
            for i in range(3)
        ]

    monkeypatch.setattr(crawler_mod, "_collect_one_dimension", fake_collect)

    agent = crawler_mod.CrawlerAgent(model_gateway_instance=FakeGateway(), event_bus=FakeBus())
    await agent.run(AgentContext(task_id=tid, task_context=ctx))

    out = ctx.get("crawler_output")
    # L2 部分命中但不够,走 L3
    assert out["cache_source"] == "L3"
    assert call_count["n"] >= 1  # L3 真正调了
    # L3 成功后应回写 L1 + L2
    assert any(op[0] == "set" for op in fake_cache.ops)
    assert len(fake_store.add_calls) >= 1


@pytest.mark.asyncio
async def test_cache_disabled_goes_straight_to_l3(monkeypatch):
    """_CACHE_ENABLED=False(conftest 默认) 时跳过整条缓存路径。"""
    tid, ctx = _setup_task(monkeypatch)

    # 不启 enable_cache_for_test,所以缓存关闭
    monkeypatch.setattr(crawler_mod, "_resolve_cookies_str", lambda _u: "fake=1")
    monkeypatch.setattr(crawler_mod, "_INTER_GROUP_SLEEP", 0)
    monkeypatch.setattr(crawler_mod, "_ENRICH_ENABLED", False)

    async def fake_collect(cookies, keywords, target, runtime_cfg):
        return [_FakeViralNote(note_id="x1", title="x", note_type="图集")]

    monkeypatch.setattr(crawler_mod, "_collect_one_dimension", fake_collect)

    agent = crawler_mod.CrawlerAgent(model_gateway_instance=FakeGateway(), event_bus=FakeBus())
    await agent.run(AgentContext(task_id=tid, task_context=ctx))

    out = ctx.get("crawler_output")
    assert out["cache_source"] == "L3"


@pytest.mark.asyncio
async def test_competitor_multi_kw_per_word_cache_crawl_miss_only(
    monkeypatch, enable_cache_for_test
):
    """竞品多词时逐词判库:已命中词走 L1,仅对未命中词发起补采(不把整组并成一把 key)。"""
    tid, ctx = _setup_task(
        monkeypatch,
        raw_input="防脱精华",
        keywords=["防脱精华"],
        dimensions={
            "industry": ["防脱精华"],
            "competitor": ["词A", "词B"],
            "brand": [],
        },
    )
    fake_cache = FakeKeywordCache()
    fake_cache.store["防脱精华"] = [
        {
            "note_id": f"m{i}",
            "title": f"m{i}",
            "likes": 1000,
            "comments": 1,
            "collects": 1,
            "source_keywords": ["防脱精华"],
            "media_type": "image",
            "dimension": "industry",
            "dimensions_hit": [],
            "url": "",
            "desc": "",
        }
        for i in range(5)
    ]
    fake_cache.store["词A"] = [
        {
            "note_id": f"a{i}",
            "title": f"a{i}",
            "likes": 500,
            "comments": 1,
            "collects": 1,
            "source_keywords": ["词A"],
            "media_type": "image",
            "dimension": "competitor",
            "dimensions_hit": [],
            "url": "",
            "desc": "",
        }
        for i in range(5)
    ]

    from backend.app.infrastructure.cache import keyword_cache

    monkeypatch.setattr(keyword_cache, "get_keyword_cache", lambda: fake_cache)
    monkeypatch.setattr(crawler_mod, "_resolve_cookies_str", lambda _u: "fake=1")
    monkeypatch.setattr(crawler_mod, "_INTER_GROUP_SLEEP", 0)
    monkeypatch.setattr(crawler_mod, "_ENRICH_ENABLED", False)

    collect_calls: List[List[str]] = []

    async def fake_collect(cookies, keywords, target, runtime_cfg):
        collect_calls.append(list(keywords))
        return [
            _FakeViralNote(
                note_id="b1",
                title="fresh b",
                note_type="图集",
                source_keywords=["词B"],
            )
        ]

    monkeypatch.setattr(crawler_mod, "_collect_one_dimension", fake_collect)

    agent = crawler_mod.CrawlerAgent(model_gateway_instance=FakeGateway(), event_bus=FakeBus())
    await agent.run(AgentContext(task_id=tid, task_context=ctx))

    out = ctx.get("crawler_output")
    assert out["cache_source"] == "L3"
    assert collect_calls == [["词B"]], collect_calls
    trace = out.get("competitor_cache_trace") or {}
    assert trace.get("competitor_cache_miss_keywords") == ["词B"]
    comp_samples = out["samples_by_dimension"].get("competitor") or []
    assert len(comp_samples) >= 5


@pytest.mark.asyncio
async def test_competitor_comma_in_one_string_still_resolves_each_word_l1(
    monkeypatch, enable_cache_for_test
):
    """dimensions.competitor 为「A,B,C」单条时拆成多词,逐词 L1 而非只认第一个。"""
    tid, ctx = _setup_task(
        monkeypatch,
        raw_input="防脱",
        keywords=["防脱"],
        dimensions={
            "industry": ["防脱"],
            "competitor": ["词A,词B,词C"],
            "brand": [],
        },
    )
    fake_cache = FakeKeywordCache()
    for w in ("防脱", "词A", "词B", "词C"):
        fake_cache.store[w] = [
            {
                "note_id": f"{w}{i}",
                "title": w,
                "likes": 10,
                "comments": 1,
                "collects": 1,
                "source_keywords": [w],
                "media_type": "image",
                "dimension": "industry" if w == "防脱" else "competitor",
                "dimensions_hit": [],
                "url": "",
                "desc": "",
            }
            for i in range(5)
        ]

    from backend.app.infrastructure.cache import keyword_cache

    monkeypatch.setattr(keyword_cache, "get_keyword_cache", lambda: fake_cache)
    monkeypatch.setattr(crawler_mod, "_collect_one_dimension", _should_not_call)
    monkeypatch.setattr(crawler_mod, "_resolve_cookies_str", lambda _u: "fake=1")

    agent = crawler_mod.CrawlerAgent(model_gateway_instance=FakeGateway(), event_bus=FakeBus())
    await agent.run(AgentContext(task_id=tid, task_context=ctx))

    out = ctx.get("crawler_output")
    assert out["source"] == "cache"
    gets = [op for op in fake_cache.ops if op[0] == "get"]
    assert ("get", ("词A",)) in gets
    assert ("get", ("词B",)) in gets
    assert ("get", ("词C",)) in gets


@pytest.mark.asyncio
async def test_top_level_competitor_keywords_merged_into_crawler_dims(
    monkeypatch, enable_cache_for_test
):
    """input_spec.competitor_keywords 与 parsed 合并后,竞品多词仍逐词判缓存。"""
    tid, ctx = _setup_task(
        monkeypatch,
        raw_input="防脱",
        keywords=["防脱"],
        dimensions={
            "industry": ["防脱"],
            "competitor": [],
            "brand": [],
        },
        competitor_keywords=["K1", "K2"],
    )
    fake_cache = FakeKeywordCache()
    for w in ("防脱", "K1", "K2"):
        fake_cache.store[w] = [
            {
                "note_id": f"{w}{i}",
                "title": w,
                "likes": 10,
                "comments": 1,
                "collects": 1,
                "source_keywords": [w],
                "media_type": "image",
                "url": "",
                "desc": "",
            }
            for i in range(5)
        ]

    from backend.app.infrastructure.cache import keyword_cache

    monkeypatch.setattr(keyword_cache, "get_keyword_cache", lambda: fake_cache)
    monkeypatch.setattr(crawler_mod, "_collect_one_dimension", _should_not_call)
    monkeypatch.setattr(crawler_mod, "_resolve_cookies_str", lambda _u: "fake=1")

    agent = crawler_mod.CrawlerAgent(model_gateway_instance=FakeGateway(), event_bus=FakeBus())
    await agent.run(AgentContext(task_id=tid, task_context=ctx))

    out = ctx.get("crawler_output")
    assert out["source"] == "cache"
    gets = [op for op in fake_cache.ops if op[0] == "get"]
    assert ("get", ("K1",)) in gets
    assert ("get", ("K2",)) in gets


@pytest.mark.asyncio
async def test_industry_competitor_same_multiset_still_per_word_l1_not_batched(
    monkeypatch, enable_cache_for_test
):
    """行业/竞品词表 multiset 相同(多品牌)时,不得用整组 key 一次 get;已逐词命中者不进采集。"""
    brands = ["华为", "小米", "戴尔", "联想"]
    tid, ctx = _setup_task(
        monkeypatch,
        raw_input="手机",
        keywords=["手机"],
        dimensions={
            "industry": list(brands),
            "competitor": list(brands),
            "brand": [],
        },
    )
    fake_cache = FakeKeywordCache()
    for w in ("华为", "小米"):
        fake_cache.store[w] = [
            {
                "note_id": f"{w}1",
                "title": w,
                "likes": 10,
                "comments": 1,
                "collects": 1,
                "source_keywords": [w],
                "media_type": "image",
                "url": "",
                "desc": "",
            }
        ]

    from backend.app.infrastructure.cache import keyword_cache

    monkeypatch.setattr(keyword_cache, "get_keyword_cache", lambda: fake_cache)
    monkeypatch.setattr(crawler_mod, "_resolve_cookies_str", lambda _u: "fake=1")
    monkeypatch.setattr(crawler_mod, "_INTER_GROUP_SLEEP", 0)
    monkeypatch.setattr(crawler_mod, "_ENRICH_ENABLED", False)

    collect_calls: List[List[str]] = []

    async def fake_collect(cookies, keywords, target, runtime_cfg):
        collect_calls.append(list(keywords))
        return [_FakeViralNote(note_id="x1", title="x", note_type="图集")]

    monkeypatch.setattr(crawler_mod, "_collect_one_dimension", fake_collect)

    agent = crawler_mod.CrawlerAgent(model_gateway_instance=FakeGateway(), event_bus=FakeBus())
    await agent.run(AgentContext(task_id=tid, task_context=ctx))

    assert collect_calls, collect_calls
    kws = collect_calls[0]
    assert "华为" not in kws and "小米" not in kws
    assert "戴尔" in kws and "联想" in kws


# ========================= helpers =========================


class _FakeViralNote:
    def __init__(self, **kwargs):
        self.note_id = kwargs.get("note_id", "n1")
        self.note_url = kwargs.get("note_url", f"https://x/{self.note_id}")
        self.note_type = kwargs.get("note_type", "图集")
        self.title = kwargs.get("title", "t")
        self.desc = kwargs.get("desc", "")
        self.liked_count = kwargs.get("liked_count", 100)
        self.comment_count = kwargs.get("comment_count", 10)
        self.collected_count = kwargs.get("collected_count", 20)
        self.interaction_score = kwargs.get("interaction_score", 130)
        self.image_list = kwargs.get("image_list", [])
        self.video_cover = None
        self.video_addr = None
        self.nickname = "u"
        self.source_keywords = kwargs.get("source_keywords", [])


async def _should_not_call(*args, **kwargs):
    raise AssertionError("这个路径在本测试里不应被调到")
