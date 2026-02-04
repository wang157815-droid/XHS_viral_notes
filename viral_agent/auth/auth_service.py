"""
认证服务 - JWT Token 认证核心

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

# 用户数据文件（放在可持久化数据目录）
USERS_FILE = Path("datas/auth/users.json")

security = HTTPBearer(auto_error=False)


def init_auth() -> None:
    """
    初始化认证系统
    - 检查 JWT_SECRET 是否配置
    - 首次运行时创建随机强密码的管理员账户
    - 为已有用户迁移新字段
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

    # 确保目录存在
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)

    # 首次运行时创建随机密码的管理员
    if not USERS_FILE.exists():
        random_password = generate_random_password(16)
        default_users = {
            "admin": {
                "password": pwd_context.hash(random_password),
                "role": "admin",
                "must_change_password": True,
                "created_at": datetime.now().isoformat(),
                "display_name": "澜斯",
                "is_initial_admin": True
            }
        }
        USERS_FILE.write_text(
            json.dumps(default_users, indent=2, ensure_ascii=False)
        )

        # 设置文件权限为仅所有者可读写
        try:
            USERS_FILE.chmod(0o600)
        except OSError:
            pass  # Windows 不支持 chmod

        logger.warning("=" * 60)
        logger.warning("首次启动！已创建管理员账户：")
        logger.warning(f"  用户名: admin")
        logger.warning(f"  显示名称: 澜斯")
        logger.warning(f"  密码: {random_password}")
        logger.warning("请登录后立即修改密码！")
        logger.warning("=" * 60)

    # 为已有用户迁移新字段
    _migrate_user_fields()

    logger.info("认证系统初始化完成")


def _migrate_user_fields() -> None:
    """为现有用户补充 display_name 和 is_initial_admin 字段（幂等）"""
    users = load_users()
    modified = False

    for username, data in users.items():
        if "display_name" not in data:
            data["display_name"] = "澜斯" if username == "admin" else username
            modified = True

        if "is_initial_admin" not in data:
            data["is_initial_admin"] = (username == "admin")
            modified = True

    if modified:
        save_users(users)
        logger.info("用户数据字段迁移完成（display_name, is_initial_admin）")


# ==================== 底层工具函数 ====================

def generate_random_password(length: int = 16) -> str:
    """生成随机强密码"""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def hash_password(password: str) -> str:
    """使用 bcrypt 哈希密码"""
    return pwd_context.hash(password)


def load_users() -> dict:
    """加载用户数据"""
    if not USERS_FILE.exists():
        return {}
    return json.loads(USERS_FILE.read_text())


def save_users(users: dict) -> None:
    """保存用户数据"""
    USERS_FILE.write_text(json.dumps(users, indent=2, ensure_ascii=False))
    try:
        USERS_FILE.chmod(0o600)
    except OSError:
        pass


# ==================== 密码与认证 ====================

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


# ==================== JWT Token ====================

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
    """验证 JWT Token + 校验用户是否仍然存在"""
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

        # 校验用户是否仍然存在（防止改名/删除后旧 Token 仍可访问）
        users = load_users()
        if username not in users:
            raise HTTPException(
                status_code=401,
                detail="用户不存在或已被更改，请重新登录"
            )

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

    if check_must_change_password(username):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="必须先修改密码才能访问其他功能",
            headers={"X-Must-Change-Password": "true"}
        )

    return username


# ==================== 角色判断 ====================

def is_admin(username: str) -> bool:
    """检查用户是否是管理员"""
    users = load_users()
    if username not in users:
        return False
    return users[username].get("role") == "admin"


def is_initial_admin(username: str) -> bool:
    """检查用户是否是初始管理员（不可删除、不可降级）"""
    users = load_users()
    if username not in users:
        return False
    return users[username].get("is_initial_admin", False)


def get_initial_admin_username() -> str:
    """获取初始管理员的当前用户名（可能已被改名）"""
    users = load_users()
    for username, data in users.items():
        if data.get("is_initial_admin", False):
            return username
    return "admin"  # 终极 fallback
