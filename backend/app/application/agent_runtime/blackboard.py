"""RunBlackboard —— 自主 Agent 单次运行的工作记忆（产物存储）。

动机：phase3 v1 把每个工具的完整结果（截断后的原始 JSON）每轮重复回灌给模型，
多对象任务很快撑爆上下文，且模型无法"按句柄引用"之前已经取到的数据。

本模块提供一个进程内、与单次运行绑定的黑板：
- 采集类工具把完整结构化结果写入黑板（put_dataset），只把"紧凑摘要 + 句柄"回注模型；
- 模型后续用 query_dataset(handle, ...) 对黑板里的数据做筛选/排序/选列，不必重复采集；
- 计划（plan）也存在黑板上，供 plan-execute 与前端步骤可视化使用。

黑板存放在 ToolContext.extra["blackboard"]，生命周期 = 一次 run_stream。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# 单数据集预览默认条数（回注模型时只给少量预览，全量在黑板里 query）
PREVIEW_DEFAULT = 5
# query_dataset 单次最多返回行数（防止把全量数据又灌回模型）
QUERY_MAX_ROWS = 60


@dataclass
class Dataset:
    """黑板里的一份数据集（一次采集/批量操作的结果）。"""

    handle: str
    kind: str
    items: List[Dict[str, Any]]
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.items)

    def fields(self) -> List[str]:
        """从首行推断可用字段名。"""
        for item in self.items:
            if isinstance(item, dict):
                return list(item.keys())
        return []


def _coerce_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except (ValueError, AttributeError):
            return None
    return None


def _sort_key(value: Any) -> Tuple[int, float, str]:
    """统一可比较的排序键：数值优先按数值，否则按字符串；None 垫底。"""
    if value is None:
        return (0, 0.0, "")
    num = _coerce_number(value)
    if num is not None:
        return (2, num, "")
    return (1, 0.0, str(value))


def _match_condition(field_value: Any, condition: Any) -> bool:
    """单字段条件匹配。

    condition 可以是：
    - 标量：等值匹配（数值/字符串宽松比较）
    - dict：{op: operand}，op ∈ eq/ne/gte/gt/lte/lt/contains/in
    """
    if isinstance(condition, dict):
        for op, operand in condition.items():
            if not _apply_op(field_value, str(op).lower(), operand):
                return False
        return True
    # 标量等值：数值能转就按数值比，否则字符串
    fv_num = _coerce_number(field_value)
    op_num = _coerce_number(condition)
    if fv_num is not None and op_num is not None:
        return fv_num == op_num
    return str(field_value) == str(condition)


def _apply_op(field_value: Any, op: str, operand: Any) -> bool:
    if op in {"eq", "=="}:
        return _match_condition(field_value, operand)
    if op in {"ne", "!="}:
        return not _match_condition(field_value, operand)
    if op == "contains":
        return str(operand).lower() in str(field_value or "").lower()
    if op == "in":
        if isinstance(operand, (list, tuple, set)):
            return field_value in operand or str(field_value) in {str(o) for o in operand}
        return False
    # 数值比较
    fv = _coerce_number(field_value)
    ov = _coerce_number(operand)
    if fv is None or ov is None:
        return False
    if op in {"gte", ">="}:
        return fv >= ov
    if op in {"gt", ">"}:
        return fv > ov
    if op in {"lte", "<="}:
        return fv <= ov
    if op in {"lt", "<"}:
        return fv < ov
    return False


class RunBlackboard:
    """单次运行的工作记忆：数据集存储 + 查询 + 计划。"""

    def __init__(self) -> None:
        self._datasets: Dict[str, Dataset] = {}
        self._counters: Dict[str, int] = {}
        # 计划：[{"id": "s1", "title": "...", "status": "pending|done|skip"}]
        self.plan: List[Dict[str, Any]] = []

    # ── 数据集 ────────────────────────────────────────────────────────────
    def put_dataset(
        self, kind: str, items: List[Dict[str, Any]], meta: Optional[Dict[str, Any]] = None
    ) -> str:
        """写入一份数据集，返回句柄（如 ds:notes:1）。"""
        kind = kind or "data"
        n = self._counters.get(kind, 0) + 1
        self._counters[kind] = n
        handle = f"ds:{kind}:{n}"
        clean = [i for i in (items or []) if isinstance(i, dict)]
        self._datasets[handle] = Dataset(handle=handle, kind=kind, items=clean, meta=dict(meta or {}))
        return handle

    def get(self, handle: str) -> Optional[Dataset]:
        return self._datasets.get(handle)

    def list_datasets(self) -> List[Dataset]:
        return list(self._datasets.values())

    def all_items(self, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        """跨数据集聚合（可按 kind 过滤），按 note_id/id/comment_id 去重。"""
        merged: Dict[str, Dict[str, Any]] = {}
        ordered: List[Dict[str, Any]] = []
        for ds in self._datasets.values():
            if kind and ds.kind != kind:
                continue
            for item in ds.items:
                key = str(
                    item.get("note_id") or item.get("comment_id") or item.get("id") or id(item)
                )
                if key in merged:
                    continue
                merged[key] = item
                ordered.append(item)
        return ordered

    def query(
        self,
        handle: str,
        *,
        filters: Optional[Dict[str, Any]] = None,
        sort_by: Optional[str] = None,
        descending: bool = True,
        top_k: Optional[int] = None,
        fields: Optional[List[str]] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """对某个数据集做筛选/排序/选列。返回 (rows, matched_total)。"""
        ds = self._datasets.get(handle)
        if ds is None:
            raise KeyError(handle)
        rows = list(ds.items)
        if filters:
            rows = [r for r in rows if all(_match_condition(r.get(f), c) for f, c in filters.items())]
        matched_total = len(rows)
        if sort_by:
            rows = sorted(rows, key=lambda r: _sort_key(r.get(sort_by)), reverse=bool(descending))
        if top_k is not None and top_k > 0:
            rows = rows[: min(int(top_k), QUERY_MAX_ROWS)]
        else:
            rows = rows[:QUERY_MAX_ROWS]
        if fields:
            rows = [{k: r.get(k) for k in fields} for r in rows]
        return rows, matched_total

    def summary(self, handle: str) -> str:
        ds = self._datasets.get(handle)
        if ds is None:
            return f"句柄 {handle} 不存在"
        return f"{handle}（{ds.kind}，{ds.count} 条；字段：{', '.join(ds.fields())}）"

    # ── 计划 ──────────────────────────────────────────────────────────────
    def set_plan(self, steps: List[Any]) -> List[Dict[str, Any]]:
        plan: List[Dict[str, Any]] = []
        for idx, step in enumerate(steps or [], start=1):
            if isinstance(step, dict):
                sid = str(step.get("id") or f"s{idx}")
                title = str(step.get("title") or step.get("text") or "").strip()
                status = str(step.get("status") or "pending")
            else:
                sid = f"s{idx}"
                title = str(step or "").strip()
                status = "pending"
            if title:
                plan.append({"id": sid, "title": title, "status": status})
        self.plan = plan
        return self.plan

    def update_step(self, step_id: str, status: str) -> bool:
        for step in self.plan:
            if step.get("id") == step_id:
                step["status"] = status
                return True
        return False


def get_blackboard(ctx: Any) -> RunBlackboard:
    """从 ToolContext.extra 取（或惰性创建）本次运行的黑板。"""
    extra = getattr(ctx, "extra", None)
    if not isinstance(extra, dict):
        # ctx 无 extra（不应发生）时返回临时黑板，保证工具不崩
        return RunBlackboard()
    bb = extra.get("blackboard")
    if not isinstance(bb, RunBlackboard):
        bb = RunBlackboard()
        extra["blackboard"] = bb
    return bb
