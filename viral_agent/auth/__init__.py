"""
认证模块 - JWT Token 认证服务 + 用户管理

提供用户名密码登录、JWT Token 验证、用户管理和个人资料修改功能。
"""
from .auth_service import (
    verify_token,
    verify_token_and_password_changed,
    verify_password,
    create_token,
    init_auth,
    check_must_change_password,
    change_password,
    is_admin,
    is_initial_admin,
    get_initial_admin_username,
    # 底层工具（供外部使用）
    load_users,
    save_users,
)

from .user_manager import (
    create_user,
    delete_user,
    list_users,
    get_user_info,
    update_user_role,
    # 新功能
    change_username,
    update_display_name,
)

__all__ = [
    # 认证核心
    'verify_token',
    'verify_token_and_password_changed',
    'verify_password',
    'create_token',
    'init_auth',
    'check_must_change_password',
    'change_password',
    'is_admin',
    'is_initial_admin',
    'get_initial_admin_username',
    'load_users',
    'save_users',
    # 用户管理
    'create_user',
    'delete_user',
    'list_users',
    'get_user_info',
    'update_user_role',
    # 个人资料
    'change_username',
    'update_display_name',
]
