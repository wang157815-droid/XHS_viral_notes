"""PostgreSQL-backed observability metrics for phase 4.7."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from loguru import logger

from .engine import get_business_db_session

try:
    from sqlalchemy import text

    _SA_AVAILABLE = True
except ImportError:  # pragma: no cover
    text = None  # type: ignore[assignment]
    _SA_AVAILABLE = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False)


@dataclass
class TaskMetricRecord:
    metric_type: str
    metric_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    task_id: Optional[str] = None
    trace_id: str = ""
    agent: str = ""
    stage: str = ""
    duration_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    status: str = "ok"
    error_code: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now)


@dataclass
class EvalRunRecord:
    suite: str
    mode: str = "deterministic"
    status: str = "unknown"
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    commit_sha: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    details: Dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=_utc_now)
    finished_at: Optional[str] = None


class MetricsStore:
    """Small sync store used by runtime hooks and admin metrics APIs."""

    def record_task_metric(self, record: TaskMetricRecord, *, raise_on_error: bool = False) -> Optional[str]:
        if not _SA_AVAILABLE:
            if raise_on_error:
                raise RuntimeError("sqlalchemy 未安装")
            return None
        try:
            with get_business_db_session() as session:
                session.execute(
                    text(
                        """
                        INSERT INTO task_metrics (
                            metric_id, task_id, trace_id, agent, stage, metric_type,
                            duration_ms, tokens_in, tokens_out, status, error_code,
                            metadata, created_at
                        ) VALUES (
                            :metric_id, :task_id, :trace_id, :agent, :stage, :metric_type,
                            :duration_ms, :tokens_in, :tokens_out, :status, :error_code,
                            CAST(:metadata AS jsonb), CAST(:created_at AS timestamptz)
                        )
                        """
                    ),
                    {
                        "metric_id": record.metric_id,
                        "task_id": record.task_id,
                        "trace_id": record.trace_id,
                        "agent": record.agent,
                        "stage": record.stage,
                        "metric_type": record.metric_type,
                        "duration_ms": max(0, int(record.duration_ms or 0)),
                        "tokens_in": max(0, int(record.tokens_in or 0)),
                        "tokens_out": max(0, int(record.tokens_out or 0)),
                        "status": record.status or "ok",
                        "error_code": record.error_code,
                        "metadata": _json(record.metadata),
                        "created_at": record.created_at,
                    },
                )
            return record.metric_id
        except Exception as exc:  # noqa: BLE001 - metrics must not break user flows
            if raise_on_error:
                raise
            logger.debug(f"[MetricsStore] task metric 写入失败: {exc}")
            return None

    def record_eval_run(self, record: EvalRunRecord, *, raise_on_error: bool = False) -> Optional[str]:
        if not _SA_AVAILABLE:
            if raise_on_error:
                raise RuntimeError("sqlalchemy 未安装")
            return None
        try:
            with get_business_db_session() as session:
                session.execute(
                    text(
                        """
                        INSERT INTO eval_runs (
                            run_id, suite, mode, commit_sha, status, metrics,
                            details, started_at, finished_at
                        ) VALUES (
                            :run_id, :suite, :mode, :commit_sha, :status,
                            CAST(:metrics AS jsonb), CAST(:details AS jsonb),
                            CAST(:started_at AS timestamptz), CAST(:finished_at AS timestamptz)
                        )
                        ON CONFLICT (run_id) DO UPDATE SET
                            suite = EXCLUDED.suite,
                            mode = EXCLUDED.mode,
                            commit_sha = EXCLUDED.commit_sha,
                            status = EXCLUDED.status,
                            metrics = EXCLUDED.metrics,
                            details = EXCLUDED.details,
                            started_at = EXCLUDED.started_at,
                            finished_at = EXCLUDED.finished_at
                        """
                    ),
                    {
                        "run_id": record.run_id,
                        "suite": record.suite,
                        "mode": record.mode,
                        "commit_sha": record.commit_sha,
                        "status": record.status,
                        "metrics": _json(record.metrics),
                        "details": _json(record.details),
                        "started_at": record.started_at,
                        "finished_at": record.finished_at,
                    },
                )
            return record.run_id
        except Exception as exc:  # noqa: BLE001
            if raise_on_error:
                raise
            logger.debug(f"[MetricsStore] eval run 写入失败: {exc}")
            return None

    def get_summary(self, *, window_hours: int = 24) -> Dict[str, Any]:
        window_hours = max(1, min(int(window_hours or 24), 24 * 30))
        interval = f"{window_hours} hours"
        with get_business_db_session() as session:
            task_row = session.execute(
                text(
                    """
                    SELECT
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE status = 'completed') AS completed,
                        COUNT(*) FILTER (WHERE status = 'failed') AS failed,
                        COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled
                    FROM tasks
                    WHERE created_at >= NOW() - CAST(:window AS interval)
                    """
                ),
                {"window": interval},
            ).mappings().first()
            latency_row = session.execute(
                text(
                    """
                    SELECT
                        COALESCE(percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms), 0) AS p50,
                        COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms), 0) AS p95
                    FROM task_metrics
                    WHERE metric_type = 'task_run'
                      AND duration_ms > 0
                      AND created_at >= NOW() - CAST(:window AS interval)
                    """
                ),
                {"window": interval},
            ).mappings().first()
            model_row = session.execute(
                text(
                    """
                    SELECT
                        COUNT(*) AS calls,
                        COALESCE(SUM(tokens_in), 0) AS tokens_in,
                        COALESCE(SUM(tokens_out), 0) AS tokens_out,
                        COALESCE(AVG(duration_ms), 0) AS avg_duration_ms,
                        COUNT(*) FILTER (WHERE status <> 'ok') AS failed_calls
                    FROM task_metrics
                    WHERE metric_type = 'model_call'
                      AND created_at >= NOW() - CAST(:window AS interval)
                    """
                ),
                {"window": interval},
            ).mappings().first()
            errors = session.execute(
                text(
                    """
                    SELECT error_code, COUNT(*) AS count
                    FROM task_metrics
                    WHERE error_code IS NOT NULL
                      AND error_code <> ''
                      AND created_at >= NOW() - CAST(:window AS interval)
                    GROUP BY error_code
                    ORDER BY count DESC
                    LIMIT 10
                    """
                ),
                {"window": interval},
            ).mappings().all()
            eval_rows = session.execute(
                text(
                    """
                    SELECT suite, mode, status, started_at, finished_at, metrics
                    FROM eval_runs
                    ORDER BY started_at DESC
                    LIMIT 5
                    """
                )
            ).mappings().all()

        total = int((task_row or {}).get("total") or 0)
        failed = int((task_row or {}).get("failed") or 0)
        return {
            "window_hours": window_hours,
            "tasks": {
                "total": total,
                "completed": int((task_row or {}).get("completed") or 0),
                "failed": failed,
                "cancelled": int((task_row or {}).get("cancelled") or 0),
                "failure_rate": round(failed / total, 4) if total else 0,
                "p50_ms": int(float((latency_row or {}).get("p50") or 0)),
                "p95_ms": int(float((latency_row or {}).get("p95") or 0)),
            },
            "models": {
                "calls": int((model_row or {}).get("calls") or 0),
                "failed_calls": int((model_row or {}).get("failed_calls") or 0),
                "tokens_in": int((model_row or {}).get("tokens_in") or 0),
                "tokens_out": int((model_row or {}).get("tokens_out") or 0),
                "avg_duration_ms": int(float((model_row or {}).get("avg_duration_ms") or 0)),
            },
            "top_errors": [
                {"error_code": row["error_code"], "count": int(row["count"] or 0)}
                for row in errors
            ],
            "recent_eval_runs": [
                {
                    "suite": row["suite"],
                    "mode": row["mode"],
                    "status": row["status"],
                    "started_at": row["started_at"].isoformat() if hasattr(row["started_at"], "isoformat") else row["started_at"],
                    "finished_at": row["finished_at"].isoformat() if hasattr(row["finished_at"], "isoformat") else row["finished_at"],
                    "metrics": row["metrics"] or {},
                }
                for row in eval_rows
            ],
        }


metrics_store = MetricsStore()
