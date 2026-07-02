"""阶段 4.1 真实化 Agent 单测：mock 所有外部 I/O,验证三分支。

对每个 Agent 覆盖：
- success: 模型/采集/RAG/Vision 都返回有效数据 → Agent 产出结构化输出
- retry_then_success: 第一次 JSON 解析失败,第二次成功
- full_fallback: 两次都失败 → Agent 走 fallback/stub

不消耗真实 API token,只测代码路径与聚合逻辑。
"""

from __future__ import annotations

import asyncio
import types
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import pytest

from backend.app.application.agents.base import AgentContext
from backend.app.domain.task_context import TaskContext, TaskContextWriter


# ---------- helpers ----------

def _make_context(initial_input_spec: Dict[str, Any] | None = None, extra: Dict[str, Any] | None = None) -> tuple[TaskContext, AgentContext]:
    ctx = TaskContext(task_id="t_eval")
    if initial_input_spec is not None:
        TaskContextWriter(ctx).write(
            "input_spec", initial_input_spec, agent_id="test", note="seed"
        )
    extra = extra or {}
    for part, data in extra.items():
        TaskContextWriter(ctx).write(part, data, agent_id="test", note="seed")
    return ctx, AgentContext(task_id="t_eval", task_context=ctx)


class FakeGateway:
    """可脚本化的 ModelGateway mock。"""

    def __init__(self, sequence: List[Any]):
        # sequence 元素可以是 str (直接返回 content) / Exception (raise) / dict (返回 response dict)
        self._sequence = list(sequence)
        self.calls: List[Dict[str, Any]] = []

    async def chat(self, *, agent_id, messages, modality="text", task_id=None, overrides=None):
        self.calls.append(
            {
                "agent_id": agent_id,
                "modality": modality,
                "overrides": overrides,
                "messages": messages,
            }
        )
        if not self._sequence:
            raise RuntimeError("FakeGateway: out of scripted responses")
        item = self._sequence.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, dict):
            return item
        return {"content": item, "profile_id": "p", "model_name": "m", "provider": "mock", "usage": {}}

    async def embed(self, *, agent_id, texts, task_id=None):
        return {"vectors": [[0.0] * 4 for _ in texts]}


class FakeBus:
    def __init__(self):
        self.events: List[Dict[str, Any]] = []

    async def publish_event(self, *, task_id, type, payload, branch_id=None):  # noqa: A002
        self.events.append({"task_id": task_id, "type": type, "payload": payload, "branch_id": branch_id})


# ---------- InputParserAgent ----------

@pytest.mark.asyncio
async def test_input_parser_success_extracts_json():
    from backend.app.application.agents.input_parser_agent import InputParserAgent

    gateway = FakeGateway([
        '{"keywords": ["巧克力", "北欧"], "dimensions": {"brand": ["Fazer"], "competitor": [], "industry": ["甜品"]}, "adjustments": ["多一点开箱"], "confidence": 0.9}'
    ])
    agent = InputParserAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context({"raw_input": "分析北欧巧克力 Fazer 的爆款", "keywords": []})

    result = await agent.run(ac)
    assert result.ok
    parsed = ctx.get("input_spec")["parsed"]
    assert parsed["keywords"] == ["巧克力", "北欧"]
    assert parsed["dimensions"]["brand"] == ["Fazer"]
    assert parsed["confidence"] == 0.9


@pytest.mark.asyncio
async def test_input_parser_retry_then_success():
    from backend.app.application.agents.input_parser_agent import InputParserAgent

    gateway = FakeGateway([
        "乱七八糟,不是 JSON",
        '{"keywords": ["k1"], "dimensions": {"brand":[], "competitor":[], "industry":[]}, "adjustments":[], "confidence":0.5}',
    ])
    agent = InputParserAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context({"raw_input": "xxx", "keywords": []})
    await agent.run(ac)

    parsed = ctx.get("input_spec")["parsed"]
    assert parsed["keywords"] == ["k1"]
    assert len(gateway.calls) == 2


@pytest.mark.asyncio
async def test_input_parser_two_failures_falls_back():
    from backend.app.application.agents.input_parser_agent import InputParserAgent

    gateway = FakeGateway(["不是 JSON", "也不是 JSON"])
    agent = InputParserAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context({"raw_input": "xxx", "keywords": ["hint"]})
    await agent.run(ac)

    parsed = ctx.get("input_spec")["parsed"]
    # 两次失败后使用 hint_keywords 作为兜底
    assert parsed["keywords"] == ["hint"]


