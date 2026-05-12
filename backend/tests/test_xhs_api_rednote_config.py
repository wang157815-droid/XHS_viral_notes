from __future__ import annotations


def test_rednote_login_url_selects_rednote_api_and_origin(monkeypatch):
    monkeypatch.setenv("XHS_LOGIN_START_URL", "https://www.rednote.com/explore")
    monkeypatch.delenv("XHS_INTERNATIONAL", raising=False)
    monkeypatch.delenv("XHS_API_BASE_URL", raising=False)
    monkeypatch.delenv("XHS_WEB_ORIGIN", raising=False)

    import xhs_utils.xhs_util as util

    assert util.xhs_is_international() is True
    assert util.xhs_web_origin() == "https://www.rednote.com"
    assert util.xhs_api_base_url() == "https://webapi.rednote.com"


def test_selfinfo_uses_get_signature(monkeypatch):
    calls = []

    def fake_generate_request_params(cookies_str, api, data="", method="POST"):
        calls.append((cookies_str, api, data, method))
        return {}, {"a1": "a"}, ""

    class FakeResponse:
        def json(self):
            return {"success": True, "msg": "ok", "data": {}}

    import apis.xhs_pc_apis as api_mod

    monkeypatch.setattr(api_mod, "generate_request_params", fake_generate_request_params)
    monkeypatch.setattr(api_mod.requests, "get", lambda *args, **kwargs: FakeResponse())

    client = api_mod.XHS_Apis()
    client.get_user_self_info("a1=a; web_session=s")
    client.get_user_self_info2("a1=a; web_session=s")

    assert calls == [
        ("a1=a; web_session=s", "/api/sns/web/v1/user/selfinfo", "", "GET"),
        ("a1=a; web_session=s", "/api/sns/web/v2/user/me", "", "GET"),
    ]
