from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ...core.responses import ok
from ...core.security import create_access_token, get_current_user, require_admin_user
from ...services.auth_orchestrator import auth_orchestrator
from ...services.redmuse_auth import (
    UserAlreadyExistsError,
    get_user_store,
)

router = APIRouter(prefix="/auth", tags=["auth"])


# ============================================================
# Phase 0: RedMuse 用户名/密码登录
# ============================================================


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)
    nickname: str | None = Field(default=None, max_length=64)
    role: str = Field(default="user")
    xhs_credential_path: str | None = Field(default=None, max_length=256)


class UpdatePasswordRequest(BaseModel):
    password: str = Field(..., min_length=6, max_length=128)


class UpdateRoleRequest(BaseModel):
    role: str = Field(..., pattern="^(admin|user)$")


@router.post("/login")
async def login(payload: LoginRequest):
    """用户名 + 密码登录，签发 RedMuse JWT。"""
    store = get_user_store()
    user = store.authenticate(payload.username, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="账号已被停用，请联系管理员")

    token = create_access_token(
        {
            "user_id": user.user_id,
            "username": user.username,
            "nickname": user.nickname,
            "role": user.role,
        },
        token_type="redmuse",
    )
    return ok(
        {
            "token": token,
            "user": user.public_dict(),
        }
    )


# ============================================================
# 当前用户信息
# ============================================================


@router.get("/me")
async def me(current_user: dict = Depends(get_current_user)):
    return ok(
        {
            "user_id": current_user["user_id"],
            "nickname": current_user.get("nickname", ""),
            "role": current_user.get("role", "user"),
            "username": current_user.get("username", ""),
            "token_type": current_user.get("token_type", "redmuse"),
        }
    )


@router.post("/logout")
async def logout():
    return ok({"logout": True})


# ============================================================
# Phase 0: 用户管理（admin 专用）
# ============================================================


@router.get("/users")
async def list_users(_admin: dict = Depends(require_admin_user)):
    store = get_user_store()
    return ok({"users": [u.public_dict() for u in store.list_users()]})


@router.post("/users")
async def create_user(
    payload: CreateUserRequest,
    _admin: dict = Depends(require_admin_user),
):
    if payload.role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="非法角色")
    store = get_user_store()
    try:
        user = store.create_user(
            username=payload.username,
            password=payload.password,
            nickname=payload.nickname,
            role=payload.role,
            xhs_credential_path=payload.xhs_credential_path,
        )
    except UserAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ok(user.public_dict())


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    admin: dict = Depends(require_admin_user),
):
    if user_id == admin["user_id"]:
        raise HTTPException(status_code=400, detail="不能删除当前登录的管理员账号")
    store = get_user_store()
    if not store.delete_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    return ok({"user_id": user_id, "deleted": True})


@router.patch("/users/{user_id}/password")
async def update_user_password(
    user_id: str,
    payload: UpdatePasswordRequest,
    _admin: dict = Depends(require_admin_user),
):
    store = get_user_store()
    try:
        user = store.set_password(user_id, payload.password)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="用户不存在") from exc
    return ok(user.public_dict())


@router.patch("/users/{user_id}/role")
async def update_user_role(
    user_id: str,
    payload: UpdateRoleRequest,
    admin: dict = Depends(require_admin_user),
):
    if user_id == admin["user_id"] and payload.role != "admin":
        raise HTTPException(status_code=400, detail="不能撤销当前登录管理员的 admin 角色")
    store = get_user_store()
    try:
        user = store.set_role(user_id, payload.role)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="用户不存在") from exc
    return ok(user.public_dict())


# ============================================================
# Legacy XHS 扫码登录（Phase 2 会改造为「XHS 数据源授权」专用入口）
# ============================================================


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
        # XHS 扫码完成的 token 标记为 xhs_selfinfo，与 Phase 0 用户名/密码登录的
        # token_type=redmuse 区分；Phase 2 后该路径会改造为「仅刷新 XHS 凭据」，
        # 不再发放系统 JWT。
        response_data["token"] = create_access_token(user, token_type="xhs_selfinfo")
    else:
        response_data["token"] = None

    return ok(response_data)


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
