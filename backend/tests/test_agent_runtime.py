"""phase3 自主规划 Agent 运行时单测。

覆盖：
- ToolRegistry：注册 / 校验（不存在、缺参、handler 异常）/ 审计
- AgentRuntime：function-calling loop 终止条件（model_final / tool_budget / 昂贵硬上限 / max_iters）
- 路由回归：agent_task→run_agent_task、爆文模型→反问、选择反问解析、Fazer→agent_task（classifier 解析）
"""

from __future__ import annotations

import pytest

from backend.app.application.agent_runtime.runtime import AgentRuntime, AgentRuntimeConfig
from backend.app.application.agent_runtime.tool_registry import (
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)


# ──────────────────────────────────────────────────────────────────────────
# 测试辅助
# ──────────────────────────────────────────────────────────────────────────
def _echo_spec(calls_log: list) -> ToolSpec:
    async def handler(args, ctx):
        calls_log.append(("echo", args))
        return ToolResult(ok=True, content=f"echo:{args.get('text')}", display="echoed")

    return ToolSpec(
        name="echo",
        description="echo text",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        handler=handler,
        cost="cheap",
    )


def _expensive_spec(calls_log: list) -> ToolSpec:
    async def handler(args, ctx):
        calls_log.append(("big", args))
        return ToolResult(ok=True, content="big-done", display="big")

    return ToolSpec(
        name="big",
        description="expensive op",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
        handler=handler,
        cost="expensive",
    )


class FakeGateway:
    """伪 gateway：chat_with_tools 按脚本逐轮返回；chat_stream 固定吐 final_text。"""

    def __init__(self, script, final_text="最终结论"):
        self.script = list(script)
        self.final_text = final_text
        self.with_tools_calls = 0
        self.stream_calls = 0

    async def chat_with_tools(self, agent_id, messages, *, tools, tool_choice="auto", task_id=None, overrides=None):
        self.with_tools_calls += 1
        if self.script:
            return self.script.pop(0)
        return {"content": "", "tool_calls": []}

    async def chat_stream(self, agent_id, messages, *, modality="text", task_id=None, overrides=None):
        self.stream_calls += 1
        mid = "m1"
        yield {"type": "message_start", "message_id": mid}
        yield {"type": "message_delta", "message_id": mid, "delta": self.final_text}
        yield {"type": "message_done", "message_id": mid, "content": self.final_text}


def _ctx() -> ToolContext:
    return ToolContext(owner_user_id="u1", conversation_id="c1")


async def _drain(runtime: AgentRuntime, **kwargs):
    events = []
    async for ev in runtime.run_stream(**kwargs):
        events.append(ev)
    return events


# ──────────────────────────────────────────────────────────────────────────
# ToolRegistry
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_registry_execute_success_and_audit():
    reg = ToolRegistry()
    log: list = []
    reg.register(_echo_spec(log))
    res = await reg.execute("echo", {"text": "hi"}, _ctx())
    assert res.ok is True
    assert res.content == "echo:hi"
    assert log == [("echo", {"text": "hi"})]
    assert reg.audit_log()[-1].name == "echo" and reg.audit_log()[-1].ok is True


@pytest.mark.asyncio
async def test_registry_tool_not_found():
    reg = ToolRegistry()
    res = await reg.execute("nope", {}, _ctx())
    assert res.ok is False
    assert res.error == "TOOL_NOT_FOUND"


@pytest.mark.asyncio
async def test_registry_missing_required_arg():
    reg = ToolRegistry()
    reg.register(_echo_spec([]))
    res = await reg.execute("echo", {}, _ctx())
    assert res.ok is False
    assert res.error == "MISSING_ARGS"


@pytest.mark.asyncio
async def test_registry_handler_exception_is_caught():
    reg = ToolRegistry()

    async def boom(args, ctx):
        raise RuntimeError("kaboom")

    reg.register(
        ToolSpec(name="boom", description="x", parameters={"type": "object", "properties": {}}, handler=boom)
    )
    res = await reg.execute("boom", {}, _ctx())
    assert res.ok is False
    assert res.error == "RuntimeError"


def test_registry_rejects_duplicate_without_override():
    reg = ToolRegistry()
    reg.register(_echo_spec([]))
    with pytest.raises(ValueError):
        reg.register(_echo_spec([]))
    # override=True 允许覆盖
    reg.register(_echo_spec([]), override=True)