def test_collect_api_competitor_keywords():
    """API 字段 competitor_keywords / advanced_config 正确收集。"""
    from backend.app.application.agents.input_parser_agent import (
        collect_api_competitor_keywords,
    )

    spec = {
        "raw_input": "",
        "competitor_keywords": [" 费列罗 ", "德芙"],
        "advanced_config": {"competitor_keywords": ["德芙", "好时"]},
    }
    assert collect_api_competitor_keywords(spec) == ["费列罗", "德芙", "好时"]


@pytest.mark.asyncio
async def test_input_parser_api_competitor_overrides_llm():
    """API 接口 competitor_keywords 优先于 LLM 推断,LLM 推断词被丢弃。"""
    from backend.app.application.agents.input_parser_agent import InputParserAgent

    gateway = FakeGateway([
        '{"keywords": ["巧克力"], "dimensions": {"brand": [], "competitor": ["瑞士莲"], "industry": []}, "adjustments": [], "confidence": 0.8, "competitor_source": "llm_inferred"}'
    ])
    agent = InputParserAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context(
        {
            "raw_input": "分析巧克力市场",
            "keywords": ["巧克力"],
            "competitor_keywords": ["费列罗", "德芙"],
        }
    )
    await agent.run(ac)
    parsed = ctx.get("input_spec")["parsed"]
    # API 竞品词优先,LLM 推断的"瑞士莲"被丢弃
    assert parsed["dimensions"]["competitor"] == ["费列罗", "德芙"]
    assert parsed["competitor_source"] == "api"


@pytest.mark.asyncio
async def test_input_parser_llm_user_explicit_keeps_only_user_competitors():
    """LLM 判断用户明确提到了竞品 → competitor_source=user_explicit,只保留 LLM 提取的竞品。"""
    from backend.app.application.agents.input_parser_agent import InputParserAgent

    gateway = FakeGateway([
        '{"keywords": ["巧克力"], "dimensions": {"brand": [], "competitor": ["费列罗", "德芙"], "industry": ["甜品"]}, "adjustments": [], "confidence": 0.9, "competitor_source": "user_explicit"}'
    ])
    agent = InputParserAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context(
        {
            "raw_input": "分析巧克力市场，竞品看费列罗和德芙",
            "keywords": ["巧克力"],
        }
    )
    await agent.run(ac)
    parsed = ctx.get("input_spec")["parsed"]
    # LLM 正确识别用户提到的竞品,不额外推断
    assert parsed["dimensions"]["competitor"] == ["费列罗", "德芙"]
    assert parsed["competitor_source"] == "user_explicit"


@pytest.mark.asyncio
async def test_input_parser_user_explicit_truncates_to_three():
    """user_explicit 路径下 LLM 若返回超过 3 个竞品词,本地截断为 3。"""
    from backend.app.application.agents.input_parser_agent import InputParserAgent

    gateway = FakeGateway([
        '{"keywords": ["巧克力"], "dimensions": {"brand": [], "competitor": ["A", "B", "C", "D", "E"], "industry": []}, "adjustments": [], "confidence": 0.9, "competitor_source": "user_explicit"}'
    ])
    agent = InputParserAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context(
        {
            "raw_input": "对比 A B C D E",
            "keywords": ["巧克力"],
        }
    )
    await agent.run(ac)
    parsed = ctx.get("input_spec")["parsed"]
    assert parsed["dimensions"]["competitor"] == ["A", "B", "C"]


@pytest.mark.asyncio
async def test_input_parser_llm_inferred_competitors_preserved():
    """用户没提竞品 → LLM 自行推断 → competitor_source=llm_inferred；非 API 路径最多保留 3 个竞品词。"""
    from backend.app.application.agents.input_parser_agent import InputParserAgent

    gateway = FakeGateway([
        '{"keywords": ["笔记本电脑"], "dimensions": {"brand": [], "competitor": ["华硕", "联想", "戴尔", "惠普", "宏碁"], "industry": ["电脑"]}, "adjustments": [], "confidence": 0.8, "competitor_source": "llm_inferred"}'
    ])
    agent = InputParserAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context(
        {
            "raw_input": "分析笔记本电脑市场",
            "keywords": ["笔记本电脑"],
        }
    )
    await agent.run(ac)
    parsed = ctx.get("input_spec")["parsed"]
    assert parsed["dimensions"]["competitor"] == ["华硕", "联想", "戴尔"]
    assert parsed["competitor_source"] == "llm_inferred"


# ---------- InsightAgent (4.3pre.3 原生 schema) ----------

