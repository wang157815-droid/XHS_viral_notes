"""RedMuse 系统级身份与认证模块（Phase 0）。

本模块提供与小红书登录解耦的本地系统用户体系：
- 用户存储：``datas/redmuse_auth/users.json``（JSON 文件，便于 Phase 5 迁移到 PostgreSQL）。
- 密码哈希：bcrypt（通过 passlib 包装）。
- 引导：通过环境变量 ``REDMUSE_BOOTSTRAP_ADMIN_USER`` / ``REDMUSE_BOOTSTRAP_ADMIN_PASSWORD``
  在首次启动时创建第一个 admin，并挂载 ``datas/users/admin/cookies.json`` 作为其 XHS 凭据。

Phase 0 范围内仅做最小可用闭环；完整 RBAC（角色矩阵、SSO、审计）留给 Phase 5。
"""

from .password_hash import hash_password, verify_password
from .user_store import (
    RedMuseUser,
    RedMuseUserStore,
    UserAlreadyExistsError,
    UserNotFoundError,
    get_user_store,
)
from .bootstrap import bootstrap_admin_if_needed

__all__ = [
    "RedMuseUser",
    "RedMuseUserStore",
    "UserAlreadyExistsError",
    "UserNotFoundError",
    "get_user_store",
    "hash_password",
    "verify_password",
    "bootstrap_admin_if_needed",
]