# ──────────────────────────────────────────────────────────────────────────
# AgentRuntime loop 终止条件
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_runtime_model_final_no_tool_calls():
    reg = ToolRegistry()
    reg.register(_echo_spec([]))
    gw = FakeGateway(script=[{"content": "答案A", "tool_calls": []}])
    rt = AgentRuntime(registry=reg, gateway=gw, config=AgentRuntimeConfig())
    from backend.app.application.agent_runtime.runtime import AgentRunTrace

    trace = AgentRunTrace()
    events = await _drain(rt, goal="问题", ctx=_ctx(), trace=trace)
    done = [e for e in events if e.get("type") == "message_done"]
    assert done and done[-1]["content"] == "答案A"
    assert trace.stop_reason == "model_final"
    assert gw.stream_calls == 0  # natural finish 复用已生成内容，不再额外 chat_stream


@pytest.mark.asyncio
async def test_runtime_executes_tool_then_finalizes():
    reg = ToolRegistry()
    log: list = []
    reg.register(_echo_spec(log))
    gw = FakeGateway(
        script=[
            {"content": "", "tool_calls": [{"id": "1", "name": "echo", "arguments": {"text": "hi"}}]},
            {"content": "完成", "tool_calls": []},
        ]
    )
    rt = AgentRuntime(registry=reg, gateway=gw, config=AgentRuntimeConfig())
    from backend.app.application.agent_runtime.runtime import AgentRunTrace

    trace = AgentRunTrace()
    events = await _drain(rt, goal="问题", ctx=_ctx(), trace=trace)
    assert log == [("echo", {"text": "hi"})]
    tool_results = [e for e in events if e.get("status") == "agent_tool_result"]
    assert tool_results and tool_results[0]["tool"] == "echo"
    done = [e for e in events if e.get("type") == "message_done"]
    assert done and done[-1]["content"] == "完成"
    assert len(trace.tool_calls) == 1


@pytest.mark.asyncio
async def test_runtime_tool_budget_enforced():
    reg = ToolRegistry()
    log: list = []
    reg.register(_echo_spec(log))
    gw = FakeGateway(
        script=[
            {
                "content": "",
                "tool_calls": [
                    {"id": "1", "name": "echo", "arguments": {"text": "a"}},
                    {"id": "2", "name": "echo", "arguments": {"text": "b"}},
                ],
            }
        ]
    )
    rt = AgentRuntime(registry=reg, gateway=gw, config=AgentRuntimeConfig(tool_budget=1))
    from backend.app.application.agent_runtime.runtime import AgentRunTrace

    trace = AgentRunTrace()
    await _drain(rt, goal="问题", ctx=_ctx(), trace=trace)
    # 预算=1：只执行一次 echo，第二个被预算拦截
    assert len(log) == 1
    assert len(trace.tool_calls) == 1
    assert gw.stream_calls == 1  # 触发强制收尾 → chat_stream


@pytest.mark.asyncio
async def test_runtime_expensive_hard_cap():
    reg = ToolRegistry()
    log: list = []
    reg.register(_expensive_spec(log))
    gw = FakeGateway(
        script=[
            {
                "content": "",
                "tool_calls": [
                    {"id": "1", "name": "big", "arguments": {"q": "x"}},
                    {"id": "2", "name": "big", "arguments": {"q": "y"}},
                ],
            },
            {"content": "完成", "tool_calls": []},
        ]
    )
    rt = AgentRuntime(registry=reg, gateway=gw, config=AgentRuntimeConfig(max_expensive_calls=1, tool_budget=10))
    await _drain(rt, goal="问题", ctx=_ctx())
    # 昂贵硬上限=1：只执行一次 big，第二个被拦截但不终止
    assert len(log) == 1


def test_config_timeout_for_by_cost():
    cfg = AgentRuntimeConfig(
        per_tool_timeout=120, per_tool_timeout_cheap=60, per_tool_timeout_expensive=200
    )
    assert cfg.timeout_for("cheap") == 60
    assert cfg.timeout_for("expensive") == 200
    assert cfg.timeout_for(None) == 120
    assert cfg.timeout_for("unknown") == 120