@pytest.mark.asyncio
async def test_insight_agent_outputs_native_schema():
    """4.3pre.3: InsightAgent 改为一次 LLM 调用 + 本地聚合,输出新原生 schema
    (content_direction / pain_points_top / seo_aggregation / stats_axis_label),
    不再产出旧 industry/competitor/brand 三段式适配。
    """
    from backend.app.application.agents.insight_agent import InsightAgent

    gateway = FakeGateway([
        '{"content_direction": {"top_direction": "口播单推", '
        '"summary_points": ["方向集中", "热度上升"], "highlight": "口播单推占主导"}, '
        '"seo_aggregation": {"differentiation_advice": "聚焦细纹场景"}}'
    ])
    agent = InsightAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context(
        {"raw_input": "抗老精华", "keywords": ["抗老"]},
        extra={
            "viral_model_output": {
                "models": [
                    {"name": "口播单推型", "coverage": 0.4, "avg_interaction": 8000},
                    {"name": "干货分享型", "coverage": 0.3, "avg_interaction": 6000},
                ],
            },
            "multimodal_output": {
                "annotations": {
                    "n1": {"pain_keywords": "暗沉,松垮"},
                    "n2": {"pain_keywords": "法令纹/暗沉"},
                }
            },
            "crawler_output": {
                "sources": {
                    "competitor": [
                        {"title": "竞品 A 30天抗老见效", "pain_keywords": "细纹,抗老"},
                        {"title": "竞品 B 一支精华全搞定", "pain_keywords": "精华,抗老"},
                    ]
                }
            },
        },
    )
    await agent.run(ac)

    out = ctx.get("semantic_output")
    # 仅调用一次 LLM
    assert len(gateway.calls) == 1
    # 新原生 schema 顶级字段齐全
    assert set(out.keys()) >= {
        "content_direction",
        "pain_points_top",
        "seo_aggregation",
        "stats_axis_label",
    }
    # 旧 3 段字段不再存在(破坏性切换)
    assert "industry" not in out
    assert "competitor" not in out
    assert "brand" not in out
    # content_direction 正确回填
    assert out["content_direction"]["highlight"] == "口播单推占主导"
    assert out["content_direction"]["top_direction"] == "口播单推"
    # pain_points_top 带频次
    pain = out["pain_points_top"]
    assert isinstance(pain, list) and pain, "痛点 Top 不应为空"
    assert {"keyword", "count"} <= set(pain[0].keys())
    # 暗沉出现 2 次(n1 + n2),应排第一
    assert pain[0]["keyword"] == "暗沉"
    assert pain[0]["count"] == 2
    # seo_aggregation 来自 deterministic 聚合
    seo = out["seo_aggregation"]
    assert "core_keywords" in seo and "long_tail" in seo
    assert seo["differentiation_advice"] == "聚焦细纹场景"
    # stats_axis_label 默认值(LLM 未给,任务元数据未指定)
    assert out["stats_axis_label"] == "高频痛点 / 议程"


@pytest.mark.asyncio
async def test_insight_agent_llm_fails_still_produces_deterministic_fields():
    """LLM 两次失败 → content_direction 多为空,但 deterministic 聚合(pain/seo)仍能输出。"""
    from backend.app.application.agents.insight_agent import InsightAgent

    gateway = FakeGateway(["乱码", "乱码"])  # 1 次调用 × 2 次 retry
    agent = InsightAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context(
        {"keywords": []},
        extra={
            "viral_model_output": {"models": []},
            "multimodal_output": {
                "annotations": {
                    "n1": {"pain_keywords": "暗沉,松垮,法令纹"},
                    "n2": {"pain_keywords": "暗沉/松垮"},
                }
            },
            "crawler_output": {},
        },
    )
    await agent.run(ac)

    out = ctx.get("semantic_output")
    # LLM 失败 → content_direction 字段存在但各子项为空
    assert out["content_direction"]["highlight"] == ""
    # pain_points_top 仍能从 annotations 聚合得到,且暗沉/松垮 top
    keywords = [p["keyword"] for p in out["pain_points_top"]]
    assert "暗沉" in keywords
    assert "松垮" in keywords
    # 默认轴名
    assert out["stats_axis_label"] == "高频痛点 / 议程"


@pytest.mark.asyncio
async def test_insight_agent_axis_label_from_task_metadata_overrides_default():
    """任务元数据(advanced_config.stats_axis_label)可覆盖默认 "高频痛点 / 议程"。"""
    from backend.app.application.agents.insight_agent import InsightAgent

    gateway = FakeGateway(["{}"])
    agent = InsightAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context(
        {
            "keywords": [],
            "advanced_config": {"stats_axis_label": "皮肤问题"},
        },
        extra={
            "viral_model_output": {"models": []},
            "multimodal_output": {"annotations": {}},
            "crawler_output": {},
        },
    )
    await agent.run(ac)

    out = ctx.get("semantic_output")
    assert out["stats_axis_label"] == "皮肤问题"


