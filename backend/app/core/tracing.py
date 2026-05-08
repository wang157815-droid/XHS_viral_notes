"""Request/task trace helpers for lightweight observability."""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token
from typing import Optional

_trace_id_var: ContextVar[str] = ContextVar("redmuse_trace_id", default="")


def new_trace_id() -> str:
    return uuid.uuid4().hex


def get_trace_id(default: Optional[str] = None) -> str:
    return _trace_id_var.get() or default or new_trace_id()


def set_trace_id(trace_id: Optional[str]) -> Token[str]:
    value = (trace_id or "").strip() or new_trace_id()
    return _trace_id_var.set(value)


def reset_trace_id(token: Token[str]) -> None:
    _trace_id_var.reset(token)