@pytest.mark.asyncio
async def test_runtime_max_iters_forces_final():
    reg = ToolRegistry()
    log: list = []
    reg.register(_echo_spec(log))
    tool_resp = {"content": "", "tool_calls": [{"id": "1", "name": "echo", "arguments": {"text": "x"}}]}
    gw = FakeGateway(script=[tool_resp, tool_resp, tool_resp])
    rt = AgentRuntime(registry=reg, gateway=gw, config=AgentRuntimeConfig(max_iters=2, tool_budget=10))
    from backend.app.application.agent_runtime.runtime import AgentRunTrace

    trace = AgentRunTrace()
    events = await _drain(rt, goal="问题", ctx=_ctx(), trace=trace)
    assert trace.stop_reason == "max_iters"
    assert trace.iterations == 2
    done = [e for e in events if e.get("type") == "message_done"]
    assert done and done[-1]["content"] == "最终结论"


# ──────────────────────────────────────────────────────────────────────────
# 路由回归
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_search_notes_surfaces_upstream_api_error(monkeypatch):
    """第三方接口 err_no!=0 时，search_notes 返回显式 UPSTREAM_API_ERROR，而非伪装成 0 条。"""
    from backend.app.application.agent_runtime.tools import notes as notes_mod

    def fake_search(self, keyword, page=1, sort="general", note_type=0, **kw):
        return {"err_no": 1, "message": "cannot access local variable 'e'", "data": None}

    monkeypatch.setattr(notes_mod.RedbookApiClient, "search", fake_search)
    res = await notes_mod._search_notes({"keyword": "Fazer"}, _ctx())
    assert res.ok is False
    assert res.error == "UPSTREAM_API_ERROR"
    assert "不可用" in res.content


@pytest.mark.asyncio
async def test_search_notes_empty_results_still_ok(monkeypatch):
    """err_no==0 但确实无结果时，仍返回 ok=True（0 条），与接口错误区分开。"""
    from backend.app.application.agent_runtime.tools import notes as notes_mod

    def fake_search(self, keyword, page=1, sort="general", note_type=0, **kw):
        return {"err_no": 0, "message": "success", "data": {"data": {"items": []}}, "count": 5}

    monkeypatch.setattr(notes_mod.RedbookApiClient, "search", fake_search)
    res = await notes_mod._search_notes({"keyword": "冷门词"}, _ctx())
    assert res.ok is True
    assert "0 条" in res.display


def test_parse_analysis_mode():
    from backend.app.application.conversation_service import ConversationService

    f = ConversationService._parse_analysis_mode
    assert f("2") == "agent"
    assert f("我要 AI 自主分析") == "agent"
    assert f("1") == "workflow"
    assert f("走定制 workflow") == "workflow"
    assert f("随便") is None


@pytest.mark.asyncio
async def test_resolve_pending_choice_to_agent(tmp_path):
    from backend.app.application.conversation_service import ConversationService
    from backend.app.application.conversation.tool_agent import ANALYSIS_CHOICE_TOOL
    from backend.app.services.conversation_store import ConversationStore

    svc = ConversationService(store=ConversationStore(store_dir=str(tmp_path / "conv")))
    metadata = {
        "pending_tool_decision": {
            "tool_name": ANALYSIS_CHOICE_TOOL,
            "arguments": {
                "workflow_tool": "start_xhs_analysis",
                "workflow_arguments": {"keywords": ["Fazer"]},
                "agent_goal": "逐条拆解 Fazer 卖点",
            },
            "missing_fields": ["analysis_mode"],
        }
    }
    call = svc._resolve_pending_tool_call(metadata, "我选 AI 自主", None)
    assert call is not None and call.name == "run_agent_task"
    assert call.arguments["goal"] == "逐条拆解 Fazer 卖点"


@pytest.mark.asyncio
async def test_resolve_pending_choice_to_workflow(tmp_path):
    from backend.app.application.conversation_service import ConversationService
    from backend.app.application.conversation.tool_agent import ANALYSIS_CHOICE_TOOL
    from backend.app.services.conversation_store import ConversationStore

    svc = ConversationService(store=ConversationStore(store_dir=str(tmp_path / "conv")))
    metadata = {
        "pending_tool_decision": {
            "tool_name": ANALYSIS_CHOICE_TOOL,
            "arguments": {
                "workflow_tool": "start_xhs_analysis",
                "workflow_arguments": {"keywords": ["防晒"]},
                "agent_goal": "x",
            },
            "missing_fields": ["analysis_mode"],
        }
    }
    call = svc._resolve_pending_tool_call(metadata, "用 workflow", None)
    assert call is not None and call.name == "start_xhs_analysis"
    assert call.arguments["keywords"] == ["防晒"]
    assert call.arguments["confirm_new_task"] is True


