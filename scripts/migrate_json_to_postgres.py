"""One-shot JSON to PostgreSQL migration for phase 4.6.

Usage:
    python scripts/migrate_json_to_postgres.py --dry-run
    python scripts/migrate_json_to_postgres.py --apply --snapshot-dir datas/json_snapshot_20260427
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.infrastructure.db.engine import get_business_db_engine
from backend.app.infrastructure.db.schema import ensure_business_schema


def main() -> None:
    args = _parse_args()
    if not args.apply and not args.dry_run:
        raise SystemExit("必须指定 --dry-run 或 --apply")

    payload = collect_payload(REPO_ROOT)
    print_summary(payload)
    if args.dry_run:
        return

    if args.snapshot_dir:
        create_snapshot(Path(args.snapshot_dir))

    ensure_business_schema()
    engine = get_business_db_engine()
    with engine.begin() as conn:
        migrate_tasks(conn, payload["tasks"])
        migrate_identities(conn, payload["identities"])
        migrate_knowledge(conn, payload["domains"], payload["documents"])
        migrate_settings(conn, payload["system_settings"])
        migrate_focus_keywords(conn, payload["focus_keywords"])
        migrate_conversations(conn, payload["conversations"])
    print("迁移完成")


def collect_payload(root: Path) -> Dict[str, Any]:
    return {
        "tasks": load_json_files(root / "datas" / "tasks"),
        "identities": load_json_object(root / "datas" / "auth" / "xhs_identities.json"),
        "domains": load_json_object(root / "datas" / "knowledge" / "domains.json"),
        "documents": load_json_object(root / "datas" / "knowledge" / "documents.json"),
        "system_settings": load_json_object(root / "datas" / "config" / "system_settings.json"),
        "focus_keywords": load_focus_keywords(root / "datas" / "config" / "focus_keywords.json"),
        "conversations": load_json_files(root / "datas" / "conversations"),
    }


def load_json_files(directory: Path) -> List[Dict[str, Any]]:
    if not directory.exists():
        return []
    out = []
    for path in sorted(directory.glob("*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            print(f"跳过损坏 JSON {path}: {exc}")
    return out


def load_json_object(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        print(f"跳过损坏 JSON {path}: {exc}")
        return {}


def load_focus_keywords(path: Path) -> List[str]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"跳过损坏 JSON {path}: {exc}")
        return []
    if isinstance(data, list):
        return [str(x) for x in data if x]
    if isinstance(data, dict):
        return [str(x) for x in (data.get("items") or []) if x]
    return []


def print_summary(payload: Dict[str, Any]) -> None:
    print("迁移预览:")
    print(f"- tasks: {len(payload['tasks'])}")
    print(f"- identities: {len(payload['identities'])}")
    print(f"- knowledge_domains: {len(payload['domains'])}")
    print(f"- knowledge_documents: {len(payload['documents'])}")
    print(f"- system_settings: {bool(payload['system_settings'])}")
    print(f"- focus_keywords: {len(payload['focus_keywords'])}")
    print(f"- conversations: {len(payload['conversations'])}")


def create_snapshot(target: Path) -> None:
    target = target if target.is_absolute() else REPO_ROOT / target
    target.mkdir(parents=True, exist_ok=True)
    for rel in ["datas/tasks", "datas/auth", "datas/knowledge", "datas/config", "datas/conversations"]:
        src = REPO_ROOT / rel
        if src.exists():
            dst = target / rel.replace("/", "_")
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
    print(f"已创建 JSON 快照: {target}")


def migrate_tasks(conn, tasks: Iterable[Dict[str, Any]]) -> None:
    sql = text(
        """
        INSERT INTO tasks(
            task_id, owner_user_id, status, input_spec, idempotency_key, context_version,
            canvas_version, keywords, progress, collected_count, duration_seconds,
            created_at, updated_at, error_code, last_error
        ) VALUES (
            :task_id, :owner_user_id, :status, CAST(:input_spec AS jsonb), :idempotency_key,
            :context_version, :canvas_version, CAST(:keywords AS jsonb), :progress,
            :collected_count, :duration_seconds, CAST(:created_at AS timestamptz),
            CAST(:updated_at AS timestamptz), :error_code, :last_error
        )
        ON CONFLICT (task_id) DO UPDATE SET
            owner_user_id = EXCLUDED.owner_user_id,
            status = EXCLUDED.status,
            input_spec = EXCLUDED.input_spec,
            idempotency_key = EXCLUDED.idempotency_key,
            context_version = EXCLUDED.context_version,
            canvas_version = EXCLUDED.canvas_version,
            keywords = EXCLUDED.keywords,
            progress = EXCLUDED.progress,
            collected_count = EXCLUDED.collected_count,
            duration_seconds = EXCLUDED.duration_seconds,
            updated_at = EXCLUDED.updated_at,
            error_code = EXCLUDED.error_code,
            last_error = EXCLUDED.last_error
        """
    )
    for raw in tasks:
        conn.execute(
            sql,
            {
                "task_id": raw.get("task_id"),
                "owner_user_id": raw.get("owner_user_id") or "",
                "status": raw.get("status") or "pending",
                "input_spec": json.dumps(raw.get("input_spec") or {}, ensure_ascii=False),
                "idempotency_key": raw.get("idempotency_key"),
                "context_version": int(raw.get("context_version") or 0),
                "canvas_version": int(raw.get("canvas_version") or 0),
                "keywords": json.dumps(raw.get("keywords") or [], ensure_ascii=False),
                "progress": int(raw.get("progress") or 0),
                "collected_count": int(raw.get("collected_count") or 0),
                "duration_seconds": int(raw.get("duration_seconds") or 0),
                "created_at": raw.get("created_at"),
                "updated_at": raw.get("updated_at") or raw.get("created_at"),
                "error_code": raw.get("error_code"),
                "last_error": raw.get("last_error"),
            },
        )


def migrate_identities(conn, identities: Dict[str, Dict[str, Any]]) -> None:
    sql = text(
        """
        INSERT INTO identities(user_id, username, nickname, role, source, linked_user_ids, created_at, updated_at)
        VALUES (:user_id, :username, :nickname, :role, :source, CAST(:linked_user_ids AS jsonb),
                CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz))
        ON CONFLICT (user_id) DO UPDATE SET
            username = EXCLUDED.username,
            nickname = EXCLUDED.nickname,
            role = EXCLUDED.role,
            source = EXCLUDED.source,
            linked_user_ids = EXCLUDED.linked_user_ids,
            updated_at = EXCLUDED.updated_at
        """
    )
    for user_id, raw in identities.items():
        conn.execute(sql, _identity_params(user_id, raw))


def migrate_knowledge(conn, domains: Dict[str, Dict[str, Any]], documents: Dict[str, Dict[str, Any]]) -> None:
    domain_sql = text(
        """
        INSERT INTO knowledge_domains(domain_id, name, keywords, priority, enabled, rule_count, created_at, updated_at)
        VALUES (:domain_id, :name, CAST(:keywords AS jsonb), :priority, :enabled, :rule_count,
                CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz))
        ON CONFLICT (domain_id) DO UPDATE SET
            name = EXCLUDED.name,
            keywords = EXCLUDED.keywords,
            priority = EXCLUDED.priority,
            enabled = EXCLUDED.enabled,
            rule_count = EXCLUDED.rule_count,
            updated_at = EXCLUDED.updated_at
        """
    )
    for domain_id, raw in domains.items():
        conn.execute(
            domain_sql,
            {
                "domain_id": raw.get("domain_id") or domain_id,
                "name": raw.get("name") or "",
                "keywords": json.dumps(raw.get("keywords") or [], ensure_ascii=False),
                "priority": raw.get("priority") or "medium",
                "enabled": bool(raw.get("enabled", True)),
                "rule_count": int(raw.get("rule_count") or len(raw.get("keywords") or [])),
                "created_at": raw.get("created_at"),
                "updated_at": raw.get("updated_at") or raw.get("created_at"),
            },
        )
    doc_sql = text(
        """
        INSERT INTO knowledge_documents(
            doc_id, name, format, size_bytes, chunks, keywords, domains, stored_path,
            vector_status, vector_message, uploaded_by, uploaded_at, metadata
        ) VALUES (
            :doc_id, :name, :format, :size_bytes, :chunks, CAST(:keywords AS jsonb),
            CAST(:domains AS jsonb), :stored_path, :vector_status, :vector_message,
            :uploaded_by, CAST(:uploaded_at AS timestamptz), CAST(:metadata AS jsonb)
        )
        ON CONFLICT (doc_id) DO UPDATE SET
            name = EXCLUDED.name,
            format = EXCLUDED.format,
            size_bytes = EXCLUDED.size_bytes,
            chunks = EXCLUDED.chunks,
            keywords = EXCLUDED.keywords,
            domains = EXCLUDED.domains,
            stored_path = EXCLUDED.stored_path,
            vector_status = EXCLUDED.vector_status,
            vector_message = EXCLUDED.vector_message,
            uploaded_by = EXCLUDED.uploaded_by,
            uploaded_at = EXCLUDED.uploaded_at,
            metadata = EXCLUDED.metadata
        """
    )
    for doc_id, raw in documents.items():
        conn.execute(
            doc_sql,
            {
                "doc_id": raw.get("doc_id") or doc_id,
                "name": raw.get("name") or raw.get("filename") or "",
                "format": raw.get("format") or "txt",
                "size_bytes": int(raw.get("size_bytes") or 0),
                "chunks": int(raw.get("chunks") or 0),
                "keywords": json.dumps(raw.get("keywords") or [], ensure_ascii=False),
                "domains": json.dumps(raw.get("domains") or [], ensure_ascii=False),
                "stored_path": raw.get("stored_path"),
                "vector_status": raw.get("vector_status") or "pending",
                "vector_message": raw.get("vector_message") or "",
                "uploaded_by": raw.get("uploaded_by") or "",
                "uploaded_at": raw.get("uploaded_at"),
                "metadata": json.dumps(raw.get("metadata") or {}, ensure_ascii=False),
            },
        )


def migrate_settings(conn, settings: Dict[str, Any]) -> None:
    if not settings:
        return
    conn.execute(
        text(
            """
            INSERT INTO system_settings(id, settings, updated_at)
            VALUES (1, CAST(:settings AS jsonb), NOW())
            ON CONFLICT (id) DO UPDATE SET settings = EXCLUDED.settings, updated_at = NOW()
            """
        ),
        {"settings": json.dumps(settings, ensure_ascii=False)},
    )


def migrate_focus_keywords(conn, items: List[str]) -> None:
    conn.execute(
        text(
            """
            INSERT INTO focus_keywords(id, items, updated_at)
            VALUES (1, CAST(:items AS jsonb), NOW())
            ON CONFLICT (id) DO UPDATE SET items = EXCLUDED.items, updated_at = NOW()
            """
        ),
        {"items": json.dumps(items, ensure_ascii=False)},
    )


def migrate_conversations(conn, files: List[Dict[str, Any]]) -> None:
    conv_sql = text(
        """
        INSERT INTO conversations(conversation_id, owner_user_id, title, summary, active_task_id, metadata, created_at, updated_at)
        VALUES (:conversation_id, :owner_user_id, :title, :summary, :active_task_id, CAST(:metadata AS jsonb),
                CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz))
        ON CONFLICT (conversation_id) DO UPDATE SET
            owner_user_id = EXCLUDED.owner_user_id,
            title = EXCLUDED.title,
            summary = EXCLUDED.summary,
            active_task_id = EXCLUDED.active_task_id,
            metadata = EXCLUDED.metadata,
            updated_at = EXCLUDED.updated_at
        """
    )
    msg_sql = text(
        """
        INSERT INTO conversation_messages(
            message_id, conversation_id, role, content, intent, intent_confidence,
            clarification_needed, clarification_question, citations, task_handoff,
            linked_task_id, debug, created_at
        ) VALUES (
            :message_id, :conversation_id, :role, :content, :intent, :intent_confidence,
            :clarification_needed, :clarification_question, CAST(:citations AS jsonb),
            CAST(:task_handoff AS jsonb), :linked_task_id, CAST(:debug AS jsonb),
            CAST(:created_at AS timestamptz)
        )
        ON CONFLICT (message_id) DO UPDATE SET
            content = EXCLUDED.content,
            intent = EXCLUDED.intent,
            citations = EXCLUDED.citations,
            task_handoff = EXCLUDED.task_handoff,
            debug = EXCLUDED.debug
        """
    )
    for payload in files:
        conv = payload.get("conversation") or {}
        messages = payload.get("messages") or []
        conn.execute(
            conv_sql,
            {
                "conversation_id": conv.get("conversation_id"),
                "owner_user_id": conv.get("owner_user_id") or "",
                "title": conv.get("title") or "新对话",
                "summary": conv.get("summary") or "",
                "active_task_id": conv.get("active_task_id"),
                "metadata": json.dumps(conv.get("metadata") or {}, ensure_ascii=False),
                "created_at": conv.get("created_at"),
                "updated_at": conv.get("updated_at") or conv.get("created_at"),
            },
        )
        for msg in messages:
            conn.execute(
                msg_sql,
                {
                    "message_id": msg.get("message_id"),
                    "conversation_id": msg.get("conversation_id") or conv.get("conversation_id"),
                    "role": msg.get("role") or "assistant",
                    "content": msg.get("content") or "",
                    "intent": msg.get("intent") or "unknown",
                    "intent_confidence": float(msg.get("intent_confidence") or 0),
                    "clarification_needed": bool(msg.get("clarification_needed")),
                    "clarification_question": msg.get("clarification_question"),
                    "citations": json.dumps(msg.get("citations") or [], ensure_ascii=False),
                    "task_handoff": json.dumps(msg.get("task_handoff"), ensure_ascii=False)
                    if msg.get("task_handoff") is not None
                    else None,
                    "linked_task_id": msg.get("linked_task_id"),
                    "debug": json.dumps(msg.get("debug"), ensure_ascii=False) if msg.get("debug") is not None else None,
                    "created_at": msg.get("created_at"),
                },
            )


def _identity_params(user_id: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    created = raw.get("created_at")
    return {
        "user_id": raw.get("user_id") or user_id,
        "username": raw.get("username") or f"xhs_{user_id}",
        "nickname": raw.get("nickname") or "",
        "role": raw.get("role") or "user",
        "source": raw.get("source") or "xhs_selfinfo",
        "linked_user_ids": json.dumps(raw.get("linked_user_ids") or [], ensure_ascii=False),
        "created_at": created,
        "updated_at": raw.get("updated_at") or created,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--snapshot-dir", default="")
    return parser.parse_args()


if __name__ == "__main__":
    main()
