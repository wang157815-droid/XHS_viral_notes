from backend.app.services.auth_orchestrator import AuthOrchestrator
from viral_agent.services.auth.qrcode_login_service import QRCodeLoginService


def test_auth_orchestrator_rejects_cross_object_user_id_and_nickname():
    payload = {
        "nickname": "真实昵称",
        "extra": {
            "session_user": {
                "user_id": "69de081e000000003301d2de",
            }
        },
    }

    user_id, nickname, source = AuthOrchestrator._extract_profile_fields(payload)

    assert user_id == ""
    assert nickname == ""
    assert source == "not_found"


def test_qrcode_cookie_probe_rejects_cross_object_user_id_and_nickname():
    payload = {
        "success": True,
        "data": {
            "nickname": "真实昵称",
            "extra": {
                "session_user": {
                    "user_id": "69de081e000000003301d2de",
                }
            },
        },
    }

    identity = QRCodeLoginService._extract_payload_identity(payload)

    assert identity is None


def test_identity_parser_accepts_legacy_selfinfo_result_id():
    payload = {
        "result": {
            "success": True,
            "data": "49364089447",
        },
        "basic_info": {
            "nickname": "wangsai",
            "red_id": "49364089447",
        },
        "extra": {
            "session_user": {
                "user_id": "69de081e000000003301d2de",
            }
        },
    }

    user_id, nickname, source = AuthOrchestrator._extract_profile_fields(payload)
    identity = QRCodeLoginService._extract_payload_identity({"data": payload})

    assert (user_id, nickname, source) == ("49364089447", "wangsai", "result.data")
    assert identity == {
        "user_id": "49364089447",
        "nickname": "wangsai",
        "source": "result.data",
    }


def test_identity_parser_rejects_selfinfo2_guest_top_level_user_id():
    payload = {
        "success": True,
        "data": {
            "user_id": "69de081e000000003301d2de",
            "nickname": "小红书69DF30A8",
            "red_id": "",
        },
    }

    user_id, nickname, source = AuthOrchestrator._extract_profile_fields(payload["data"])
    identity = QRCodeLoginService._extract_payload_identity(payload)

    assert (user_id, nickname, source) == ("", "", "not_found")
    assert identity is None


def test_sms_challenge_not_triggered_by_initial_qr_login_form():
    snapshot = {
        "text": "扫码登录 手机号登录 获取验证码",
        "inputs": [{"placeholder": "请输入手机号", "type": "tel", "maxlength": "11"}],
        "controls": ["获取验证码"],
    }

    assert QRCodeLoginService._sms_challenge_signal_from_snapshot(
        snapshot,
        qrcode_visible=True,
    ) is False


def test_sms_challenge_detected_even_when_qr_container_remains_visible():
    snapshot = {
        "text": "手机号验证 验证码已发送，请输入短信验证码",
        "inputs": [{"placeholder": "请输入验证码", "type": "text", "maxlength": "6"}],
        "controls": ["重新发送"],
    }

    assert QRCodeLoginService._sms_challenge_signal_from_snapshot(
        snapshot,
        qrcode_visible=True,
    ) is True


def test_sms_error_message_detects_invalid_or_expired_code():
    snapshot = {
        "text": "验证码不正确，请重新输入",
        "controls": ["确定"],
    }

    assert (
        QRCodeLoginService._sms_error_message_from_snapshot(snapshot)
        == "验证码错误或已过期，请重新输入"
    )


def test_sms_error_message_detects_too_many_requests():
    snapshot = {
        "text": "请求过于频繁，请稍后再试",
        "controls": ["重新发送"],
    }

    assert (
        QRCodeLoginService._sms_error_message_from_snapshot(snapshot)
        == "验证码请求过于频繁，请稍后再试"
    )


def test_scan_confirmation_detects_scanned_status():
    snapshot = {
        "text": "扫码成功，请在小红书 App 上确认登录",
        "controls": ["等待手机确认"],
    }

    assert QRCodeLoginService._scan_confirmation_signal_from_snapshot(
        snapshot,
        qrcode_visible=False,
    ) is True


def test_scan_confirmation_ignored_while_qrcode_still_visible():
    snapshot = {
        "text": "扫码登录 请使用小红书扫码",
        "controls": ["扫码登录"],
    }

    assert QRCodeLoginService._scan_confirmation_signal_from_snapshot(
        snapshot,
        qrcode_visible=True,
    ) is False