@pytest.mark.asyncio
async def test_classifier_routes_fazer_to_agent_task(monkeypatch):
    """Fazer 定制化请求 → agent_task（误路由回归核心用例）。"""
    from backend.app.application.conversation import intent_classifier as ic_mod

    async def fake_chat(agent_id, messages, **kwargs):
        return {
            "content": (
                '{"intent":"agent_task","confidence":0.93,'
                '"slots":{"keywords":["Fazer"],"time_range":"半年内","min_interaction":"1000+"},'
                '"missing_fields":[],"clarification_question":null,"reason":"定制化逐条拆解"}'
            )
        }

    monkeypatch.setattr(ic_mod.model_gateway, "chat", fake_chat)
    result = await ic_mod.intent_classifier.classify(
        content="检索近半年互动量1000+的Fazer笔记，逐条拆解卖点与引流钩子"
    )
    assert result.intent == "agent_task"
    assert "Fazer" in result.extracted_keywords


@pytest.mark.asyncio
async def test_classifier_capability_question_to_general_qa(monkeypatch):
    """能力询问 → general_qa（不应误触发任务）。"""
    from backend.app.application.conversation import intent_classifier as ic_mod

    async def fake_chat(agent_id, messages, **kwargs):
        return {
            "content": '{"intent":"general_qa","confidence":0.92,"slots":{},"missing_fields":[],"reason":"功能询问"}'
        }

    monkeypatch.setattr(ic_mod.model_gateway, "chat", fake_chat)
    result = await ic_mod.intent_classifier.classify(content="你可以做爆文分析吗")
    assert result.intent == "general_qa"


# ──────────────────────────────────────────────────────────────────────────
# Phase 4：工作记忆黑板 / 单位制预算 / 批量去重 / 结构化产物 / plan-execute
# ──────────────────────────────────────────────────────────────────────────
def _expensive_units_spec(calls_log: list, units: int = 5) -> ToolSpec:
    async def handler(args, ctx):
        calls_log.append(("ubig", args))
        return ToolResult(ok=True, content="ubig-done", display="ubig", units=units)

    return ToolSpec(
        name="ubig",
        description="expensive op (multi-unit)",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
        handler=handler,
        cost="expensive",
    )


def test_blackboard_put_query_sort_filter_fields():
    from backend.app.application.agent_runtime.blackboard import RunBlackboard

    bb = RunBlackboard()
    handle = bb.put_dataset(
        "notes",
        [
            {"note_id": "a", "likes": 100, "title": "x"},
            {"note_id": "b", "likes": 900, "title": "y"},
            {"note_id": "c", "likes": 500, "title": "z"},
        ],
    )
    assert handle == "ds:notes:1"
    # 排序 + topK + 选列
    rows, matched = bb.query(handle, sort_by="likes", descending=True, top_k=2, fields=["note_id"])
    assert matched == 3
    assert rows == [{"note_id": "b"}, {"note_id": "c"}]
    # 数值阈值筛选
    rows2, matched2 = bb.query(handle, filters={"likes": {"gte": 500}})
    assert matched2 == 2
    assert {r["note_id"] for r in rows2} == {"b", "c"}
    # 不存在的句柄
    with pytest.raises(KeyError):
        bb.query("ds:notes:99")


def test_blackboard_plan_set_and_update():
    from backend.app.application.agent_runtime.blackboard import RunBlackboard

    bb = RunBlackboard()
    plan = bb.set_plan(["采集", "拆解", "汇总"])
    assert [s["id"] for s in plan] == ["s1", "s2", "s3"]
    assert all(s["status"] == "pending" for s in plan)
    assert bb.update_step("s2", "done") is True
    assert bb.update_step("nope", "done") is False
    assert next(s for s in bb.plan if s["id"] == "s2")["status"] == "done"


