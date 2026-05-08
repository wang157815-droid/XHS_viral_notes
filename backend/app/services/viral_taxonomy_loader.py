"""
ViralTaxonomyLoader: 爆文 6 要素分类枚举的加载 + 管理(阶段 4.3pre.1)。

软编码策略(用户 Q2 确认):
- 初始枚举以 DEFAULT_VIRAL_TAXONOMY 常量形式随代码版本控制
- 运行时覆盖存 datas/config/viral_taxonomy.json (管理员可在设置页编辑)
- AI 推断出的新类型 → **直接写入 taxonomy**(与默认枚举去重合并),无需审核
- `pending_additions` 仅保留兼容旧数据;新流程不再写入 pending

版本化设计(taxonomy_version):
- 每次 taxonomy 变更(管理员编辑或 AI 扩展),version 自增
- ViralModelMatrix 产出时记录 taxonomy_version,便于旧数据追溯

权威文档: docs/canvas_restructure_spec.md 第 5.2 章 + Q2 决策
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from loguru import logger

from ..domain.viral_model import ELEMENT_ORDER, ElementCode


# ----------------------------------------------------------------------
# 初始 taxonomy(从"抗老精华"模板归纳;其他品类可在运行时通过 AI 扩展)
# ----------------------------------------------------------------------

DEFAULT_VIRAL_TAXONOMY: Dict[str, List[str]] = {
    ElementCode.A_COVER.value: [
        "纯产品图",
        "前后对比",
        "好状态颜值照",
        "皮肤问题展示",
        "达人手持产品",
        "场景摆拍",
        "使用过程拼图",
        "达人与产品合照",
        "其他（制造真实性）",
    ],
    ElementCode.B_COVER_TEXT.value: [
        "干货/经验分享",
        "痛点",
        "效果",
        "吸睛词",
        "猎奇字",
        "数字",
        "人群身份",
        "背书",
        "其他",
    ],
    ElementCode.C_TITLE.value: [
        "干货/经验分享",
        "笔记主题",
        "猎奇吸睛字",
        "效果承诺",
        "痛点+解决方案",
        "数字钩子",
        "科普知识",
    ],
    ElementCode.D_OPENING.value: [
        "干货切入",
        "痛点切入",
        "痒点切入",
        "好状态(痒点切入)",
        "自身切入(年龄/经历)",
        "效果切入",
        "场景切入",
    ],
    ElementCode.E_PRODUCT_INTRO.value: [
        "直接带出",
        "融入到干货/经验分享中",
        "剧情中自然直入",
        "自用推荐",
        "亲测好用狂推",
        "对比引出",
        "痛点过渡",
        "干货分享引出",
    ],
    ElementCode.F_PRODUCT_PLACEMENT.value: [
        "融合自己使用方法/感受讲卖点",
        "直接讲卖点",
        "产品用法/使用年龄",
        "效果+质地",
        "剧情中展示",
    ],
}


# ----------------------------------------------------------------------
# Loader(对齐 SystemSettingsStore 风格)
# ----------------------------------------------------------------------


class ViralTaxonomyLoader:
    """爆文 6 要素 taxonomy 加载器。

    与 `SystemSettingsStore` / `FocusKeywordsStore` 同风格:
    - 默认值在代码里(DEFAULT_VIRAL_TAXONOMY)
    - 运行时覆盖存 JSON 文件,asyncio.Lock 保护并发写
    - 容错: 文件缺失/损坏时回退默认值
    """

    def __init__(
        self,
        store_file: str = "datas/config/viral_taxonomy.json",
    ) -> None:
        self.path = Path(store_file)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    async def get(self) -> Dict[str, Any]:
        """读取当前 taxonomy(默认值 + 运行时覆盖 + 历史 pending)。

        返回结构:
            {
                "version": 3,
                "updated_at": "2026-04-20T...",
                "taxonomy": {"A_cover": [...], "B_cover_text": [...], ...},
                "pending_additions": {...}  # 旧版遗留;新推断类型已直接进 taxonomy
            }
        """
        async with self._lock:
            raw = self._load_raw()
        merged = self._merge_with_defaults(raw)
        return merged

    async def get_element_types(self, code: ElementCode) -> List[str]:
        """便捷: 获取某单一要素的所有合法分类(含 taxonomy 与遗留 pending)。"""
        data = await self.get()
        tax = data.get("taxonomy", {})
        pending = data.get("pending_additions", {})
        base = list(tax.get(code.value, []))
        extra = list(pending.get(code.value, []))
        return base + extra

    async def propose_new_type(self, code: ElementCode, new_type: str) -> bool:
        """AI 推断的新类型:直接并入持久化 taxonomy(与默认枚举去重合并),立即可用。

        若该要素尚无运行时列表,则写入「默认 ∪ 历史 pending ∪ 新类型」;
        若已有运行时列表(含管理员整表替换),则仅在列表上追加新类型。

        返回:
            True 表示成功写入; False 表示已存在(dedupe) 或类型名非法
        """
        new_type = (new_type or "").strip()
        if not new_type or len(new_type) > 64:
            return False
        async with self._lock:
            raw = self._load_raw()
            taxonomy = raw.setdefault("taxonomy", {})
            pending = raw.setdefault("pending_additions", {})
            default_list = list(DEFAULT_VIRAL_TAXONOMY.get(code.value, []))
            runtime_list = list(taxonomy.get(code.value, []))
            pending_list = list(pending.get(code.value, []))
            existing: Set[str] = (
                set(default_list) | set(runtime_list) | set(pending_list)
            )
            if new_type in existing:
                return False

            if not runtime_list:
                merged = list(dict.fromkeys(default_list + pending_list + [new_type]))
            else:
                merged = list(dict.fromkeys(runtime_list + [new_type]))
            taxonomy[code.value] = merged

            if pending_list and code.value in pending:
                pending[code.value] = [x for x in pending[code.value] if x != new_type]
                if not pending[code.value]:
                    del pending[code.value]

            raw["version"] = int(raw.get("version", 1)) + 1
            raw["updated_at"] = self._now_iso()
            self._save_raw(raw)
        logger.info(
            f"[ViralTaxonomy] AI 推断新类型已直接使用 {code.value}='{new_type}'"
        )
        return True

    async def approve_pending(self, code: ElementCode, type_name: str) -> bool:
        """兼容旧版:将仍留在 pending_additions 中的类型合并到主 taxonomy。"""
        async with self._lock:
            raw = self._load_raw()
            pending = raw.setdefault("pending_additions", {})
            bucket = pending.get(code.value, [])
            if type_name not in bucket:
                return False
            bucket.remove(type_name)
            taxonomy = raw.setdefault("taxonomy", {})
            taxonomy.setdefault(code.value, []).append(type_name)
            raw["version"] = int(raw.get("version", 1)) + 1
            raw["updated_at"] = self._now_iso()
            self._save_raw(raw)
        return True

    async def replace_taxonomy(self, new_taxonomy: Dict[str, List[str]]) -> Dict[str, Any]:
        """管理员整体替换 taxonomy(设置页编辑后保存)。"""
        async with self._lock:
            raw = {
                "version": int(self._load_raw().get("version", 1)) + 1,
                "updated_at": self._now_iso(),
                "taxonomy": {k: list(v) for k, v in new_taxonomy.items()},
                "pending_additions": {},  # 整体替换时清空 pending
            }
            self._save_raw(raw)
        return self._merge_with_defaults(raw)

    async def current_version(self) -> int:
        """当前 taxonomy 版本号(供 ViralModelMatrix 记录)。"""
        async with self._lock:
            raw = self._load_raw()
        return int(raw.get("version", 1))

    # ------------------------------------------------------------------
    # private
    # ------------------------------------------------------------------

    def _load_raw(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            text = self.path.read_text(encoding="utf-8")
            if not text.strip():
                return {}
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"[ViralTaxonomyLoader] 读取 {self.path} 失败({exc}),回退默认值"
            )
            return {}

    def _save_raw(self, data: Dict[str, Any]) -> None:
        try:
            self.path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[ViralTaxonomyLoader] 写入 {self.path} 失败: {exc}")
            raise

    def _merge_with_defaults(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """把运行时数据和 DEFAULT 合并。

        - 没 raw 时:完全用 DEFAULT,版本号 1
        - raw 里的 taxonomy 是运行时覆盖的权威,和 DEFAULT 求并集
        """
        if not raw:
            return {
                "version": 1,
                "updated_at": None,
                "taxonomy": {k: list(v) for k, v in DEFAULT_VIRAL_TAXONOMY.items()},
                "pending_additions": {},
            }

        merged_taxonomy: Dict[str, List[str]] = {}
        runtime = raw.get("taxonomy", {}) or {}
        for code in ELEMENT_ORDER:
            key = code.value
            default_list = list(DEFAULT_VIRAL_TAXONOMY.get(key, []))
            runtime_list = list(runtime.get(key, []))
            # 运行时优先(管理员可以删除 default),但如果 runtime_list 是空,用 default
            if runtime_list:
                merged_taxonomy[key] = runtime_list
            else:
                merged_taxonomy[key] = default_list

        return {
            "version": int(raw.get("version", 1)),
            "updated_at": raw.get("updated_at"),
            "taxonomy": merged_taxonomy,
            "pending_additions": dict(raw.get("pending_additions", {}) or {}),
        }

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------------
# 默认单例(供 Agent / API 直接使用)
# ----------------------------------------------------------------------

_default_loader: Optional[ViralTaxonomyLoader] = None


def get_viral_taxonomy_loader() -> ViralTaxonomyLoader:
    """全局单例,大多数场景下用这个。"""
    global _default_loader
    if _default_loader is None:
        _default_loader = ViralTaxonomyLoader()
    return _default_loader


__all__ = [
    "DEFAULT_VIRAL_TAXONOMY",
    "ViralTaxonomyLoader",
    "get_viral_taxonomy_loader",
]