# ---------- RAGAgent ----------

@dataclass
class FakeRagResult:
    doc_id: str
    chunk_index: int
    text: str
    score: float
    metadata: Dict[str, Any]


class FakeRagService:
    def __init__(self, results: Dict[str, List[FakeRagResult]]):
        self._results = results
        self.calls: List[str] = []

    def search(self, query, domains=None, top_k=5, min_score=0.0):  # noqa: ARG002
        self.calls.append(query)
        return self._results.get(query, [])


@pytest.mark.asyncio
async def test_rag_agent_queries_and_deduplicates(monkeypatch):
    from backend.app.application.agents import rag_agent as rag_mod

    # Gateway: 查询改写返回两个 query
    gateway = FakeGateway([
        '{"rewritten_queries": ["q1", "q2"], "reasoning": "r"}',
    ])

    fake_service = FakeRagService(
        {
            "q1": [
                FakeRagResult("d1", 0, "rule1 text", 0.9, {"title": "Rule1"}),
                FakeRagResult("d2", 1, "rule2 text", 0.7, {"title": "Rule2"}),
            ],
            "q2": [
                # d1/0 同 key,保留高分(q1 的 0.9 > q2 的 0.6)
                FakeRagResult("d1", 0, "rule1 text (again)", 0.6, {"title": "Rule1"}),
                FakeRagResult("d3", 2, "rule3 text", 0.8, {"title": "Rule3"}),
            ],
        }
    )
    monkeypatch.setattr(rag_mod, "_get_rag_service", lambda: fake_service)

    agent = rag_mod.RAGAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context(
        {"raw_input": "北欧巧克力", "keywords": ["巧克力"]}
    )
    await agent.run(ac)

    out = ctx.get("rag_output")
    assert out["rewritten_queries"] == ["q1", "q2"]
    # 3 个唯一规则(d1/d2/d3);d1 保留高分 0.9
    assert len(out["business_rules"]) == 3
    # rule_id 形如 "kb-<doc_id[:12]>-<chunk_index>"
    rule_ids = {r["rule_id"] for r in out["business_rules"]}
    assert "kb-d1-0" in rule_ids and "kb-d2-1" in rule_ids and "kb-d3-2" in rule_ids
    # 按 score 排序时 d1 应该在第一(0.9 > 0.8 > 0.7)
    assert out["business_rules"][0]["rule_id"] == "kb-d1-0"
    assert out["business_rules"][0]["score"] == 0.9
    # hits 里保留 doc_id 字段
    hits_doc_ids = [h["doc_id"] for h in out["hits"]]
    assert "d1" in hits_doc_ids


@pytest.mark.asyncio
async def test_rag_agent_falls_back_when_service_unavailable(monkeypatch):
    from backend.app.application.agents import rag_agent as rag_mod

    monkeypatch.setattr(rag_mod, "_get_rag_service", lambda: None)
    gateway = FakeGateway(['{"rewritten_queries": ["q"], "reasoning": "x"}'])

    agent = rag_mod.RAGAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context({"raw_input": "x", "keywords": ["k"]})
    await agent.run(ac)

    out = ctx.get("rag_output")
    # service 未就绪 → 回落占位数据
    rule_ids = {r["rule_id"] for r in out["business_rules"]}
    assert "rule-compliance-01" in rule_ids


# ---------- CrawlerAgent ----------


class _FakeViralNote:
    """模拟 ViralNote 属性访问。"""

    def __init__(self, **kwargs):
        self.note_id = kwargs.get("note_id", "n1")
        self.note_url = kwargs.get("note_url", "https://x/1")
        self.note_type = kwargs.get("note_type", "图集")
        self.title = kwargs.get("title", "标题")
        self.desc = kwargs.get("desc", "")
        self.liked_count = kwargs.get("liked_count", 1000)
        self.comment_count = kwargs.get("comment_count", 50)
        self.collected_count = kwargs.get("collected_count", 200)
        self.interaction_score = kwargs.get("interaction_score", 1250)
        self.image_list = kwargs.get("image_list", ["https://img/1"])
        self.video_cover = kwargs.get("video_cover", None)
        self.video_addr = kwargs.get("video_addr", None)
        self.nickname = kwargs.get("nickname", "u")
        self.source_keywords = kwargs.get("source_keywords", [])


