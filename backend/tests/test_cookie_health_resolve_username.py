"""CookieHealthService._resolve_username 须与爬虫 Cookie 路径解析一致。"""

from backend.app.services.cookie_health_service import CookieHealthService


def test_resolve_username_prefers_jwt_claim(monkeypatch):
    assert (
        CookieHealthService._resolve_username(
            {"user_id": "any", "username": "custom_login"}
        )
        == "custom_login"
    )


def test_resolve_username_falls_back_to_identity_store(monkeypatch):
    class FakeStore:
        def get(self, user_id: str):
            if user_id == "69deadbeef":
                return {"user_id": user_id, "username": "xhs_69deadbeef"}
            return None

    monkeypatch.setattr(
        "backend.app.services.identity_store.get_identity_store",
        lambda: FakeStore(),
        raising=False,
    )

    assert (
        CookieHealthService._resolve_username(
            {"user_id": "69deadbeef", "username": "", "nickname": "测"}
        )
        == "xhs_69deadbeef"
    )


def test_resolve_username_admin_when_missing(monkeypatch):
    class EmptyStore:
        def get(self, user_id: str):
            return None

    monkeypatch.setattr(
        "backend.app.services.identity_store.get_identity_store",
        lambda: EmptyStore(),
        raising=False,
    )

    assert CookieHealthService._resolve_username({"user_id": "nope", "username": ""}) == "admin"

