"""焦点关键词持久化存储（阶段 4.α 补丁）。

管理员在前端"设置 → 焦点关键词"配置的关键词列表,存到
`datas/config/focus_keywords.json`,ARQ worker 的 `scheduled_warmup` 任务从
这里读取,实现"前端配置 → 后端持久化 → worker 真实使用"的完整链路。

规范化规则：
- 去除前后空白,丢弃空字符串
- 大小写不敏感去重（按 lower() 比较,保留首次出现的原写法）
- 上限 50 条（上层也可调整）
- 写入时加 `_updated_at` 时间戳

设计对齐 `system_settings_store.py`：JSON 文件 + asyncio.Lock。
后续迁 PostgreSQL 时保持方法签名替换实现即可。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from loguru import logger

from ..infrastructure.db.engine import get_business_db_session

try:
    from sqlalchemy import text

    _SA_AVAILABLE = True
except ImportError:  # pragma: no cover
    text = None  # type: ignore[assignment]
    _SA_AVAILABLE = False


_MAX_ITEMS_DEFAULT = 50
_MAX_KW_LENGTH = 32  # 单个关键词字符上限,避免奇葩长串


class FocusKeywordsStore:
    """焦点关键词 JSON 持久化。"""

    def __init__(
        self,
        store_file: str = "datas/config/focus_keywords.json",
        max_items: int = _MAX_ITEMS_DEFAULT,
    ) -> None:
        self.path = Path(store_file)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_items = max_items
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    async def get(self) -> List[str]:
        """读取当前关键词列表（保序）。"""
        async with self._lock:
            raw = self._load_raw()
        return list(raw.get("items", []))

    async def replace(self, items: Iterable[str]) -> List[str]:
        """整体替换关键词列表,返回规范化后的结果。"""
        normalized = self._normalize_list(items)
        async with self._lock:
            self._save_raw(
                {
                    "items": normalized,
                    "_updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        return normalized

    async def add(self, keyword: str) -> List[str]:
        """追加单个关键词(已存在则 no-op),返回最新列表。"""
        async with self._lock:
            raw = self._load_raw()
            current = list(raw.get("items", []))
            merged = self._normalize_list(current + [keyword])
            self._save_raw(
                {
                    "items": merged,
                    "_updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        return merged

    async def remove(self, keyword: str) -> List[str]:
        """删除单个关键词(不区分大小写),返回最新列表。"""
        target = (keyword or "").strip().lower()
        if not target:
            return await self.get()

        async with self._lock:
            raw = self._load_raw()
            current = list(raw.get("items", []))
            filtered = [kw for kw in current if kw.lower() != target]
            self._save_raw(
                {
                    "items": filtered,
                    "_updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        return filtered

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _load_raw(self) -> dict:
        if not self.path.exists():
            return {"items": []}
        try:
            text = self.path.read_text(encoding="utf-8")
            if not text.strip():
                return {"items": []}
            data = json.loads(text)
            # 兼容两种历史格式：纯 list 或 {"items": [...]}
            if isinstance(data, list):
                return {"items": [str(x) for x in data if x]}
            if isinstance(data, dict):
                items = data.get("items") or []
                return {"items": [str(x) for x in items if x]}
            return {"items": []}
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[focus_keywords_store] 读取失败({exc}),返回空列表")
            return {"items": []}

    def _save_raw(self, data: dict) -> None:
        try:
            self.path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[focus_keywords_store] 写入失败: {exc}")
            raise

    def _normalize_list(self, items: Iterable[str]) -> List[str]:
        """规范化：trim + 丢空 + 截长度 + 保序大小写不敏感去重 + 总数上限。"""
        seen_lower: set[str] = set()
        out: List[str] = []
        for raw in items or []:
            if raw is None:
                continue
            s = str(raw).strip()
            if not s:
                continue
            if len(s) > _MAX_KW_LENGTH:
                s = s[:_MAX_KW_LENGTH]
            key = s.lower()
            if key in seen_lower:
                continue
            seen_lower.add(key)
            out.append(s)
            if len(out) >= self.max_items:
                break
        return out


class SqlAlchemyFocusKeywordsStore(FocusKeywordsStore):
    """PostgreSQL-backed focus keywords store."""

    def __init__(self, max_items: int = _MAX_ITEMS_DEFAULT) -> None:
        self.max_items = max_items
        self._lock = asyncio.Lock()

    def _load_raw(self) -> dict:
        with get_business_db_session() as session:
            row = session.execute(
                text("SELECT items FROM focus_keywords WHERE id = 1")
            ).first()
        if not row:
            return {"items": []}
        items = row[0] or []
        return {"items": [str(x) for x in items if x]}

    def _save_raw(self, data: dict) -> None:
        with get_business_db_session() as session:
            session.execute(
                text(
                    """
                    INSERT INTO focus_keywords(id, items, updated_at)
                    VALUES (1, CAST(:items AS jsonb), NOW())
                    ON CONFLICT (id) DO UPDATE SET
                        items = EXCLUDED.items,
                        updated_at = NOW()
                    """
                ),
                {"items": json.dumps(data.get("items") or [], ensure_ascii=False)},
            )


_default_store: Optional[FocusKeywordsStore] = None


def get_focus_keywords_store() -> FocusKeywordsStore:
    """返回默认全局单例。"""
    global _default_store
    if _default_store is None:
        _default_store = SqlAlchemyFocusKeywordsStore()
    return _default_store