@pytest.mark.asyncio
async def test_crawler_agent_dimensions_distinct_keywords(monkeypatch):
    """三维度 keywords 各不相同 → 每组独立采集一次(共 3 次),串行执行。"""
    from backend.app.application.agents import crawler_agent as crawler_mod
    from backend.app.domain.task_context import task_context_store

    # 先真实把任务存进 task_repository(因为 agent 用了 task_repository.get)
    from backend.app.application.task_service import task_service
    res = task_service.create_task(
        owner_user_id="owner-1",
        raw_input="x",
        keywords=["巧克力", "北欧"],
        idempotency_key=None,
    )
    tid = res.record.task_id

    ctx = task_context_store.require(tid)
    # 注入 parsed dimensions
    TaskContextWriter(ctx).write(
        "input_spec",
        {
            "parsed": {
                "keywords": ["巧克力"],
                "dimensions": {
                    "industry": ["行业词"],
                    "competitor": ["竞品词"],
                    "brand": ["本品词"],
                },
            },
        },
        agent_id="test",
        merge=True,
    )

    # Mock cookies + 禁用组间 sleep / enrich 以加速测试
    monkeypatch.setattr(crawler_mod, "_resolve_cookies_str", lambda _u: "fake_cookie=1")
    monkeypatch.setattr(crawler_mod, "_INTER_GROUP_SLEEP", 0)
    monkeypatch.setattr(crawler_mod, "_ENRICH_ENABLED", False)

    call_keywords: List[Tuple[str, ...]] = []

    async def fake_collect(cookies, keywords, target_count, runtime_cfg):
        assert cookies == "fake_cookie=1"
        call_keywords.append(tuple(keywords))
        kw = keywords[0]
        if "行业" in kw:
            return [_FakeViralNote(note_id="i1", title=f"[行业] {kw}", note_type="图集")]
        if "竞品" in kw:
            return [
                _FakeViralNote(note_id="c1", title=f"[竞品] {kw}", note_type="视频"),
                _FakeViralNote(note_id="c2", title=f"[竞品-2] {kw}", note_type="图集"),
            ]
        if "本品" in kw:
            return [_FakeViralNote(note_id="b1", title=f"[本品] {kw}", note_type="图集")]
        return []

    monkeypatch.setattr(crawler_mod, "_collect_one_dimension", fake_collect)

    agent = crawler_mod.CrawlerAgent(model_gateway_instance=FakeGateway([]), event_bus=FakeBus())
    ac = AgentContext(task_id=tid, task_context=ctx)
    await agent.run(ac)

    out = ctx.get("crawler_output")
    assert out["source"] == "live"
    assert out["sample_count"] == 4
    assert out["dimension_status"]["industry"] == "ok"
    assert out["dimension_status"]["competitor"] == "ok"
    assert out["dimension_status"]["brand"] == "ok"
    assert len(out["notes_video"]) == 1  # 竞品那条
    assert len(out["notes_image"]) == 3
    # 3 组不同 keywords → 调用 3 次
    assert len(call_keywords) == 3


@pytest.mark.asyncio
async def test_crawler_agent_empty_dimensions_only_collects_industry(monkeypatch):
    """parsed.dimensions 全空时只用主关键词跑行业维度,避免污染竞品/本品分析。"""
    from backend.app.application.agents import crawler_agent as crawler_mod
    from backend.app.domain.task_context import task_context_store
    from backend.app.application.task_service import task_service

    res = task_service.create_task(
        owner_user_id="owner-dedupe",
        raw_input="完美日记",
        keywords=["完美日记", "花西子"],
        idempotency_key=None,
    )
    tid = res.record.task_id
    ctx = task_context_store.require(tid)
    # parsed.dimensions 全空 → 只有 industry fallback 到 base_keywords。
    TaskContextWriter(ctx).write(
        "input_spec",
        {
            "parsed": {
                "keywords": ["完美日记", "花西子"],
                "dimensions": {"industry": [], "competitor": [], "brand": []},
            },
        },
        agent_id="test",
        merge=True,
    )

    monkeypatch.setattr(crawler_mod, "_resolve_cookies_str", lambda _u: "fake_cookie=1")
    monkeypatch.setattr(crawler_mod, "_INTER_GROUP_SLEEP", 0)
    monkeypatch.setattr(crawler_mod, "_ENRICH_ENABLED", False)

    call_keywords: List[Tuple[str, ...]] = []

    async def fake_collect(cookies, keywords, target_count, runtime_cfg):
        call_keywords.append(tuple(keywords))
        return [
            _FakeViralNote(note_id=f"n{i}", title=f"爆款 {i}", note_type="图集")
            for i in range(3)
        ]

    monkeypatch.setattr(crawler_mod, "_collect_one_dimension", fake_collect)

    agent = crawler_mod.CrawlerAgent(model_gateway_instance=FakeGateway([]), event_bus=FakeBus())
    await agent.run(AgentContext(task_id=tid, task_context=ctx))

    out = ctx.get("crawler_output")
    assert len(call_keywords) == 1
    assert out["dimension_status"]["industry"] == "ok"
    assert out["dimension_status"]["competitor"] == "empty"
    assert out["dimension_status"]["brand"] == "empty"
    assert len(out["samples_by_dimension"]["industry"]) == 3
    assert out["samples_by_dimension"]["competitor"] == []
    assert out["samples_by_dimension"]["brand"] == []
    assert out["sample_count"] == 3
    assert len(out["note_summary"]) == 3
    for note in out["note_summary"]:
        hits = note.get("dimensions_hit") or []
        assert hits == ["industry"], f"dimensions_hit={hits}"


