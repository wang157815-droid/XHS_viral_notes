"""
用户管理 - CRUD 操作 + 用户名/显示名称修改

提供用户创建、删除、查询、角色管理，
以及用户名修改和显示名称修改功能。
"""
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from .auth_service import (
    load_users,
    save_users,
    pwd_context,
    verify_password,
    create_token,
    is_initial_admin,
)


# ==================== 用户查询 ====================

def list_users() -> list:
    """获取所有用户列表（不含密码）"""
    users = load_users()
    result = []
    for username, data in users.items():
        result.append({
            "username": username,
            "display_name": data.get("display_name", username),
            "role": data.get("role", "user"),
            "is_initial_admin": data.get("is_initial_admin", False),
            "created_at": data.get("created_at"),
            "created_by": data.get("created_by"),
            "must_change_password": data.get("must_change_password", False),
            "password_changed_at": data.get("password_changed_at")
        })
    return result


def get_user_info(username: str) -> Optional[dict]:
    """获取单个用户信息（不含密码）"""
    users = load_users()
    if username not in users:
        return None

    data = users[username]
    return {
        "username": username,
        "display_name": data.get("display_name", username),
        "role": data.get("role", "user"),
        "is_initial_admin": data.get("is_initial_admin", False),
        "created_at": data.get("created_at"),
        "created_by": data.get("created_by"),
        "must_change_password": data.get("must_change_password", False),
        "password_changed_at": data.get("password_changed_at")
    }


# ==================== 用户创建与删除 ====================

def create_user(
    username: str,
    password: str,
    role: str = "user",
    created_by: Optional[str] = None
) -> dict:
    """创建新用户。Raises ValueError if invalid."""
    _validate_username(username)

    if len(password) < 8:
        raise ValueError("密码长度至少8位")

    users = load_users()
    if username in users:
        raise ValueError(f"用户名 '{username}' 已存在")

    users[username] = {
        "password": pwd_context.hash(password),
        "role": role if role in ("admin", "user") else "user",
        "must_change_password": True,
        "created_at": datetime.now().isoformat(),
        "created_by": created_by,
        "display_name": username,
        "is_initial_admin": False
    }
    save_users(users)

    logger.info(f"用户创建成功: {username} (角色: {role}, 创建者: {created_by})")

    return {
        "username": username,
        "role": users[username]["role"],
        "created_at": users[username]["created_at"]
    }


def delete_user(username: str, deleted_by: str) -> bool:
    """
    删除用户

    Raises:
        ValueError: 不能删除初始管理员或自己
    """
    if is_initial_admin(username):
        raise ValueError("不能删除初始管理员账户")

    if username == deleted_by:
        raise ValueError("不能删除自己的账户")

    users = load_users()
    if username not in users:
        return False

    del users[username]
    save_users(users)

    logger.info(f"用户已删除: {username} (操作者: {deleted_by})")
    return True


# ==================== 角色管理 ====================

def update_user_role(username: str, new_role: str, updated_by: str) -> bool:
    """更新用户角色"""
    if is_initial_admin(username) and new_role != "admin":
        raise ValueError("不能更改初始管理员账户的角色")

    if new_role not in ("admin", "user"):
        raise ValueError("角色只能是 admin 或 user")

    users = load_users()
    if username not in users:
        return False

    users[username]["role"] = new_role
    users[username]["role_updated_at"] = datetime.now().isoformat()
    users[username]["role_updated_by"] = updated_by
    save_users(users)

    logger.info(f"用户角色已更新: {username} -> {new_role} (操作者: {updated_by})")
    return True


# ==================== 显示名称 ====================

def update_display_name(username: str, new_display_name: str) -> bool:
    """修改显示名称（1-30 字符，允许中文/英文/emoji）"""
    new_display_name = new_display_name.strip()
    if not new_display_name or len(new_display_name) > 30:
        raise ValueError("显示名称长度必须在 1-30 字符之间")

    users = load_users()
    if username not in users:
        return False

    users[username]["display_name"] = new_display_name
    save_users(users)

    logger.info(f"显示名称已更新: {username} -> {new_display_name}")
    return True


