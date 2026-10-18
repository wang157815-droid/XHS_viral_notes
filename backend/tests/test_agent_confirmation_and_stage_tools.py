"""关键步骤确认闸 + 阶段工具单测。

覆盖：
- 确认闸：批准后执行 / 拒绝后不执行 / 非交互上下文不拦截 / 越权 resolve 被拒 / 相同副作用不重复执行
- 阶段工具：注册齐全、归属校验、前置阶段校验、非分阶段任务拒绝
"""

from __future__ import annotations

import asyncio

import pytest

from backend.app.application.agent_runtime.confirmation import confirmation_broker
from backend.app.application.agent_runtime.runtime import AgentRuntime, AgentRuntimeConfig
from backend.app.application.agent_runtime.tool_registry import (
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)


class FakeGateway:
    def __init__(self, script):
        self.script = list(script)

    async def chat_with_tools(self, agent_id, messages, *, tools, tool_choice="auto", task_id=None, overrides=None):
        return self.script.pop(0) if self.script else {"content": "完成", "tool_calls": []}

    async def chat_stream(self, agent_id, messages, *, modality="text", task_id=None, overrides=None):
        yield {"type": "message_start", "message_id": "m"}
        yield {"type": "message_done", "message_id": "m", "content": "完成"}


def _gated_registry(log: list) -> ToolRegistry:
    async def handler(args, ctx):
        log.append(args)
        return ToolResult(ok=True, content="crawled")

    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="crawl",
            description="抓取数据。会调用外部接口",
            parameters={"type": "object", "properties": {"q": {"type": "string"}}},
            handler=handler,
            cost="cheap",
            requires_confirmation=True,
        )
    )
    return reg


def _call(q="a"):
    return {"content": "", "tool_calls": [{"name": "crawl", "arguments": {"q": q}}]}


async def _run_with_decision(runtime, ctx, approved):
    events = []
    async for ev in runtime.run_stream(goal="g", ctx=ctx):
        events.append(ev)
        if ev.get("status") == "agent_confirm_required" and approved is not None:
            assert confirmation_broker.resolve(
                ev["confirmation_id"], approved=approved,
                owner_user_id=ctx.owner_user_id, conversation_id=ctx.conversation_id,
            )
    return events


def _interactive_ctx():
    return ToolContext(owner_user_id="u1", conversation_id="c1", extra={"interactive_confirm": True})


async def test_confirm_approved_executes():
    log: list = []
    rt = AgentRuntime(registry=_gated_registry(log), gateway=FakeGateway([_call()]))
    events = await _run_with_decision(rt, _interactive_ctx(), True)
    statuses = [e.get("status") for e in events]
    assert "agent_confirm_required" in statuses
    assert any(e.get("decision") == "approved" for e in events)
    assert log == [{"q": "a"}]


async def test_confirm_rejected_skips_tool():
    log: list = []
    rt = AgentRuntime(registry=_gated_registry(log), gateway=FakeGateway([_call()]))
    events = await _run_with_decision(rt, _interactive_ctx(), False)
    assert any(e.get("decision") == "rejected" for e in events)
    assert log == []


async def test_confirm_timeout_skips_tool():
    log: list = []
    rt = AgentRuntime(
        registry=_gated_registry(log),
        gateway=FakeGateway([_call()]),
        config=AgentRuntimeConfig(confirm_timeout=0.05),
    )
    events = await _run_with_decision(rt, _interactive_ctx(), None)
    assert any(e.get("decision") == "timeout" for e in events)
    assert log == []


async def test_non_interactive_context_not_gated():
    log: list = []
    rt = AgentRuntime(registry=_gated_registry(log), gateway=FakeGateway([_call()]))
    events = await _run_with_decision(rt, ToolContext(owner_user_id="u1", conversation_id="c1"), None)
    assert not any(e.get("status") == "agent_confirm_required" for e in events)
    assert log == [{"q": "a"}]


async def test_duplicate_side_effect_not_repeated():
    log: list = []
    rt = AgentRuntime(registry=_gated_registry(log), gateway=FakeGateway([_call(), _call()]))
    await _run_with_decision(rt, _interactive_ctx(), True)
    assert log == [{"q": "a"}]


async def test_resolve_rejects_other_owner():
    item = confirmation_broker.create(owner_user_id="u1", conversation_id="c1", tool="t", arguments={})
    try:
        assert not confirmation_broker.resolve(item.confirmation_id, approved=True, owner_user_id="u2", conversation_id="c1")
        assert not confirmation_broker.resolve(item.confirmation_id, approved=True, owner_user_id="u1", conversation_id="c2")
        assert confirmation_broker.resolve(item.confirmation_id, approved=True, owner_user_id="u1", conversation_id="c1")
        assert await asyncio.wait_for(item.future, 1) is True
    finally:
        confirmation_broker.discard(item.confirmation_id)


# ── 阶段工具 ─────────────────────────────────────────────────────────────────
@pytest.fixture
def stage_registry(tmp_path, monkeypatch):
    from backend.app.application.agent_runtime.tools import stages

    monkeypatch.setattr(stages, "_STORE_DIR", tmp_path / "stage_runs")
    reg = ToolRegistry()
    stages.register(reg)
    return reg


def test_stage_tools_registered(stage_registry):
    names = set(stage_registry.names())
    assert {
        "create_analysis_task", "parse_task_input", "crawl_task_notes", "analyze_task_images",
        "analyze_task_videos", "build_viral_model", "generate_task_insights",
        "retrieve_task_knowledge", "render_task_canvas", "get_task_progress",
    } <= names
    gated = {n for n in names if stage_registry.get(n).requires_confirmation}
    assert gated == {"create_analysis_task", "crawl_task_notes", "render_task_canvas"}


async def test_stage_ownership_and_prerequisites(stage_registry):
    owner = ToolContext(owner_user_id="u1", conversation_id="c1")
    created = await stage_registry.execute("create_analysis_task", {"keywords": ["咖啡"]}, owner)
    assert created.ok, created.content
    task_id = created.data["task_id"]

    other = ToolContext(owner_user_id="u2", conversation_id="c9")
    denied = await stage_registry.execute("parse_task_input", {"task_id": task_id}, other)
    assert not denied.ok and denied.error == "FORBIDDEN"

    blocked = await stage_registry.execute("crawl_task_notes", {"task_id": task_id}, owner)
    assert not blocked.ok and blocked.error == "PREREQUISITE_MISSING"

    progress = await stage_registry.execute("get_task_progress", {"task_id": task_id}, owner)
    assert progress.ok and "input_parser" in progress.content


async def test_stage_rejects_non_staged_task(stage_registry):
    from backend.app.application.task_service import task_service

    record = task_service.create_task(owner_user_id="u1", raw_input="x", keywords=["x"]).record
    res = await stage_registry.execute(
        "parse_task_input", {"task_id": record.task_id}, ToolContext(owner_user_id="u1")
    )
    assert not res.ok and res.error == "TASK_NOT_STAGED"