@pytest.mark.asyncio
async def test_crawler_agent_explicit_shared_keywords_collected_once(monkeypatch):
    """三维度显式 keywords 完全相同 → 只真实采集 1 次,结果共享给三维度。"""
    from backend.app.application.agents import crawler_agent as crawler_mod
    from backend.app.domain.task_context import task_context_store
    from backend.app.application.task_service import task_service

    res = task_service.create_task(
        owner_user_id="owner-dedupe-explicit",
        raw_input="完美日记",
        keywords=["完美日记", "花西子"],
        idempotency_key=None,
    )
    tid = res.record.task_id
    ctx = task_context_store.require(tid)
    TaskContextWriter(ctx).write(
        "input_spec",
        {
            "parsed": {
                "keywords": ["完美日记", "花西子"],
                "dimensions": {
                    "industry": ["完美日记", "花西子"],
                    "competitor": ["完美日记", "花西子"],
                    "brand": ["完美日记", "花西子"],
                },
            },
        },
        agent_id="test",
        merge=True,
    )

    monkeypatch.setattr(crawler_mod, "_resolve_cookies_str", lambda _u: "fake_cookie=1")
    monkeypatch.setattr(crawler_mod, "_INTER_GROUP_SLEEP", 0)
    monkeypatch.setattr(crawler_mod, "_ENRICH_ENABLED", False)

    call_keywords: List[Tuple[str, ...]] = []

    async def fake_collect(cookies, keywords, target_count, runtime_cfg):
        call_keywords.append(tuple(keywords))
        return [
            _FakeViralNote(note_id=f"n{i}", title=f"爆款 {i}", note_type="图集")
            for i in range(3)
        ]

    monkeypatch.setattr(crawler_mod, "_collect_one_dimension", fake_collect)

    agent = crawler_mod.CrawlerAgent(model_gateway_instance=FakeGateway([]), event_bus=FakeBus())
    await agent.run(AgentContext(task_id=tid, task_context=ctx))

    out = ctx.get("crawler_output")
    assert len(call_keywords) == 1, f"应只调一次 collect,实际调用 {len(call_keywords)} 次"
    assert out["dimension_status"]["industry"] == "ok"
    assert out["dimension_status"]["competitor"].startswith("shared:")
    assert out["dimension_status"]["brand"].startswith("shared:")
    for dim in ("industry", "competitor", "brand"):
        assert len(out["samples_by_dimension"][dim]) == 3
        assert out["samples_by_dimension"][dim][0]["dimension"] == dim
    assert out["sample_count"] == 3
    assert len(out["note_summary"]) == 3
    for note in out["note_summary"]:
        hits = note.get("dimensions_hit") or []
        assert set(hits) == {"industry", "competitor", "brand"}, f"dimensions_hit={hits}"


@pytest.mark.asyncio
async def test_crawler_agent_all_dims_fail_falls_back_to_stub(monkeypatch):
    from backend.app.application.agents import crawler_agent as crawler_mod
    from backend.app.domain.task_context import task_context_store
    from backend.app.application.task_service import task_service

    res = task_service.create_task(
        owner_user_id="owner-2",
        raw_input="x",
        keywords=["k"],
        idempotency_key=None,
    )
    tid = res.record.task_id
    ctx = task_context_store.require(tid)

    monkeypatch.setattr(crawler_mod, "_resolve_cookies_str", lambda _u: "fake_cookie=1")
    monkeypatch.setattr(crawler_mod, "_INTER_GROUP_SLEEP", 0)
    monkeypatch.setattr(crawler_mod, "_ENRICH_ENABLED", False)

    async def always_fail(cookies, keywords, target_count, runtime_cfg):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(crawler_mod, "_collect_one_dimension", always_fail)

    agent = crawler_mod.CrawlerAgent(model_gateway_instance=FakeGateway([]), event_bus=FakeBus())
    await agent.run(AgentContext(task_id=tid, task_context=ctx))

    out = ctx.get("crawler_output")
    assert out["source"] == "stub"
    assert out["sample_count"] > 0  # stub 填充了样本