# ==================== 修改用户名 ====================

def change_username(old_username: str, new_username: str, password: str) -> dict:
    """修改用户名：验证密码→更新JSON→迁移目录→更新引用→签发新Token"""
    if old_username == new_username:
        raise ValueError("新用户名与当前用户名相同")

    # 1. 密码二次验证
    if not verify_password(old_username, password):
        raise ValueError("密码错误")

    # 2. 验证新用户名格式
    _validate_username(new_username)

    # 3. 检查新用户名是否已占用
    users = load_users()
    if old_username not in users:
        raise ValueError("当前用户不存在")
    if new_username in users:
        raise ValueError(f"用户名 '{new_username}' 已被占用")

    # 4. 更新 users.json（原子操作：读 → 改 → 整体写）
    user_data = users.pop(old_username)
    user_data["username_changed_at"] = datetime.now().isoformat()
    previous = user_data.get("previous_usernames", [])
    previous.append(old_username)
    user_data["previous_usernames"] = previous

    # 如果 display_name 等于旧用户名或为空，同步更新为新用户名
    current_display = user_data.get("display_name", "")
    if not current_display or current_display == old_username:
        user_data["display_name"] = new_username

    users[new_username] = user_data

    # 5. 更新其他用户的引用
    for _uname, _udata in users.items():
        if _udata.get("created_by") == old_username:
            _udata["created_by"] = new_username
        if _udata.get("role_updated_by") == old_username:
            _udata["role_updated_by"] = new_username

    save_users(users)

    # 6. 重命名用户数据目录
    _rename_user_directory(old_username, new_username, users)

    # 7. 清理用户数据服务缓存
    _clear_user_service_cache(old_username)

    # 8. 签发新 Token
    new_token = create_token(new_username)

    logger.info(f"用户名已修改: {old_username} -> {new_username}")

    return {
        "new_username": new_username,
        "new_token": new_token,
        "display_name": user_data.get("display_name", new_username)
    }


# ==================== 内部辅助函数 ====================

def _validate_username(username: str) -> None:
    """验证用户名格式"""
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]{2,19}$', username):
        raise ValueError(
            "用户名必须以字母开头，只能包含字母、数字、下划线，长度3-20位"
        )


def _rename_user_directory(
    old_username: str,
    new_username: str,
    users_backup: dict
) -> None:
    """重命名用户数据目录，失败时回滚 users.json"""
    old_dir = Path(f"datas/users/{old_username}")
    new_dir = Path(f"datas/users/{new_username}")

    if not old_dir.exists():
        return

    if new_dir.exists():
        # 回滚 users.json
        _rollback_username_change(old_username, new_username, users_backup)
        raise ValueError("目标用户数据目录已存在，无法重命名")

    try:
        os.rename(str(old_dir), str(new_dir))
    except OSError as e:
        _rollback_username_change(old_username, new_username, users_backup)
        raise ValueError(f"用户数据目录重命名失败: {e}")


def _rollback_username_change(
    old_username: str,
    new_username: str,
    users: dict
) -> None:
    """回滚用户名修改（恢复 users.json）"""
    logger.warning(f"回滚用户名修改: {new_username} -> {old_username}")

    user_data = users.pop(new_username, None)
    if user_data:
        previous = user_data.get("previous_usernames", [])
        if previous and previous[-1] == old_username:
            previous.pop()
        user_data.pop("username_changed_at", None)
        users[old_username] = user_data

        for _uname, _udata in users.items():
            if _udata.get("created_by") == new_username:
                _udata["created_by"] = old_username
            if _udata.get("role_updated_by") == new_username:
                _udata["role_updated_by"] = old_username

        save_users(users)


def _clear_user_service_cache(old_username: str) -> None:
    """清理 UserDataService 的单例缓存"""
    try:
        from viral_agent.services.user_data_service import _user_services
        _user_services.pop(old_username, None)
    except ImportError:
        pass
