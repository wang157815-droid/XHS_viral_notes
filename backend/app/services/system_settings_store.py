"""系统设置持久化存储（阶段 4.α 补丁）。

将 `/settings/system` 的配置（文本/视觉/向量模型、视频分析开关、
定时爬虫预热开关等）持久化到 JSON 文件，避免进程重启丢失。

设计原则：
- 文件存储：`datas/config/system_settings.json`，对齐 `IdentityStore` 风格
- 异步接口：便于 ARQ cron 任务和 FastAPI 路由直接 await
- 并发安全：内部用 `asyncio.Lock` 保护读改写
- 降级友好：文件缺失或损坏时返回默认值，不抛异常

后续迁 PostgreSQL 时保持同名方法契约，替换实现即可。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

from ..infrastructure.db.engine import get_business_db_session

try:
    from sqlalchemy import text

    _SA_AVAILABLE = True
except ImportError:  # pragma: no cover
    text = None  # type: ignore[assignment]
    _SA_AVAILABLE = False


DEFAULT_SYSTEM_SETTINGS: Dict[str, Any] = {
    "text_model": "deepseek-chat",
    "vision_model": "qwen3-vl-plus",
    "embedding_model": "text-embedding-v4",
    "video_analysis_enabled": True,
    "crawler_schedule": {
        "enabled": True,
        "interval_hours": 6,
        "hot_keywords_top_n": 50,
    },
}


class SystemSettingsStore:
    """系统设置 JSON 持久化。"""

    def __init__(self, store_file: str = "datas/config/system_settings.json") -> None:
        self.path = Path(store_file)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def get(self) -> Dict[str, Any]:
        """读取完整系统设置（缺失项以默认值补齐）。"""
        async with self._lock:
            data = self._load_raw()
        return self._merge_with_defaults(data)

    async def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """局部更新并持久化，返回更新后的完整设置。

        - 顶层字段覆盖：`text_model`、`video_analysis_enabled` 等
        - 嵌套 dict 深合并：`crawler_schedule` 内部字段可单独更新
        - 未在 patch 中出现的字段保持原值
        """
        async with self._lock:
            current = self._load_raw()
            merged = self._deep_merge(current, patch)
            merged["_updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save_raw(merged)
        return self._merge_with_defaults(merged)

    async def replace(self, new_settings: Dict[str, Any]) -> Dict[str, Any]:
        """完整替换（覆盖写），用于 PUT 整份 payload。"""
        async with self._lock:
            payload = dict(new_settings)
            payload["_updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save_raw(payload)
        return self._merge_with_defaults(payload)

    async def get_crawler_schedule(self) -> Dict[str, Any]:
        """便捷方法：读取 crawler_schedule 配置段。"""
        data = await self.get()
        schedule = data.get("crawler_schedule") or {}
        return {
            "enabled": bool(schedule.get("enabled", True)),
            "interval_hours": int(schedule.get("interval_hours", 6)),
            "hot_keywords_top_n": int(schedule.get("hot_keywords_top_n", 50)),
        }

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _load_raw(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            raw = self.path.read_text(encoding="utf-8")
            if not raw.strip():
                return {}
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[system_settings_store] 读取失败({exc}),返回空配置")
            return {}

    def _save_raw(self, data: Dict[str, Any]) -> None:
        try:
            self.path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[system_settings_store] 写入失败: {exc}")
            raise

    def _merge_with_defaults(self, data: Dict[str, Any]) -> Dict[str, Any]:
        merged = self._deep_merge(DEFAULT_SYSTEM_SETTINGS, data)
        merged.pop("_updated_at", None)
        return merged

    def _deep_merge(self, base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
        """深合并：嵌套 dict 递归合并，其他类型直接覆盖。"""
        if not isinstance(base, dict):
            return dict(patch) if isinstance(patch, dict) else patch
        if not isinstance(patch, dict):
            return dict(base)

        result: Dict[str, Any] = {**base}
        for k, v in patch.items():
            if isinstance(v, dict) and isinstance(result.get(k), dict):
                result[k] = self._deep_merge(result[k], v)
            else:
                result[k] = v
        return result


class SqlAlchemySystemSettingsStore(SystemSettingsStore):
    """PostgreSQL-backed system settings store."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    def _load_raw(self) -> Dict[str, Any]:
        with get_business_db_session() as session:
            row = session.execute(
                text("SELECT settings FROM system_settings WHERE id = 1")
            ).first()
        if not row:
            return {}
        data = row[0]
        return data if isinstance(data, dict) else {}

    def _save_raw(self, data: Dict[str, Any]) -> None:
        with get_business_db_session() as session:
            session.execute(
                text(
                    """
                    INSERT INTO system_settings(id, settings, updated_at)
                    VALUES (1, CAST(:settings AS jsonb), NOW())
                    ON CONFLICT (id) DO UPDATE SET
                        settings = EXCLUDED.settings,
                        updated_at = NOW()
                    """
                ),
                {"settings": json.dumps(data, ensure_ascii=False)},
            )


_default_store: Optional[SystemSettingsStore] = None


def get_system_settings_store() -> SystemSettingsStore:
    """返回默认全局单例，方便路由/cron 直接调用。"""
    global _default_store
    if _default_store is None:
        _default_store = SqlAlchemySystemSettingsStore()
    return _default_store
