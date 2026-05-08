"""XHS 数据源凭据相关 API（Phase 1）。

接口列表：

- ``GET /xhs-auth/credential``       —— 当前 RedMuse 用户的凭据（含状态）
- ``GET /xhs-auth/credential/status`` —— 主动跑一次健康检查（轻量）
- ``DELETE /xhs-auth/credential``    —— 解绑当前用户的 XHS 凭据（不删 cookies.json 物理文件）
- ``GET /xhs-auth/credentials``      —— admin 列出所有用户的凭据状态

Phase 1 不提供"重新授权"入口；前端仍走 ``/auth/xhs-login/session`` 走旧扫码流程，
Phase 2 会改造为本路由组的 ``POST /xhs-auth/credential/reauthorize``。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from ...core.responses import ok
from ...core.security import get_current_user, require_admin_user
from ...services.xhs_auth import (
    get_credential_health_checker,
    get_credential_store,
)


router = APIRouter(prefix="/xhs-auth", tags=["xhs-auth"])


def _credential_payload(redmuse_user_id: str) -> Dict[str, Any]:
    cred = get_credential_store().get_by_redmuse_user_id(redmuse_user_id)
    if not cred:
        return {
            "redmuse_user_id": redmuse_user_id,
            "is_bound": False,
            "status": "unbound",
            "status_message": "未绑定小红书账号，请扫码授权",
            "xhs_user_id": None,
            "xhs_nickname": None,
            "last_validated_at": None,
            "created_at": None,
            "updated_at": None,
        }
    return cred.public_dict()


@router.get("/credential")
async def get_my_credential(current_user: Dict[str, Any] = Depends(get_current_user)):
    """读取当前 RedMuse 用户绑定的 XHS 凭据元信息（不返回 cookie 字符串）。"""
    return ok(_credential_payload(current_user["user_id"]))


@router.get("/credential/status")
async def check_my_credential_status(
    force: bool = False,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """主动触发一次凭据健康检查；``force=true`` 时绕过缓存。"""
    health = get_credential_health_checker().check(
        current_user["user_id"], force=force
    )
    return ok(
        {
            "redmuse_user_id": health.redmuse_user_id,
            "status": health.status,
            "message": health.message,
            "is_bound": health.is_bound,
            "last_validated_at": health.last_validated_at,
        }
    )


@router.delete("/credential")
async def unbind_my_credential(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """解绑当前用户的 XHS 凭据。

    仅删除 store 中的索引记录，不动 ``datas/users/<dir>/cookies.json`` 物理文件
    （Phase 2 重新授权时会被 selfinfo 流程覆盖写）。
    """
    deleted = get_credential_store().delete(current_user["user_id"])
    if not deleted:
        raise HTTPException(status_code=404, detail="未绑定 XHS 凭据，无需解绑")
    return ok({"redmuse_user_id": current_user["user_id"], "unbound": True})


@router.get("/credentials")
async def list_all_credentials(_admin: Dict[str, Any] = Depends(require_admin_user)):
    """admin：列出所有用户的凭据状态（不含 cookie 内容）。"""
    creds = get_credential_store().list_credentials()
    return ok({"credentials": [c.public_dict() for c in creds]})