def test_blackboard_all_items_dedup_by_id():
    from backend.app.application.agent_runtime.blackboard import RunBlackboard

    bb = RunBlackboard()
    bb.put_dataset("notes", [{"note_id": "n1"}, {"note_id": "n2"}])
    bb.put_dataset("notes", [{"note_id": "n2"}, {"note_id": "n3"}])
    merged = bb.all_items(kind="notes")
    assert [i["note_id"] for i in merged] == ["n1", "n2", "n3"]


@pytest.mark.asyncio
async def test_runtime_expensive_unit_budget_enforced():
    """单位制预算：批量昂贵工具消耗多单位，超预算后续昂贵调用被拦截（区别于按次数硬上限）。"""
    reg = ToolRegistry()
    log: list = []
    reg.register(_expensive_units_spec(log, units=5))
    gw = FakeGateway(
        script=[
            {
                "content": "",
                "tool_calls": [
                    {"id": "1", "name": "ubig", "arguments": {"q": "x"}},
                    {"id": "2", "name": "ubig", "arguments": {"q": "y"}},
                ],
            },
            {"content": "完成", "tool_calls": []},
        ]
    )
    rt = AgentRuntime(
        registry=reg,
        gateway=gw,
        config=AgentRuntimeConfig(max_expensive_calls=10, tool_budget=10, expensive_unit_budget=4),
    )
    await _drain(rt, goal="问题", ctx=_ctx())
    # 第一次消耗 5 单位 > 预算 4 → 第二次被单位预算拦截
    assert len(log) == 1


@pytest.mark.asyncio
async def test_query_dataset_reads_blackboard_without_crawl():
    """query_dataset 是 cheap 工具：只读黑板、不触发任何采集。"""
    from backend.app.application.agent_runtime.blackboard import get_blackboard
    from backend.app.application.agent_runtime.tools import memory as memory_mod

    ctx = _ctx()
    bb = get_blackboard(ctx)
    handle = bb.put_dataset(
        "notes",
        [
            {"note_id": "a", "likes": 100},
            {"note_id": "b", "likes": 900},
            {"note_id": "c", "likes": 500},
        ],
    )
    res = await memory_mod._query_dataset(
        {"handle": handle, "filters": {"likes": {"gte": 300}}, "sort_by": "likes", "top_k": 5}, ctx
    )
    assert res.ok is True
    ids = [r["note_id"] for r in res.data["rows"]]
    assert ids == ["b", "c"]
    # 句柄不存在 → HANDLE_NOT_FOUND
    miss = await memory_mod._query_dataset({"handle": "ds:notes:999"}, ctx)
    assert miss.ok is False and miss.error == "HANDLE_NOT_FOUND"


@pytest.mark.asyncio
async def test_collect_notes_dedup_cache_plus_crawl(monkeypatch):
    """collect_notes：DB 缓存命中 + 实时翻页补采，按 note_id 去重，写入黑板。"""
    from backend.app.application.agent_runtime.blackboard import get_blackboard
    from backend.app.application.agent_runtime.tools import notes as notes_mod
    from backend.app.infrastructure.storage.comment_cache_store import comment_cache_store

    async def fake_load(keywords):
        return ([{"note_id": "n1", "title": "cached", "likes": 999}], 1)

    monkeypatch.setattr(comment_cache_store, "load_notes_for_keywords", fake_load)

    def fake_search(self, keyword, page=1, sort="general", note_type=0, **kw):
        if page == 1:
            return {"err_no": 0, "ids": ["n1", "n2", "n3"]}
        if page == 2:
            return {"err_no": 0, "ids": ["n3", "n4"]}
        return {"err_no": 0, "ids": []}

    def fake_parse(self, resp):
        return [{"note_id": nid} for nid in resp.get("ids", [])]

    def fake_to_dict(self, raw, keyword):
        nid = raw["note_id"]
        return {"note_id": nid, "title": f"t{nid}", "likes": int(nid[1:]) * 100, "source_keyword": keyword}

    monkeypatch.setattr(notes_mod.RedbookApiClient, "search", fake_search)
    monkeypatch.setattr(notes_mod.RedbookApiClient, "parse_search_notes", fake_parse)
    monkeypatch.setattr(notes_mod.RedbookApiClient, "note_to_dict", fake_to_dict)

    ctx = _ctx()
    res = await notes_mod._collect_notes({"keywords": ["Fazer"], "target_count": 4}, ctx)
    assert res.ok is True
    assert res.data["count"] == 4
    assert res.data["cached"] == 1
    assert res.data["crawled"] == 3
    # 黑板里去重后的 4 条
    ds = get_blackboard(ctx).get(res.data["handle"])
    ids = [i["note_id"] for i in ds.items]
    assert sorted(ids) == ["n1", "n2", "n3", "n4"]
    assert len(ids) == len(set(ids))


