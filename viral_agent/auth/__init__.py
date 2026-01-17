"""
认证模块 - JWT Token 认证服务

提供用户名密码登录、JWT Token 验证和用户管理功能。
"""
from .auth_service import (
    verify_token,
    verify_token_and_password_changed,
    verify_password,
    create_token,
    init_auth,
    check_must_change_password,
    change_password,
    # 用户管理函数
    is_admin,
    create_user,
    delete_user,
    list_users,
    get_user_info,
    update_user_role
)

__all__ = [
    'verify_token',
    'verify_token_and_password_changed',
    'verify_password',
    'create_token',
    'init_auth',
    'check_must_change_password',
    'change_password',
    # 用户管理
    'is_admin',
    'create_user',
    'delete_user',
    'list_users',
    'get_user_info',
    'update_user_role'
]
