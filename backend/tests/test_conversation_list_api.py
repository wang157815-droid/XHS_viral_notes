from __future__ import annotations

from fastapi.testclient import TestClient


def _override_user(app, get_current_user, user_id: str, role: str = "user") -> None:
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": user_id,
        "nickname": user_id,
        "role": role,
        "username": user_id,
    }


def _message(conversation_id: str, idx: int, role: str = "user"):
    from backend.app.domain.conversation import ChatMessage

    return ChatMessage(
        message_id=f"msg_{idx}",
        conversation_id=conversation_id,
        role=role,
        content=f"第 {idx} 条消息",
        intent="knowledge_qa" if role == "assistant" else "unknown",
    )


def test_list_conversations_owner_keyword_and_admin(tmp_path, monkeypatch):
    from backend.app.api.routes import conversations as conversations_route
    from backend.app.core.security import get_current_user
    from backend.app.main import app
    from backend.app.services.conversation_store import ConversationStore

    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    monkeypatch.setattr(conversations_route, "get_conversation_store", lambda: store)

    _override_user(app, get_current_user, "u1")
    try:
        client = TestClient(app)
        c1 = client.post(
            "/api/v1/conversations",
            json={"title": "防脱知识问答", "metadata": {"recent_keywords": ["防脱"]}},
        ).json()["data"]
        store.append_message(c1["conversation_id"], _message(c1["conversation_id"], 1))
        store.append_message(c1["conversation_id"], _message(c1["conversation_id"], 2, role="assistant"))

        store.create(owner_user_id="u2", title="竞品分析会话", metadata={"recent_keywords": ["香水"]})

        listed = client.get("/api/v1/conversations?keyword=防脱")
        assert listed.status_code == 200
        data = listed.json()["data"]
        assert data["include_all_effective"] is False
        assert [item["conversation_id"] for item in data["items"]] == [c1["conversation_id"]]
        assert data["items"][0]["last_intent"] == "knowledge_qa"
        assert data["items"][0]["message_count"] == 2

        _override_user(app, get_current_user, "admin", role="admin")
        admin_listed = client.get("/api/v1/conversations?include_all=true")
        assert admin_listed.status_code == 200
        admin_data = admin_listed.json()["data"]
        assert admin_data["include_all_effective"] is True
        assert len(admin_data["items"]) == 2
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_list_messages_has_more(tmp_path, monkeypatch):
    from backend.app.api.routes import conversations as conversations_route
    from backend.app.core.security import get_current_user
    from backend.app.main import app
    from backend.app.services.conversation_store import ConversationStore

    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    monkeypatch.setattr(conversations_route, "get_conversation_store", lambda: store)
    _override_user(app, get_current_user, "u1")
    try:
        client = TestClient(app)
        conversation = client.post("/api/v1/conversations", json={"title": "分页"}).json()["data"]
        cid = conversation["conversation_id"]
        for idx in range(5):
            store.append_message(cid, _message(cid, idx))

        first_page = client.get(f"/api/v1/conversations/{cid}/messages?limit=2")
        assert first_page.status_code == 200
        payload = first_page.json()["data"]
        assert [item["message_id"] for item in payload["items"]] == ["msg_3", "msg_4"]
        assert payload["has_more"] is True

        second_page = client.get(f"/api/v1/conversations/{cid}/messages?limit=2&before=msg_3")
        assert second_page.status_code == 200
        payload = second_page.json()["data"]
        assert [item["message_id"] for item in payload["items"]] == ["msg_1", "msg_2"]
        assert payload["has_more"] is True
    finally:
        app.dependency_overrides.pop(get_current_user, None)