@pytest.mark.asyncio
async def test_present_artifact_table_from_handle_and_ctx():
    """present_artifact(table) 可用 source_handle 从黑板取行，并写入 ctx.extra['artifacts']。"""
    from backend.app.application.agent_runtime.blackboard import get_blackboard
    from backend.app.application.agent_runtime.tools import artifact as artifact_mod

    ctx = _ctx()
    bb = get_blackboard(ctx)
    handle = bb.put_dataset(
        "notes",
        [{"note_id": "n1", "title": "A", "likes": 100}, {"note_id": "n2", "title": "B", "likes": 900}],
    )
    res = await artifact_mod._present_artifact(
        {
            "kind": "table",
            "title": "卖点拆解",
            "columns": ["note_id", "title", "likes"],
            "source_handle": handle,
            "sort_by": "likes",
        },
        ctx,
    )
    assert res.ok is True
    arts = ctx.extra.get("artifacts")
    assert arts and arts[0]["type"] == "table"
    assert arts[0]["columns"] == ["note_id", "title", "likes"]
    # 按 likes 降序 → n2 在前
    assert arts[0]["rows"][0][0] == "n2"
    assert len(arts[0]["rows"]) == 2


@pytest.mark.asyncio
async def test_present_artifact_missing_columns_fails():
    from backend.app.application.agent_runtime.tools import artifact as artifact_mod

    res = await artifact_mod._present_artifact({"kind": "table", "title": "X"}, _ctx())
    assert res.ok is False and res.error == "MISSING_ARGS"


@pytest.mark.asyncio
async def test_runtime_emits_artifact_event():
    """runtime 把 present_artifact 的产物作为 artifact 事件外发，驱动前端内联渲染。"""
    from backend.app.application.agent_runtime.blackboard import get_blackboard
    from backend.app.application.agent_runtime.tools import artifact as artifact_mod

    reg = ToolRegistry()
    artifact_mod.register(reg)
    ctx = _ctx()
    handle = get_blackboard(ctx).put_dataset(
        "notes", [{"note_id": "n1", "title": "A", "likes": 100}, {"note_id": "n2", "title": "B", "likes": 50}]
    )
    gw = FakeGateway(
        script=[
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "1",
                        "name": "present_artifact",
                        "arguments": {
                            "kind": "table",
                            "title": "拆解",
                            "columns": ["note_id", "title"],
                            "source_handle": handle,
                        },
                    }
                ],
            },
            {"content": "完成", "tool_calls": []},
        ]
    )
    rt = AgentRuntime(registry=reg, gateway=gw, config=AgentRuntimeConfig())
    events = await _drain(rt, goal="拆解", ctx=ctx)
    arts = [e for e in events if e.get("type") == "artifact"]
    assert arts and arts[0]["artifact"]["type"] == "table"
    assert len(arts[0]["artifact"]["rows"]) == 2


@pytest.mark.asyncio
async def test_runtime_emits_plan_events():
    """runtime 把 set_plan/update_plan 的计划作为 agent_plan 状态事件外发。"""
    from backend.app.application.agent_runtime.tools import planning as planning_mod

    reg = ToolRegistry()
    planning_mod.register(reg)
    gw = FakeGateway(
        script=[
            {"content": "", "tool_calls": [{"id": "1", "name": "set_plan", "arguments": {"steps": ["采集", "拆解", "汇总"]}}]},
            {"content": "", "tool_calls": [{"id": "2", "name": "update_plan", "arguments": {"step_id": "s1", "status": "done"}}]},
            {"content": "完成", "tool_calls": []},
        ]
    )
    rt = AgentRuntime(registry=reg, gateway=gw, config=AgentRuntimeConfig())
    events = await _drain(rt, goal="多步任务", ctx=_ctx())
    plans = [e for e in events if e.get("status") == "agent_plan"]
    assert len(plans) >= 2
    assert plans[0]["plan"][0]["title"] == "采集"
    assert any(s["id"] == "s1" and s["status"] == "done" for s in plans[-1]["plan"])
