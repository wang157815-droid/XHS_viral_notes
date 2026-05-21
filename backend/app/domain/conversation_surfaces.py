"""会话所属「产品面」：侧栏与列表过滤用。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

VALID_SURFACES = frozenset({"insight", "hotspot", "post_investment"})
DEFAULT_SURFACE = "insight"


def normalize_metadata_for_write(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """创建/更新会话时规范化 metadata.surface。"""
    md = dict(metadata or {})
    raw = md.get("surface")
    if raw is None or str(raw).strip() == "":
        md["surface"] = DEFAULT_SURFACE
    else:
        s = str(raw).strip()
        md["surface"] = s if s in VALID_SURFACES else DEFAULT_SURFACE
    return md


def effective_surface(metadata: Optional[Dict[str, Any]]) -> str:
    raw = (metadata or {}).get("surface")
    if raw is None or str(raw).strip() == "":
        return DEFAULT_SURFACE
    s = str(raw).strip()
    return s if s in VALID_SURFACES else DEFAULT_SURFACE


def matches_surfaces(metadata: Optional[Dict[str, Any]], surfaces: Optional[List[str]]) -> bool:
    if not surfaces:
        return True
    return effective_surface(metadata) in frozenset(surfaces)


def parse_surfaces_query(raw: Optional[str]) -> Optional[List[str]]:
    if not raw or not str(raw).strip():
        return None
    parts = [p.strip() for p in str(raw).split(",") if p.strip()]
    if not parts:
        return None
    cleaned = [p for p in parts if p in VALID_SURFACES]
    return cleaned or None
