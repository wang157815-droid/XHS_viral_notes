from __future__ import annotations

from fastapi.testclient import TestClient


def test_history_timeline_combines_tasks_and_conversations(monkeypatch, tmp_path):
    from backend.app.api.routes import conversations as conversations_route
    from backend.app.api.routes import history as history_route
    from backend.app.core.security import get_current_user
    from backend.app.domain.task_status import TaskStatus
    from backend.app.infrastructure.repository.task_repository import TaskRecord
    from backend.app.main import app
    from backend.app.services.conversation_store import ConversationStore

    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    monkeypatch.setattr(conversations_route, "get_conversation_store", lambda: store)
    monkeypatch.setattr(history_route, "get_conversation_store", lambda: store)

    class FakeTaskService:
        def list_tasks(self, owner_user_id: str, *, include_all: bool, limit: int = 50):
            return [
                TaskRecord(
                    task_id="task_1",
                    owner_user_id=owner_user_id,
                    status=TaskStatus.COMPLETED,
                    keywords=["防晒"],
                    progress=100,
                )
            ]

    monkeypatch.setattr(history_route, "task_service", FakeTaskService())

    def _fake_user():
        return {"user_id": "u1", "nickname": "u1", "role": "user", "username": "u1"}

    app.dependency_overrides[get_current_user] = _fake_user
    try:
        client = TestClient(app)
        created = client.post("/api/v1/conversations", json={"title": "会话"}).json()["data"]
        response = client.get("/api/v1/history/timeline")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    items = response.json()["data"]["items"]
    types = {item["type"] for item in items}
    assert {"task", "conversation"} <= types
    assert any(item.get("conversation_id") == created["conversation_id"] for item in items)
