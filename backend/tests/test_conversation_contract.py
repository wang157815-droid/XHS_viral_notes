from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def conversation_client(tmp_path, monkeypatch):
    from backend.app.api.routes import conversations as conversations_route
    from backend.app.core.security import get_current_user
    from backend.app.main import app
    from backend.app.services.conversation_store import ConversationStore

    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    monkeypatch.setattr(conversations_route, "get_conversation_store", lambda: store)

    def _fake_user():
        return {"user_id": "u1", "nickname": "u1", "role": "analyst", "username": "u1"}

    app.dependency_overrides[get_current_user] = _fake_user
    try:
        yield TestClient(app), store
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_create_get_and_append_messages(conversation_client):
    client, _ = conversation_client

    create = client.post("/api/v1/conversations", json={"title": "4.4 会话", "metadata": {"recent_keywords": ["测试"]}})
    assert create.status_code == 200
    conversation = create.json()["data"]
    assert conversation["title"] == "4.4 会话"
    assert conversation["metadata"]["recent_keywords"] == ["测试"]

    conversation_id = conversation["conversation_id"]
    send = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "你好", "client_message_id": "client_msg_1"},
    )
    assert send.status_code == 200
    data = send.json()["data"]
    assert data["user_message"]["message_id"] == "client_msg_1"
    assert data["assistant_message"]["role"] == "assistant"
    assert data["intent"]["intent"] == "general_qa"

    detail = client.get(f"/api/v1/conversations/{conversation_id}")
    assert detail.status_code == 200
    messages = detail.json()["data"]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]


def test_conversation_owner_isolation(conversation_client, monkeypatch):
    client, _ = conversation_client

    create = client.post("/api/v1/conversations", json={})
    conversation_id = create.json()["data"]["conversation_id"]

    from backend.app.core.security import get_current_user
    from backend.app.main import app

    def _fake_other_user():
        return {"user_id": "u2", "nickname": "u2", "role": "analyst", "username": "u2"}

    app.dependency_overrides[get_current_user] = _fake_other_user
    try:
        forbidden = client.get(f"/api/v1/conversations/{conversation_id}")
        assert forbidden.status_code == 403
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "admin",
            "nickname": "admin",
            "role": "admin",
            "username": "admin",
        }
        allowed = client.get(f"/api/v1/conversations/{conversation_id}")
        assert allowed.status_code == 200
    finally:
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "u1",
            "nickname": "u1",
            "role": "analyst",
            "username": "u1",
        }


def test_conversation_archive_restore_delete(conversation_client):
    client, _ = conversation_client

    create = client.post("/api/v1/conversations", json={"title": "可归档"})
    conversation_id = create.json()["data"]["conversation_id"]

    archived = client.post(f"/api/v1/conversations/{conversation_id}/archive", json={"reason": "done"})
    assert archived.status_code == 200
    assert archived.json()["data"]["metadata"]["archived"] is True

    listed = client.get("/api/v1/conversations")
    assert all(item["conversation_id"] != conversation_id for item in listed.json()["data"]["items"])

    restored = client.post(f"/api/v1/conversations/{conversation_id}/restore")
    assert restored.status_code == 200
    assert restored.json()["data"]["metadata"]["archived"] is False

    deleted = client.delete(f"/api/v1/conversations/{conversation_id}")
    assert deleted.status_code == 200
    assert deleted.json()["data"]["metadata"]["deleted"] is True


def test_conversation_stream_general_answer(monkeypatch, conversation_client):
    from backend.app.application import conversation_service as service_mod

    client, _ = conversation_client

    async def fake_chat_stream(*args, **kwargs):
        yield {"type": "message_start", "message_id": "msg_stream_1"}
        yield {"type": "message_delta", "message_id": "msg_stream_1", "delta": "你好"}
        yield {"type": "message_delta", "message_id": "msg_stream_1", "delta": "呀"}
        yield {"type": "message_done", "message_id": "msg_stream_1", "content": "你好呀"}

    async def fake_decide(**kwargs):
        from backend.app.application.conversation.tool_schema import ConversationToolCall, ConversationToolDecision

        return ConversationToolDecision(
            calls=[
                ConversationToolCall(
                    name="answer_general",
                    arguments={"question": kwargs["content"]},
                    confidence=0.9,
                )
            ]
        )

    monkeypatch.setattr(service_mod.model_gateway, "chat_stream", fake_chat_stream)
    conversation = client.post("/api/v1/conversations", json={"title": "stream"}).json()["data"]
    monkeypatch.setattr(service_mod.conversation_tool_agent, "decide", fake_decide)

    with client.stream(
        "POST",
        f"/api/v1/conversations/{conversation['conversation_id']}/messages/stream",
        json={"content": "你好", "client_message_id": "client_stream_1"},
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert '"type": "message_delta"' in body
    assert "你好呀" in body
