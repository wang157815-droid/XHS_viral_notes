from __future__ import annotations

import json


def test_business_schema_contains_phase46_tables(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_VECTOR_DIM", "8")
    from backend.app.infrastructure.db.schema import business_schema_sql

    sql = business_schema_sql()
    for name in [
        "tasks",
        "identities",
        "knowledge_domains",
        "knowledge_documents",
        "knowledge_chunks",
        "system_settings",
        "focus_keywords",
        "conversations",
        "conversation_messages",
        "task_metrics",
        "eval_runs",
    ]:
        assert f"CREATE TABLE IF NOT EXISTS {name}" in sql
    assert "embedding vector(8)" in sql


def test_observability_schema_contains_phase47_tables():
    from backend.app.infrastructure.db.schema import observability_schema_sql

    sql = observability_schema_sql()
    assert "CREATE TABLE IF NOT EXISTS task_metrics" in sql
    assert "CREATE TABLE IF NOT EXISTS eval_runs" in sql
    assert "task_metrics_task_created_idx" in sql
    assert "eval_runs_suite_started_idx" in sql


def test_migration_collects_json_payload(tmp_path, monkeypatch):
    from scripts.migrate_json_to_postgres import collect_payload

    root = tmp_path
    (root / "datas" / "tasks").mkdir(parents=True)
    (root / "datas" / "auth").mkdir(parents=True)
    (root / "datas" / "knowledge").mkdir(parents=True)
    (root / "datas" / "config").mkdir(parents=True)
    (root / "datas" / "conversations").mkdir(parents=True)

    (root / "datas" / "tasks" / "t1.json").write_text(
        json.dumps({"task_id": "t1", "owner_user_id": "u1"}),
        encoding="utf-8",
    )
    (root / "datas" / "auth" / "xhs_identities.json").write_text(
        json.dumps({"u1": {"user_id": "u1"}}),
        encoding="utf-8",
    )
    (root / "datas" / "config" / "focus_keywords.json").write_text(
        json.dumps({"items": ["巧克力"]}, ensure_ascii=False),
        encoding="utf-8",
    )

    payload = collect_payload(root)

    assert payload["tasks"][0]["task_id"] == "t1"
    assert "u1" in payload["identities"]
    assert payload["focus_keywords"] == ["巧克力"]
