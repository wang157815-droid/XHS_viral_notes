"""ToolRegistry —— 自主规划 Agent 的工具注册 / 校验 / 配额 / 审计中心。

设计目标：
- 注册：把"能力"封装成统一的 ToolSpec（含 OpenAI function schema + async handler）。
- 校验：执行前校验工具是否存在、必填参数是否齐全，所有模型输出视为不可信输入。
- 配额：标注工具成本（cheap/expensive）与是否需要二次确认，供 runtime 做预算与 gate。
- 审计：每次工具调用都落审计日志（loguru + 内存环形缓冲），便于排查与成本追踪。

注意：本层只负责"执行单个工具调用"，循环预算（max_iters / tool_budget / 超时）
由 AgentRuntime 统一控制。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional

from loguru import logger


ToolCost = Literal["cheap", "expensive"]


@dataclass
class ToolContext:
    """工具执行上下文：承载用户/会话身份与运行期附加信息。

    工具 handler 通过它拿到归属用户、会话 id、UI 高级配置等，
    避免把这些信息塞进模型可见的 arguments 里（模型不可信）。
    """

    owner_user_id: str = ""
    conversation_id: str = ""
    current_user: Dict[str, Any] = field(default_factory=dict)
    advanced_config: Dict[str, Any] = field(default_factory=dict)
    task_id: Optional[str] = None
    # 运行期可写的附加信息（如 runtime 注入的 emit 回调、已创建的 task_id 列表等）
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """工具执行结果。

    - content：回注给模型的观察文本（observation），必须是字符串。
    - data：结构化结果，供后续工具或上层使用（不一定回注模型）。
    - display：可选的用户可见摘要（如"已采集 23 条笔记"）。
    - error：失败时的错误信息。
    - units：本次执行消耗的"昂贵单位"（上游 API 请求条目数）。默认 1；
      批量工具（如 collect_notes / fetch_notes_details）按实际 API 调用次数返回，
      由 AgentRuntime 累计到 expensive_unit_budget。cheap 工具该值被忽略。
    """

    ok: bool
    content: str = ""
    data: Optional[Dict[str, Any]] = None
    display: Optional[str] = None
    error: Optional[str] = None
    units: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "content": self.content,
            "data": self.data,
            "display": self.display,
            "error": self.error,
            "units": self.units,
        }


ToolHandler = Callable[[Dict[str, Any], ToolContext], Awaitable[ToolResult]]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema（OpenAI function parameters）
    handler: ToolHandler
    cost: ToolCost = "cheap"
    requires_confirmation: bool = False
    category: str = "capability"  # capability | macro

    def to_openai_tool(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolAuditRecord:
    name: str
    ok: bool
    duration_ms: int
    owner_user_id: str
    conversation_id: str
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)


class ToolRegistry:
    """工具注册表：注册 + 校验 + 执行 + 审计。"""

    def __init__(self) -> None:
        self._specs: Dict[str, ToolSpec] = {}
        self._audit: List[ToolAuditRecord] = []

    # ── 注册 / 查询 ────────────────────────────────────────────────────────
    def register(self, spec: ToolSpec, *, override: bool = False) -> None:
        if not spec.name:
            raise ValueError("ToolSpec.name 不能为空")
        if spec.name in self._specs and not override:
            raise ValueError(f"工具 {spec.name!r} 已注册（如需覆盖请传 override=True）")
        self._specs[spec.name] = spec
        logger.debug("[ToolRegistry] 注册工具 {} (cost={}, confirm={})", spec.name, spec.cost, spec.requires_confirmation)

    def has(self, name: str) -> bool:
        return name in self._specs

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._specs.get(name)

    def names(self) -> List[str]:
        return list(self._specs.keys())

    def schemas(self, *, only: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """返回 OpenAI tools 数组。only 非空时仅返回子集（按注册顺序）。"""
        specs = self._specs.values()
        if only is not None:
            allow = set(only)
            specs = [s for s in specs if s.name in allow]
        return [s.to_openai_tool() for s in specs]

    def describe_for_prompt(self, *, only: Optional[List[str]] = None) -> str:
        allow = set(only) if only is not None else None
        lines: List[str] = []
        for spec in self._specs.values():
            if allow is not None and spec.name not in allow:
                continue
            tag = "宏工具" if spec.category == "macro" else "能力工具"
            flag = "（昂贵，需确认）" if spec.requires_confirmation else ""
            lines.append(f"- {spec.name}[{tag}]{flag}: {spec.description}")
        return "\n".join(lines)

    # ── 执行 ──────────────────────────────────────────────────────────────
    async def execute(
        self,
        name: str,
        arguments: Dict[str, Any],
        ctx: ToolContext,
    ) -> ToolResult:
        """执行单个工具调用，做存在性 + 必填参数校验，并捕获异常。"""
        spec = self._specs.get(name)
        if spec is None:
            return ToolResult(
                ok=False,
                content=f"工具 {name!r} 不存在。可用工具：{', '.join(self.names())}",
                error="TOOL_NOT_FOUND",
            )
        if not isinstance(arguments, dict):
            arguments = {}

        missing = self._validate_required(spec, arguments)
        if missing:
            msg = f"工具 {name!r} 缺少必填参数：{', '.join(missing)}"
            self._record(name, False, 0, ctx, error="MISSING_ARGS")
            return ToolResult(ok=False, content=msg, error="MISSING_ARGS")

        start = time.monotonic()
        try:
            result = await spec.handler(arguments, ctx)
            duration_ms = int((time.monotonic() - start) * 1000)
            if not isinstance(result, ToolResult):
                result = ToolResult(ok=True, content=str(result))
            self._record(name, result.ok, duration_ms, ctx, error=result.error)
            return result
        except Exception as exc:  # noqa: BLE001 —— 工具不可信，统一兜底
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.warning("[ToolRegistry] 工具 {} 执行异常: {}", name, exc)
            self._record(name, False, duration_ms, ctx, error=type(exc).__name__)
            return ToolResult(
                ok=False,
                content=f"工具 {name!r} 执行失败：{exc}",
                error=type(exc).__name__,
            )

    @staticmethod
    def _validate_required(spec: ToolSpec, arguments: Dict[str, Any]) -> List[str]:
        params = spec.parameters or {}
        required = params.get("required") or []
        missing: List[str] = []
        for key in required:
            val = arguments.get(key)
            if val is None or (isinstance(val, (str, list, dict)) and len(val) == 0):
                missing.append(str(key))
        return missing

    def _record(
        self,
        name: str,
        ok: bool,
        duration_ms: int,
        ctx: ToolContext,
        *,
        error: Optional[str] = None,
    ) -> None:
        rec = ToolAuditRecord(
            name=name,
            ok=ok,
            duration_ms=duration_ms,
            owner_user_id=ctx.owner_user_id,
            conversation_id=ctx.conversation_id,
            error=error,
        )
        self._audit.append(rec)
        if len(self._audit) > 500:
            self._audit = self._audit[-250:]
        logger.info(
            "[ToolRegistry] tool={} ok={} ms={} user={} conv={} err={}",
            name, ok, duration_ms, ctx.owner_user_id, ctx.conversation_id, error,
        )

    def audit_log(self, limit: int = 100) -> List[ToolAuditRecord]:
        return list(self._audit[-limit:])


# 全局单例（工具在 tools/ 包内注册到此）
tool_registry = ToolRegistry()
