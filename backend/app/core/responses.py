from typing import Any, Dict, Optional

from .tracing import get_trace_id


def ok(data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"ok": True, "data": data or {}}


def err(code: str, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "trace_id": get_trace_id(),
        },
    }

