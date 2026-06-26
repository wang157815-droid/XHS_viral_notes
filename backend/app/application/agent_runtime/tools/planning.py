"""计划工具：set_plan / update_plan（cheap）。

让自主 Agent 在多步任务开始时显式列出计划（存入 RunBlackboard），执行中更新步骤状态。
计划同时驱动前端"执行步骤条"可视化（runtime 在调用后发 agent_plan 事件）。
"""

from __future__ import annotations

from typing import Any, Dict, List

from loguru import logger

from ..blackboard import get_blackboard
from ..tool_registry import ToolContext, ToolRegistry, ToolResult, ToolSpec


_VALID_STATUS = {"pending", "in_progress", "done", "skip"}


async def _set_plan(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    steps = args.get("steps")
    if not isinstance(steps, list) or not steps:
        return ToolResult(ok=False, content="set_plan 需要 steps（步骤数组，每项为字符串或 {id,title}）", error="MISSING_ARGS")
    bb = get_blackboard(ctx)
    plan = bb.set_plan(steps)
    if not plan:
        return ToolResult(ok=False, content="set_plan 的 steps 解析后为空", error="EMPTY_PLAN")
    lines = "\n".join(f"{i}. {s['title']}" for i, s in enumerate(plan, start=1))
    logger.info("[tool.set_plan] 制定 {} 步计划", len(plan))
    return ToolResult(
        ok=True,
        content=f"已制定 {len(plan)} 步计划：\n{lines}\n请按计划逐步执行，每完成一步用 update_plan 标记。",
        data={"plan": plan},
        display=f"已制定 {len(plan)} 步计划",
    )


async def _update_plan(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    step_id = str(args.get("step_id") or "").strip()
    status = str(args.get("status") or "done").strip().lower()
    if not step_id:
        return ToolResult(ok=False, content="update_plan 需要 step_id", error="MISSING_ARGS")
    if status not in _VALID_STATUS:
        status = "done"
    bb = get_blackboard(ctx)
    ok = bb.update_step(step_id, status)
    if not ok:
        return ToolResult(
            ok=False,
            content=f"计划中没有步骤 {step_id}。当前步骤：{[s['id'] for s in bb.plan]}",
            error="STEP_NOT_FOUND",
        )
    done = sum(1 for s in bb.plan if s.get("status") == "done")
    return ToolResult(
        ok=True,
        content=f"步骤 {step_id} → {status}（已完成 {done}/{len(bb.plan)}）。",
        data={"plan": bb.plan},
        display=f"步骤 {step_id} → {status}",
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="set_plan",
            description="为多步分析任务制定执行计划（步骤清单）。复杂任务建议第一步先调用它，让执行有条理、过程对用户可见。",
            parameters={
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "有序步骤标题列表，如 ['采集Fazer近半年笔记','按互动量筛选','逐条拆解卖点与钩子','汇总结构化表格']",
                    },
                },
                "required": ["steps"],
            },
            handler=_set_plan,
            cost="cheap",
            category="capability",
        )
    )
    registry.register(
        ToolSpec(
            name="update_plan",
            description="更新某个计划步骤的状态（in_progress/done/skip），用于推进与汇报进度。",
            parameters={
                "type": "object",
                "properties": {
                    "step_id": {"type": "string", "description": "步骤 id（set_plan 返回的 s1/s2…）"},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "done", "skip"], "description": "新状态"},
                },
                "required": ["step_id"],
            },
            handler=_update_plan,
            cost="cheap",
            category="capability",
        )
    )
