from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ...core.responses import ok
from ...core.security import create_access_token, get_current_user
from ...services.auth_orchestrator import auth_orchestrator

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginSessionRequest(BaseModel):
    scene: str = "dashboard_login"
    expected_user_id: str | None = None
    client_device_id: str | None = None


class SmsCodeRequest(BaseModel):
    sms_code: str


@router.post("/xhs-login/session")
async def create_xhs_login_session(payload: LoginSessionRequest):
    try:
        expected_user_id = (payload.expected_user_id or "").strip() or None
        if expected_user_id and expected_user_id.startswith("fallback_"):
            expected_user_id = None
        client_device_id = (payload.client_device_id or "").strip() or None
        data = await auth_orchestrator.create_qrcode_session(
            expected_user_id=expected_user_id,
            client_device_id=client_device_id,
        )
        return ok(data)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"创建扫码会话失败: {str(exc)}") from exc


@router.get("/xhs-login/session/{session_id}")
async def get_xhs_login_session(session_id: str, request: Request):
    previous = (request.headers.get("x-redmuse-previous-user-id") or "").strip()
    session = await auth_orchestrator.get_qrcode_session(
        session_id, link_with_previous=previous or None
    )
    if not session.get("exists"):
        raise HTTPException(status_code=404, detail="会话不存在或已过期")

    response_data = {k: v for k, v in session.items() if k != "exists"}
    user = session.get("user")
    if user:
        response_data["token"] = create_access_token(user)
    else:
        response_data["token"] = None

    return ok(response_data)


@router.get("/me")
async def me(current_user: dict = Depends(get_current_user)):
    return ok(
        {
            "user_id": current_user["user_id"],
            "nickname": current_user["nickname"],
            "role": current_user["role"],
        }
    )


@router.post("/logout")
async def logout():
    return ok({"logout": True})


@router.delete("/xhs-login/session/{session_id}")
async def cancel_xhs_login_session(session_id: str):
    success = await auth_orchestrator.cancel_qrcode_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="会话不存在")
    return ok({"session_id": session_id, "cancelled": True})


@router.post("/xhs-login/session/{session_id}/sms")
async def submit_xhs_login_sms(session_id: str, payload: SmsCodeRequest):
    sms_code = payload.sms_code.strip()
    if not sms_code.isdigit() or len(sms_code) < 4:
        raise HTTPException(status_code=400, detail="验证码格式不正确")

    try:
        success = await auth_orchestrator.submit_sms_code(session_id, sms_code)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not success:
        raise HTTPException(status_code=400, detail="提交验证码失败，请重试")

    return ok({"session_id": session_id, "sms_submitted": True})