@pytest.mark.asyncio
async def test_crawler_enriches_notes_with_precise_metrics(monkeypatch):
    """详情补充:采完后调详情接口,用精确互动数据覆盖 '100+' 模糊下界值。"""
    from backend.app.application.agents import crawler_agent as crawler_mod
    from backend.app.domain.task_context import task_context_store
    from backend.app.application.task_service import task_service

    res = task_service.create_task(
        owner_user_id="owner-enrich",
        raw_input="enrich test",
        keywords=["kw1"],
        idempotency_key=None,
    )
    tid = res.record.task_id
    ctx = task_context_store.require(tid)

    monkeypatch.setattr(crawler_mod, "_resolve_cookies_str", lambda _u: "fake_cookie=1")
    monkeypatch.setattr(crawler_mod, "_INTER_GROUP_SLEEP", 0)
    monkeypatch.setattr(crawler_mod, "_ENRICH_INTERVAL_SEC", 0)
    monkeypatch.setattr(crawler_mod, "_ENRICH_ENABLED", True)

    async def fake_collect(cookies, keywords, target_count, runtime_cfg):
        # 模拟搜索列表返回的"模糊下界值"
        return [
            _FakeViralNote(
                note_id="n1",
                note_url="https://www.xiaohongshu.com/explore/n1?xsec_token=abc",
                title="note 1",
                note_type="图集",
                liked_count=100,  # 原本是 "100+" 被 parse 成 100 下界
                comment_count=10,
                collected_count=50,
            ),
        ]

    monkeypatch.setattr(crawler_mod, "_collect_one_dimension", fake_collect)

    # mock 详情接口返回精确值
    def fake_detail(cookies_str, note_url):
        return {"likes": 3247, "comments": 128, "collects": 890, "shares": 42}

    monkeypatch.setattr(crawler_mod, "_fetch_note_detail_precise", fake_detail)

    agent = crawler_mod.CrawlerAgent(model_gateway_instance=FakeGateway([]), event_bus=FakeBus())
    await agent.run(AgentContext(task_id=tid, task_context=ctx))

    out = ctx.get("crawler_output")
    note = out["note_summary"][0]
    # 模糊下界 100 → 精确 3247
    assert note["likes"] == 3247
    assert note["comments"] == 128
    assert note["collects"] == 890
    assert note["metrics_precise"] is True
    # interaction_score 应基于精确值重算
    assert note["interaction_score"] == 3247 + 128 + 890 + 42
    # samples_by_dim 里的副本也应该被同步更新(propagate)
    for dim_notes in out["samples_by_dimension"].values():
        for dim_note in dim_notes:
            if dim_note.get("note_id") == "n1":
                assert dim_note["likes"] == 3247
                assert dim_note["metrics_precise"] is True


@pytest.mark.asyncio
async def test_crawler_reads_advanced_config_from_frontend(monkeypatch):
    """验证 CrawlerAgent 正确读取前端高级配置(采集数量/爆款比例/笔记类型/时间范围)。"""
    from backend.app.application.agents import crawler_agent as crawler_mod
    from backend.app.domain.task_context import task_context_store
    from backend.app.application.task_service import task_service

    res = task_service.create_task(
        owner_user_id="owner-advanced",
        raw_input="高级配置测试",
        keywords=["巧克力"],
        advanced_config={
            "sample_count": "200",
            "viral_ratio": "前30%",
            "note_type": "视频",
            "time_range": "一周内",
        },
        idempotency_key=None,
    )
    tid = res.record.task_id
    ctx = task_context_store.require(tid)

    monkeypatch.setattr(crawler_mod, "_resolve_cookies_str", lambda _u: "fake_cookie=1")
    monkeypatch.setattr(crawler_mod, "_INTER_GROUP_SLEEP", 0)
    monkeypatch.setattr(crawler_mod, "_ENRICH_ENABLED", False)

    captured_calls: List[Dict[str, Any]] = []

    async def fake_collect(cookies, keywords, target_count, runtime_cfg):
        captured_calls.append(
            {
                "target_count": target_count,
                "viral_ratio": runtime_cfg.get("viral_ratio"),
                "note_type": runtime_cfg.get("note_type"),
                "time_range": runtime_cfg.get("time_range"),
            }
        )
        return [_FakeViralNote(note_id="n1", title="测试", note_type="图集")]

    monkeypatch.setattr(crawler_mod, "_collect_one_dimension", fake_collect)

    agent = crawler_mod.CrawlerAgent(model_gateway_instance=FakeGateway([]), event_bus=FakeBus())
    await agent.run(AgentContext(task_id=tid, task_context=ctx))

    # 高级配置应已被正确解析并传给 collector
    assert captured_calls, "collector 没被调用"
    call = captured_calls[0]
    assert call["target_count"] == 200, f"target_count={call['target_count']}"
    assert call["viral_ratio"] == 0.3, f"viral_ratio={call['viral_ratio']}"
    assert call["note_type"] == 1, f"note_type={call['note_type']} (视频应为 1)"
    assert call["time_range"] == 2, f"time_range={call['time_range']} (一周内应为 2)"


