"""bcrypt 密码哈希包装。

使用 passlib 的 ``CryptContext`` 既可保证哈希算法可升级，又能在校验时识别历史哈希。
当前默认使用 bcrypt rounds=12，与 requirements 中锁定的 ``bcrypt==4.0.1`` 兼容。
"""

from __future__ import annotations

from typing import Final

from passlib.context import CryptContext

_PWD_CONTEXT: Final[CryptContext] = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12,
)


def hash_password(plain: str) -> str:
    """返回 bcrypt 哈希字符串。"""
    if not plain:
        raise ValueError("密码不能为空")
    return _PWD_CONTEXT.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """校验明文密码是否匹配哈希。无效哈希直接返回 False。"""
    if not plain or not hashed:
        return False
    try:
        return _PWD_CONTEXT.verify(plain, hashed)
    except Exception:
        return False


def needs_rehash(hashed: str) -> bool:
    """判断哈希是否需要升级（例如 rounds 调高）。"""
    if not hashed:
        return False
    try:
        return _PWD_CONTEXT.needs_update(hashed)
    except Exception:
        return False
