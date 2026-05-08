"""4.3 段落 feedback API + regeneration 路由解析（确定性部分）。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.app.application.module_regeneration import (
    ParagraphScope,
    _write_semantic,
    _write_viral_matrix,
    parse_paragraph_id,
)
from backend.app.application.paragraph_feedback import list_feedback_hints_for_paragraphs
from backend.app.domain.canvas import CanvasModule, build_empty_canvas
from backend.app.domain.module_status import ModuleStatus
from backend.app.domain.task_context import TaskContext
from backend.app.application.task_service import task_service


@pytest.fixture
def isolated_repo(tmp_path: Path, monkeypatch):
    from backend.app.infrastructure import repository as repo_module
    from backend.app.application import task_service as task_service_module
    from backend.app.application.auth import task_access

    from backend.app.infrastructure.repository.task_repository import TaskRepository

    repo = TaskRepository(storage_dir=tmp_path / "tasks")
    monkeypatch.setattr(repo_module, "task_repository", repo)
    monkeypatch.setattr(task_service_module, "task_repository", repo)
    monkeypatch.setattr(task_access, "task_repository", repo)
    import backend.app.application.task_service as ts2

    monkeypatch.setattr(ts2, "task_repository", repo)
    return repo


def test_parse_paragraph_id_matrix_and_pain():
    r = parse_paragraph_id("M2")
    assert r.scope == ParagraphScope.MATRIX_MODEL
    assert r.model_index == 2
    r2 = parse_paragraph_id("M1-A_cover-C2")
    assert r2.scope == ParagraphScope.MATRIX_CATEGORY
    assert r2.element_code == "A_cover"
    assert r2.category_index == 2
    r3 = parse_paragraph_id("P3")
    assert r3.scope == ParagraphScope.PAIN_ITEM
    assert r3.list_index == 3
    r4 = parse_paragraph_id("S-core-1")
    assert r4.scope == ParagraphScope.SEO_CORE_ITEM
    r5 = parse_paragraph_id("mod-competitor-samples-video-note-abc")
    assert r5.scope == ParagraphScope.SAMPLE_NOTE
    assert r5.sample_module_id == "mod-competitor-samples-video"
    assert r5.note_id == "abc"


@pytest.fixture
def feedback_client(isolated_repo, monkeypatch, tmp_path):
    from backend.app.domain import task_context as tc_mod
    from backend.app.domain.task_context import TaskContextStore
    from backend.app.application import idempotency as idem_mod
    from backend.app.infrastructure.idempotency_store import IdempotencyStore

    store = TaskContextStore()
    store._storage_dir = tmp_path / "ctx"  # noqa: SLF001
    store._storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tc_mod, "task_context_store", store)

    from backend.app.core.security import get_current_user
    from backend.app.main import app

    def _fake_user():
        return {"user_id": "owner-1", "nickname": "u", "role": "user", "username": "u"}

    local_idem = IdempotencyStore()

    class AsyncIdempotencyStore:
        async def begin(self, key: str) -> bool:
            return local_idem.begin(key)

        async def complete(self, key: str, status_code: int, body: dict) -> None:
            local_idem.complete(key, status_code, body)

        async def abort(self, key: str) -> None:
            local_idem.abort(key)

        async def lookup(self, key: str):
            return local_idem.lookup(key)

    monkeypatch.setattr(idem_mod, "idempotency_store", AsyncIdempotencyStore())
    app.dependency_overrides[get_current_user] = _fake_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_paragraph_feedback_persists(feedback_client):
    res = task_service.create_task(owner_user_id="owner-1", raw_input="测", keywords=["测"])
    tid = res.record.task_id
    canvas = build_empty_canvas(task_id=tid, title="t")
    canvas.modules.append(
        CanvasModule(
            module_id="mod-draft-workbench",
            title="草稿",
            layer=2,
            status=ModuleStatus.READY,
            version=3,
            content={"fields": {"title": "x"}, "feedback_map": {}},
        )
    )
    task_service.set_canvas(tid, canvas)

    client = feedback_client
    r = client.post(
        f"/api/v1/tasks/{tid}/modules/mod-draft-workbench/paragraphs/p-draft-title/feedback",
        headers={
            "Authorization": "Bearer test",
            "Idempotency-Key": "fb-1",
            "If-Match": "3",
        },
        json={"action": "like"},
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]["module"]
    assert data["content"]["feedback_map"]["p-draft-title"]["action"] == "like"

    canvas2 = task_service.get_canvas(tid)
    mod = canvas2.find_module("mod-draft-workbench")
    assert mod and mod.content["feedback_map"]["p-draft-title"]["action"] == "like"


def test_reset_feedback_removes_entry(feedback_client):
    res = task_service.create_task(owner_user_id="owner-1", raw_input="测", keywords=["测"])
    tid = res.record.task_id
    canvas = build_empty_canvas(task_id=tid, title="t")
    canvas.modules.append(
        CanvasModule(
            module_id="mod-draft-workbench",
            title="草稿",
            layer=2,
            status=ModuleStatus.READY,
            version=1,
            content={
                "feedback_map": {
                    "p1": {"action": "like", "created_at": "x"},
                }
            },
        )
    )
    task_service.set_canvas(tid, canvas)
    client = feedback_client
    r = client.post(
        f"/api/v1/tasks/{tid}/modules/mod-draft-workbench/paragraphs/p1/feedback",
        headers={
            "Authorization": "Bearer test",
            "Idempotency-Key": "fb-2",
            "If-Match": "1",
        },
        json={"action": "reset"},
    )
    assert r.status_code == 200, r.text
    mod = task_service.get_canvas(tid).find_module("mod-draft-workbench")
    assert mod and "p1" not in (mod.content.get("feedback_map") or {})


def test_regenerate_schedules_background(feedback_client, monkeypatch):
    called = {}

    async def _capture(**kwargs):
        called.update(kwargs)

    monkeypatch.setattr("backend.app.api.routes.tasks.run_module_regeneration", _capture)

    coros: list = []

    def _fake_create_task(coro):
        coros.append(coro)
        return MagicMock()

    monkeypatch.setattr(asyncio, "create_task", _fake_create_task)

    res = task_service.create_task(owner_user_id="owner-1", raw_input="测", keywords=["测"])
    tid = res.record.task_id
    canvas = build_empty_canvas(task_id=tid, title="t")
    canvas.modules.append(
        CanvasModule(
            module_id="mod-viral-model-matrix",
            title="矩阵",
            layer=1,
            status=ModuleStatus.READY,
            version=5,
        )
    )
    task_service.set_canvas(tid, canvas)

    client = feedback_client
    r = client.post(
        f"/api/v1/tasks/{tid}/modules/mod-viral-model-matrix/regenerate",
        headers={
            "Authorization": "Bearer test",
            "Idempotency-Key": "rg-1",
            "If-Match": "5",
        },
        json={"instruction": "x", "cascade": False, "paragraph_id": "M1-A_cover-C1", "feedback_hint": "h"},
    )
    assert r.status_code == 200, r.text

    assert len(coros) == 1
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(coros[0])
    finally:
        loop.close()
    assert called.get("task_id") == tid
    assert called.get("paragraph_id") == "M1-A_cover-C1"
    assert called.get("feedback_hint") == "h"


def test_module_regeneration_write_helpers_accept_task_context(monkeypatch):
    ctx = TaskContext(task_id="task-regen-1")
    bumped: list[tuple[str, int]] = []

    def _fake_bump(task_id: str, version: int):
        bumped.append((task_id, version))

    monkeypatch.setattr(
        "backend.app.application.module_regeneration.task_repository.bump_context_version",
        _fake_bump,
    )

    _write_semantic(ctx, {"pain_points_top": [{"keyword": "干燥", "count": 3}]})
    assert ctx.get("semantic_output") == {"pain_points_top": [{"keyword": "干燥", "count": 3}]}
    assert bumped[-1] == ("task-regen-1", ctx.context_version)

    _write_viral_matrix(ctx, {"models": [{"model_id": "M1", "name": "知识科普"}]})
    assert ctx.get("viral_model_output") == {"models": [{"model_id": "M1", "name": "知识科普"}]}
    assert bumped[-1] == ("task-regen-1", ctx.context_version)


def test_feedback_hints_fall_back_to_action_when_user_did_not_type_hint():
    content = {
        "feedback_map": {
            "p-dislike": {"action": "dislike", "created_at": "x"},
            "p-like": {"action": "like", "created_at": "x"},
        }
    }
    hints = list_feedback_hints_for_paragraphs(content, ["p-dislike", "p-like"])
    assert "重写" in hints["p-dislike"]
    assert "方向正确" in hints["p-like"]
