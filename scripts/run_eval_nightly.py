"""Run phase 4.7 eval suites and persist a summary into eval_runs."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Run RedMuse eval suite and record eval_runs.")
    parser.add_argument("--suite", default="deterministic", choices=["deterministic"])
    parser.add_argument("--skip-db", action="store_true", help="Do not persist eval_runs.")
    args = parser.parse_args()

    started_at = utc_now()
    command = [
        sys.executable,
        "-m",
        "pytest",
        "backend/tests/eval",
        "-m",
        args.suite,
        "-q",
    ]
    result = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True)
    finished_at = utc_now()

    status = "passed" if result.returncode == 0 else "failed"
    details = {
        "command": command,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
    }
    metrics = {
        "exit_code": result.returncode,
        "passed": result.returncode == 0,
    }

    if not args.skip_db:
        try:
            os.environ.setdefault("REDMUSE_SKIP_STARTUP_CHECKS", "true")
            from backend.app.infrastructure.db import EvalRunRecord, metrics_store

            metrics_store.record_eval_run(
                EvalRunRecord(
                    suite=args.suite,
                    mode="nightly",
                    commit_sha=current_commit(),
                    status=status,
                    metrics=metrics,
                    details=details,
                    started_at=started_at,
                    finished_at=finished_at,
                )
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[eval] eval_runs 写入失败: {exc}", file=sys.stderr)

    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
