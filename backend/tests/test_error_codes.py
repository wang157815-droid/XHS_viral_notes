"""验收门槛：错误码体系覆盖 5 大类。"""

from __future__ import annotations

from backend.app.domain.error_codes import ErrorCategory, ErrorCode, build_error


def test_all_error_codes_belong_to_five_categories():
    prefixes = {cat.value for cat in ErrorCategory}
    for code in ErrorCode:
        prefix = code.value.split("_", 1)[0]
        assert prefix in prefixes, f"{code.value} 不属于 5 大类"


def test_build_error_produces_expected_shape():
    err = build_error(ErrorCode.AUTH_COOKIE_EXPIRED, "cookie expired", details={"hint": "re-login"})
    assert err["ok"] is False
    assert err["error"]["code"] == "AUTH_COOKIE_EXPIRED"
    assert err["error"]["details"]["hint"] == "re-login"
    assert "trace_id" in err["error"]
