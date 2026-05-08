"""段落反馈：写入 CanvasModule.content.feedback_map（与 4.3 规划一致）。"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


VALID_FEEDBACK_ACTIONS = frozenset({"like", "dislike", "delete", "edit", "reset"})


def normalize_feedback_entry(
    *,
    action: str,
    edited_text: Optional[str] = None,
    feedback_hint: Optional[str] = None,
    actor: Optional[str] = None,
) -> Dict[str, Any]:
    a = (action or "").strip().lower()
    if a not in VALID_FEEDBACK_ACTIONS:
        raise ValueError(f"action 必须是其一: {sorted(VALID_FEEDBACK_ACTIONS)}")
    if a == "reset":
        return {"action": "reset", "created_at": _utc_iso()}
    entry: Dict[str, Any] = {
        "action": a,
        "created_at": _utc_iso(),
    }
    if edited_text is not None and str(edited_text).strip():
        entry["edited_text"] = str(edited_text).strip()
    if feedback_hint is not None and str(feedback_hint).strip():
        entry["feedback_hint"] = str(feedback_hint).strip()
    if actor:
        entry["actor"] = str(actor)
    return entry


def merge_feedback_into_content(
    content: Dict[str, Any],
    paragraph_id: str,
    entry: Dict[str, Any],
) -> Dict[str, Any]:
    """深拷贝 content，合并 feedback_map[paragraph_id]。"""
    out = copy.deepcopy(content) if content else {}
    fm = out.setdefault("feedback_map", {})
    if not isinstance(fm, dict):
        fm = {}
        out["feedback_map"] = fm
    pid = str(paragraph_id)
    if entry.get("action") == "reset":
        fm.pop(pid, None)
        return out
    fm[pid] = entry
    return out


def get_feedback_map(content: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not content or not isinstance(content.get("feedback_map"), dict):
        return {}
    return dict(content["feedback_map"])


def feedback_actions_for_ui(content: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """paragraph_id -> like|dislike|delete|edit（供前端恢复赞踩等）。"""
    fm = get_feedback_map(content)
    out: Dict[str, str] = {}
    for pid, row in fm.items():
        if isinstance(row, dict) and row.get("action"):
            a = str(row["action"])
            if a == "reset":
                continue
            out[str(pid)] = a
    return out


def list_feedback_hints_for_paragraphs(
    content: Optional[Dict[str, Any]], paragraph_ids: List[str]
) -> Dict[str, str]:
    """收集若干 paragraph_id 上用户填的 feedback_hint / edited_text（给重生 prompt）。"""
    fm = get_feedback_map(content)
    out: Dict[str, str] = {}
    pid_set = {str(p) for p in paragraph_ids}
    for pid in pid_set:
        row = fm.get(pid)
        if not isinstance(row, dict):
            continue
        hint = (row.get("feedback_hint") or "").strip()
        edited = (row.get("edited_text") or "").strip()
        action = str(row.get("action") or "").strip().lower()
        if hint:
            out[pid] = hint
        elif edited:
            out[pid] = f"用户编辑参考: {edited[:500]}"
        elif action == "dislike":
            out[pid] = "用户标记该段需要重写，请保留模块语义目标，但明显改写该条目的措辞、关键词或表达角度。"
        elif action == "like":
            out[pid] = "用户标记该段方向正确；若执行重写，请保留核心表达意图，仅做小幅优化。"
    return out
