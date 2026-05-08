"""
认证服务 - 用户名密码 + JWT Token

安全特性：
- JWT_SECRET 必须从环境变量读取，缺失时拒绝启动
- 密码使用 bcrypt（passlib）进行安全哈希
- 首次启动生成随机强密码，强制用户修改
- 用户数据文件权限限制为 0o600
"""
import os
import json
import secrets
import string
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from loguru import logger

from viral_agent.project_paths import get_data_root

# 使用 passlib 的 bcrypt
try:
    from passlib.context import CryptContext
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
except ImportError:
    raise ImportError("请安装 passlib[bcrypt]: pip install 'passlib[bcrypt]'")

import jwt

# JWT 配置 - 必须从环境变量读取
JWT_SECRET: Optional[str] = None
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24

security = HTTPBearer(auto_error=False)


def get_users_file() -> Path:
    """用户账户 JSON（绝对路径，不随 cwd 变化）。"""
    return get_data_root() / "auth" / "users.json"


def init_auth() -> None:
    """
    初始化认证系统
    - 检查 JWT_SECRET 是否配置
    - 首次运行时创建随机强密码的管理员账户
    """
    global JWT_SECRET

    # 从环境变量读取 JWT_SECRET
    JWT_SECRET = os.getenv("JWT_SECRET")

    # 强制要求 JWT_SECRET
    if not JWT_SECRET:
        logger.error("=" * 60)
        logger.error("错误：未配置 JWT_SECRET 环境变量！")
        logger.error("请在 .env 文件中添加：")
        logger.error(f'JWT_SECRET="{secrets.token_hex(32)}"')
        logger.error("=" * 60)
        raise RuntimeError("JWT_SECRET 未配置，拒绝启动")

    users_file = get_users_file()
    # 确保目录存在
    users_file.parent.mkdir(parents=True, exist_ok=True)

    if not users_file.exists():
        cwd_auth = Path.cwd() / "datas" / "auth" / "users.json"
        if cwd_auth.is_file():
            logger.warning(
                f"未找到账户文件 {users_file}，但当前工作目录下存在 {cwd_auth.resolve()}。"
                "若曾从子目录启动应用，请将原 datas 整目录合并到项目根下 datas，"
                "或设置环境变量 XHS_DATA_ROOT 指向原 datas 目录的绝对路径后重启。"
            )

    # 首次运行时创建随机密码的管理员
    if not users_file.exists():
        random_password = generate_random_password(16)
        default_users = {
            "admin": {
                "password": pwd_context.hash(random_password),
                "role": "admin",
                "must_change_password": True,
                "created_at": datetime.now().isoformat()
            }
        }
        users_file.write_text(
            json.dumps(default_users, indent=2, ensure_ascii=False)
        )

        # 设置文件权限为仅所有者可读写
        try:
            users_file.chmod(0o600)
        except OSError:
            pass  # Windows 不支持 chmod

        logger.warning("=" * 60)
        logger.warning("首次启动！已创建管理员账户：")
        logger.warning(f"  用户名: admin")
        logger.warning(f"  密码: {random_password}")
        logger.warning("请登录后立即修改密码！")
        logger.warning("=" * 60)

    logger.info("认证系统初始化完成")


def generate_random_password(length: int = 16) -> str:
    """生成随机强密码"""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def hash_password(password: str) -> str:
    """使用 bcrypt 哈希密码"""
    return pwd_context.hash(password)


def load_users() -> dict:
    """加载用户数据"""
    uf = get_users_file()
    if not uf.exists():
        return {}
    return json.loads(uf.read_text())


def save_users(users: dict) -> None:
    """保存用户数据"""
    uf = get_users_file()
    uf.write_text(json.dumps(users, indent=2, ensure_ascii=False))
    try:
        uf.chmod(0o600)
    except OSError:
        pass


def verify_password(username: str, password: str) -> bool:
    """验证用户密码（使用 bcrypt）"""
    users = load_users()
    if username not in users:
        return False
    return pwd_context.verify(password, users[username]["password"])


def check_must_change_password(username: str) -> bool:
    """检查是否需要强制修改密码"""
    users = load_users()
    if username in users:
        return users[username].get("must_change_password", False)
    return False


def change_password(username: str, new_password: str) -> bool:
    """修改密码"""
    users = load_users()
    if username not in users:
        return False
    users[username]["password"] = pwd_context.hash(new_password)
    users[username]["must_change_password"] = False
    users[username]["password_changed_at"] = datetime.now().isoformat()
    save_users(users)
    return True


