"""首启动 admin 用户引导（Phase 0）。

读取环境变量：

- ``REDMUSE_BOOTSTRAP_ADMIN_USER``：默认 ``admin``
- ``REDMUSE_BOOTSTRAP_ADMIN_PASSWORD``：必填，缺失则跳过引导（开发环境）
- ``REDMUSE_BOOTSTRAP_ADMIN_NICKNAME``：默认 ``管理员``
- ``REDMUSE_BOOTSTRAP_XHS_CREDENTIAL_PATH``：默认 ``datas/users/admin/cookies.json``

只有当 user store 为空时才创建。已存在用户则保持不动，避免覆盖手工修改的密码。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from loguru import logger

from .user_store import RedMuseUserStore, UserAlreadyExistsError, get_user_store


_DEFAULT_ADMIN_USERNAME = "admin"
_DEFAULT_ADMIN_NICKNAME = "管理员"
_DEFAULT_XHS_CREDENTIAL_PATH = "datas/users/admin/cookies.json"


def bootstrap_admin_if_needed(
    store: Optional[RedMuseUserStore] = None,
) -> Optional[str]:
    """如果用户表为空，根据环境变量创建首个 admin。

    Returns:
        新创建的 admin user_id；未创建（已存在用户或环境变量缺失）返回 ``None``。
    """
    store = store or get_user_store()
    if store.count() > 0:
        return None

    username = (os.getenv("REDMUSE_BOOTSTRAP_ADMIN_USER") or _DEFAULT_ADMIN_USERNAME).strip()
    password = os.getenv("REDMUSE_BOOTSTRAP_ADMIN_PASSWORD") or ""
    nickname = (os.getenv("REDMUSE_BOOTSTRAP_ADMIN_NICKNAME") or _DEFAULT_ADMIN_NICKNAME).strip()
    xhs_path = (
        os.getenv("REDMUSE_BOOTSTRAP_XHS_CREDENTIAL_PATH")
        or _DEFAULT_XHS_CREDENTIAL_PATH
    ).strip() or None

    if not password:
        logger.warning(
            "未配置 REDMUSE_BOOTSTRAP_ADMIN_PASSWORD，跳过首启动 admin 引导。"
            "前端登录前请先通过脚本或 .env 创建 admin。"
        )
        return None

    # 校验 xhs cookie 路径是否实际存在；不存在也不阻塞，只记日志方便排查
    if xhs_path:
        candidate = Path(xhs_path)
        if not candidate.exists():
            logger.info(
                f"REDMUSE 首启动：未找到既有 XHS Cookie 文件 {candidate}，"
                f"admin 创建后需在「设置 → 数据源授权」中重新扫码绑定。"
            )

    try:
        user = store.create_user(
            username=username,
            password=password,
            nickname=nickname,
            role="admin",
            xhs_credential_path=xhs_path,
        )
    except UserAlreadyExistsError:
        return None

    logger.info(
        f"RedMuse 首启动 admin 已创建: username={user.username}, "
        f"user_id={user.user_id}, xhs_credential_path={user.xhs_credential_path or '未挂载'}"
    )
    return user.user_id