@pytest.mark.asyncio
async def test_parse_advanced_config_malformed_input():
    """边界测试:高级配置字段缺失/不合法时回退 env 默认值。"""
    from backend.app.application.agents.crawler_agent import _parse_advanced_config

    # 空 dict → 全部默认
    out = _parse_advanced_config({})
    assert out["target_count"] > 0
    assert 0 < out["viral_ratio"] <= 1
    assert out["note_type"] == 0
    assert out["time_range"] == 0

    # None 或非 dict
    assert _parse_advanced_config(None)["target_count"] > 0  # type: ignore[arg-type]
    assert _parse_advanced_config("bad")["target_count"] > 0  # type: ignore[arg-type]

    # 无效 sample_count 忽略
    out = _parse_advanced_config({"sample_count": "not-a-number"})
    assert out["target_count"] > 0

    # 极端大值被上限保护
    out = _parse_advanced_config({"sample_count": "99999"})
    assert out["target_count"] <= 500

    # 未知 note_type 选项回落默认
    out = _parse_advanced_config({"note_type": "未来选项"})
    assert out["note_type"] == 0


# ---------- ImageAnalysisAgent (4.3pre.2 重写后接口) ----------

@pytest.mark.asyncio
async def test_image_analysis_writes_annotations_dict():
    """ImageAgent 改为写 annotations dict,key=note_id,8 字段标注。"""
    from backend.app.application.agents.image_analysis_agent import ImageAnalysisAgent

    valid_ann = (
        '{"cover_type": "大字标题", "cover_text_type": "干货/经验分享", '
        '"title_type": "干货/经验分享", "opening_type": "干货切入", '
        '"product_intro_type": "融入到干货/经验分享中", '
        '"product_placement_type": "融合自己使用方法/感受讲卖点", '
        '"pain_keywords": "暗沉", "content_direction": "干货分享", '
        '"product_brand": "雅诗兰黛"}'
    )
    gateway = FakeGateway([valid_ann, valid_ann, valid_ann])
    agent = ImageAnalysisAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context(
        {"raw_input": "x"},
        extra={
            "crawler_output": {
                "notes_image": [
                    {"note_id": "n1", "title": "t1", "likes": 1000, "cover_url": "https://x/1"},
                    {"note_id": "n2", "title": "t2", "likes": 500, "cover_url": "https://x/2"},
                    {"note_id": "n3", "title": "t3", "likes": 100, "cover_url": "https://x/3"},
                    {"note_id": "n4", "title": "no-cover", "likes": 50},  # 应被过滤
                ]
            }
        },
    )
    await agent.run(ac)

    mm = ctx.get("multimodal_output")
    annotations = mm.get("annotations") or {}
    # n4 没封面被过滤,只 3 条
    assert len(annotations) == 3
    assert "n1" in annotations
    # schema 字段都在
    assert annotations["n1"]["cover_type"] == "大字标题"
    assert annotations["n1"]["source_agent"] == "ImageAnalysisAgent"
    # 统计字段
    assert mm["image_stats"]["total"] == 3
    assert mm["image_stats"]["success"] == 3


@pytest.mark.asyncio
async def test_image_analysis_no_images_skips_gracefully():
    from backend.app.application.agents.image_analysis_agent import ImageAnalysisAgent

    gateway = FakeGateway([])
    agent = ImageAnalysisAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context(extra={"crawler_output": {"notes_image": []}})
    await agent.run(ac)

    mm = ctx.get("multimodal_output")
    assert mm["annotations"] == {}
    assert mm["image_stats"]["skipped_reason"] == "no_images"
    assert gateway.calls == []
