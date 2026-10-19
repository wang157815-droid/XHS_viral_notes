"""知识库检索范围（M1，设计 v1.1 §G.M1.2）。

档位（flag ``KNOWLEDGE_SEARCH_SCOPE``）：

- ``legacy``：全部文档（仅紧急回滚）；
- ``owner_or_admin``：本人 + admin 用户的文档 + 历史默认 owner 字面量 ``'admin'`` 的老文档
  （过渡档，仅 QA 与 search_knowledge 使用）；admin 名单读取失败时**失败关闭**为仅本人；
- ``strict``：本人 + ``visibility='shared'``；不使用 admin 概念。

所有过滤条件都以别名 ``d``（knowledge_documents）为前提，参数全部绑定，不拼接用户值。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from ..core.feature_flags import get_flag


SCOPE_LEGACY = "legacy"
SCOPE_OWNER_OR_ADMIN = "owner_or_admin"
SCOPE_STRICT = "strict"
SCOPES = (SCOPE_LEGACY, SCOPE_OWNER_OR_ADMIN, SCOPE_STRICT)

TRUST_OWNER_DOC = "owner_doc"
TRUST_SHARED_DOC = "shared_doc"

LEGACY_ADMIN_OWNER = "admin"  # knowledge_documents.owner_user_id 的历史默认值

_ADMIN_IDS_TTL_SEC = 60.0
_admin_ids_cache: Optional[Tuple[float, List[str]]] = None
_admin_ids_lock = threading.Lock()


def default_scope() -> str:
    raw = get_flag("KNOWLEDGE_SEARCH_SCOPE").strip().lower()
    if raw in SCOPES:
        return raw
    logger.warning("KNOWLEDGE_SEARCH_SCOPE={!r} 非法，使用 {}", raw, SCOPE_OWNER_OR_ADMIN)
    return SCOPE_OWNER_OR_ADMIN


def resolve_scope(scope: Optional[str]) -> str:
    if scope is None:
        return default_scope()
    if scope not in SCOPES:
        raise ValueError(f"未知检索档位: {scope}")
    return scope


def admin_user_ids() -> List[str]:
    """从用户存储（json / pg 均可）取 role=admin 的 user_id，进程内缓存约 60 秒。

    读取失败直接抛出，由调用方失败关闭；失败结果不缓存。
    """
    global _admin_ids_cache
    now = time.monotonic()
    with _admin_ids_lock:
        if _admin_ids_cache and now - _admin_ids_cache[0] < _ADMIN_IDS_TTL_SEC:
            return list(_admin_ids_cache[1])
    from .redmuse_auth.user_store import get_user_store

    ids = sorted(
        str(user.user_id)
        for user in get_user_store().list_users()
        if str(getattr(user, "role", "")).strip().lower() == "admin"
    )
    with _admin_ids_lock:
        _admin_ids_cache = (now, ids)
    return list(ids)


def clear_admin_ids_cache() -> None:
    global _admin_ids_cache
    with _admin_ids_lock:
        _admin_ids_cache = None


def build_scope_filter(scope: str, owner_user_id: Optional[str]) -> Tuple[str, Dict[str, Any]]:
    """返回 ``(AND 子句, 绑定参数)``；legacy 返回空子句。"""
    uid = owner_user_id or ""
    if scope == SCOPE_LEGACY:
        return "", {}
    if scope == SCOPE_STRICT:
        return (
            "AND (d.owner_user_id = :owner_user_id OR d.visibility = 'shared')",
            {"owner_user_id": uid},
        )
    if scope == SCOPE_OWNER_OR_ADMIN:
        try:
            admin_ids = admin_user_ids()
        except Exception as exc:  # noqa: BLE001 - 失败关闭：只给本人文档
            logger.warning("知识库检索：读取 admin 名单失败，过渡档失败关闭为仅本人文档: {}", exc)
            return "AND d.owner_user_id = :owner_user_id", {"owner_user_id": uid}
        return (
            "AND (d.owner_user_id = :owner_user_id"
            " OR d.owner_user_id = ANY(CAST(:admin_ids AS text[]))"
            " OR d.owner_user_id = :legacy_admin_owner)",
            {"owner_user_id": uid, "admin_ids": admin_ids, "legacy_admin_owner": LEGACY_ADMIN_OWNER},
        )
    raise ValueError(f"未知检索档位: {scope}")


def trust_level(doc_owner_user_id: Optional[str], owner_user_id: Optional[str]) -> str:
    """本人文档为 owner_doc，其余（他人 shared / 过渡档的 admin 文档）为 shared_doc。"""
    if owner_user_id and doc_owner_user_id == owner_user_id:
        return TRUST_OWNER_DOC
    return TRUST_SHARED_DOC