def create_token(username: str) -> str:
    """创建 JWT Token"""
    if not JWT_SECRET:
        raise RuntimeError("JWT_SECRET 未初始化")
    expire = datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {
        "sub": username,
        "exp": expire,
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> str:
    """验证 JWT Token（FastAPI 依赖注入）- 仅验证 token 有效性"""
    if not JWT_SECRET:
        raise RuntimeError("JWT_SECRET 未初始化")

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要登录认证",
            headers={"WWW-Authenticate": "Bearer"}
        )

    try:
        payload = jwt.decode(
            credentials.credentials,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM]
        )
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="无效的 Token")
        return username
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token 已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="无效的 Token")


def verify_token_and_password_changed(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> str:
    """
    验证 JWT Token + 检查是否已修改密码

    如果用户 must_change_password=True，除了修改密码接口外，
    其他所有接口都会返回 403 Forbidden。
    """
    username = verify_token(credentials)

    # 检查是否需要强制修改密码
    if check_must_change_password(username):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="必须先修改密码才能访问其他功能",
            headers={"X-Must-Change-Password": "true"}
        )

    return username


# ==================== 用户管理函数 ====================

def is_admin(username: str) -> bool:
    """检查用户是否是管理员"""
    users = load_users()
    if username not in users:
        return False
    return users[username].get("role") == "admin"


def create_user(
    username: str,
    password: str,
    role: str = "user",
    created_by: Optional[str] = None
) -> dict:
    """
    创建新用户

    Args:
        username: 用户名（只能包含字母、数字、下划线）
        password: 密码（至少8位）
        role: 角色（admin/user）
        created_by: 创建者用户名

    Returns:
        新用户信息

    Raises:
        ValueError: 用户名已存在或格式错误
    """
    import re

    # 验证用户名格式
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]{2,19}$', username):
        raise ValueError("用户名必须以字母开头，只能包含字母、数字、下划线，长度3-20位")

    # 验证密码长度
    if len(password) < 8:
        raise ValueError("密码长度至少8位")

    users = load_users()
    if username in users:
        raise ValueError(f"用户名 '{username}' 已存在")

    # 创建用户
    users[username] = {
        "password": pwd_context.hash(password),
        "role": role if role in ("admin", "user") else "user",
        "must_change_password": True,  # 新用户首次登录需改密
        "created_at": datetime.now().isoformat(),
        "created_by": created_by
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

    Args:
        username: 要删除的用户名
        deleted_by: 执行删除的用户名

    Returns:
        是否成功

    Raises:
        ValueError: 不能删除自己或admin账户
    """
    if username == "admin":
        raise ValueError("不能删除 admin 账户")

    if username == deleted_by:
        raise ValueError("不能删除自己的账户")

    users = load_users()
    if username not in users:
        return False

    del users[username]
    save_users(users)

    logger.info(f"用户已删除: {username} (操作者: {deleted_by})")
    return True


def list_users() -> list:
    """
    获取所有用户列表（不含密码）

    Returns:
        用户信息列表
    """
    users = load_users()
    result = []
    for username, data in users.items():
        result.append({
            "username": username,
            "role": data.get("role", "user"),
            "created_at": data.get("created_at"),
            "created_by": data.get("created_by"),
            "must_change_password": data.get("must_change_password", False),
            "password_changed_at": data.get("password_changed_at")
        })
    return result


def get_user_info(username: str) -> Optional[dict]:
    """
    获取单个用户信息（不含密码）

    Args:
        username: 用户名

    Returns:
        用户信息字典，用户不存在返回 None
    """
    users = load_users()
    if username not in users:
        return None

    data = users[username]
    return {
        "username": username,
        "role": data.get("role", "user"),
        "created_at": data.get("created_at"),
        "created_by": data.get("created_by"),
        "must_change_password": data.get("must_change_password", False),
        "password_changed_at": data.get("password_changed_at")
    }


def update_user_role(username: str, new_role: str, updated_by: str) -> bool:
    """
    更新用户角色

    Args:
        username: 用户名
        new_role: 新角色（admin/user）
        updated_by: 操作者用户名

    Returns:
        是否成功
    """
    if username == "admin" and new_role != "admin":
        raise ValueError("不能更改 admin 账户的角色")

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
