from __future__ import annotations

from fastapi.testclient import TestClient


API = "/api/v1"


def _override_user(app, get_current_user, *, role: str, user_id: str = "rbac-user"):
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": user_id,
        "nickname": user_id,
        "role": role,
        "username": user_id,
    }


def test_viewer_cannot_create_task(monkeypatch):
    from backend.app.core.security import get_current_user
    from backend.app.main import app

    app.dependency_overrides.clear()
    _override_user(app, get_current_user, role="viewer", user_id="viewer-task")
    try:
        client = TestClient(app)
        response = client.post(
            f"{API}/tasks",
            headers={"Idempotency-Key": "viewer-create-task"},
            json={"raw_input": "分析巧克力"},
        )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_viewer_cannot_write_conversations(tmp_path, monkeypatch):
    from backend.app.api.routes import conversations as conversations_route
    from backend.app.core.security import get_current_user
    from backend.app.main import app
    from backend.app.services.conversation_store import ConversationStore

    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    monkeypatch.setattr(conversations_route, "get_conversation_store", lambda: store)

    app.dependency_overrides.clear()
    _override_user(app, get_current_user, role="viewer", user_id="viewer-conv")
    try:
        client = TestClient(app)
        create = client.post(f"{API}/conversations", json={"title": "只读测试"})
        assert create.status_code == 403

        conversation = store.create(owner_user_id="viewer-conv", title="owned")
        send = client.post(
            f"{API}/conversations/{conversation.conversation_id}/messages",
            json={"content": "hello"},
        )
        assert send.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_viewer_cannot_manage_xhs_credentials():
    from backend.app.core.security import get_current_user
    from backend.app.main import app

    app.dependency_overrides.clear()
    _override_user(app, get_current_user, role="viewer", user_id="viewer-xhs")
    try:
        client = TestClient(app)
        read = client.get(f"{API}/xhs-auth/credential")
        assert read.status_code == 200

        force_status = client.get(f"{API}/xhs-auth/credential/status?force=true")
        assert force_status.status_code == 403

        bind = client.post(
            f"{API}/xhs-auth/bind/cookies",
            json={"cookies_str": "a1=valid; web_session=ABC"},
        )
        assert bind.status_code == 403

        unbind = client.delete(f"{API}/xhs-auth/credential")
        assert unbind.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_xhs_qr_login_session_requires_analyst_and_never_returns_system_jwt(monkeypatch):
    from backend.app.api.routes import auth as auth_route
    from backend.app.core.security import get_current_user
    from backend.app.main import app

    async def fake_create_qrcode_session(**kwargs):
        return {"session_id": "qr-1", "status": "waiting_scan"}

    async def fake_get_qrcode_session(session_id: str, link_with_previous=None):
        return {
            "exists": True,
            "session_id": session_id,
            "status": "success",
            "user": {
                "user_id": "xhs-user",
                "nickname": "xhs",
                "role": "admin",
                "username": "xhs-user",
            },
        }

    monkeypatch.setattr(auth_route.auth_orchestrator, "create_qrcode_session", fake_create_qrcode_session)
    monkeypatch.setattr(auth_route.auth_orchestrator, "get_qrcode_session", fake_get_qrcode_session)

    app.dependency_overrides.clear()
    _override_user(app, get_current_user, role="viewer", user_id="viewer-qr")
    try:
        client = TestClient(app)
        denied = client.post(f"{API}/auth/xhs-login/session", json={"scene": "settings_rebind"})
        assert denied.status_code == 403

        _override_user(app, get_current_user, role="analyst", user_id="analyst-qr")
        created = client.post(f"{API}/auth/xhs-login/session", json={"scene": "settings_rebind"})
        assert created.status_code == 200

        status = client.get(f"{API}/auth/xhs-login/session/qr-1")
        assert status.status_code == 200
        assert status.json()["data"]["token"] is None
    finally:
        app.dependency_overrides.pop(get_current_user, None)
