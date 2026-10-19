"""
PromptRegistry：极简提示词加载器（阶段4.0）。

设计原则：
- prompt 文件是纯自然语言 Markdown（.md），不用模板引擎
- 业务人员可以直接阅读、修改
- 动态数据（关键词、样本等）由 Agent 代码 f-string 拼接注入
- 本类只负责「读文件 + 缓存」，不做任何渲染

使用：

    from backend.app.application.agents.prompts import prompt_registry
    system_prompt = prompt_registry.load("insight_industry.md")
    user_content = f"关键词：{', '.join(keywords)}\n..."
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from threading import RLock
from typing import Any, Dict

CATALOG_FILE = "_catalog.json"


def prompt_version(content: str) -> str:
    """prompt 版本哈希：sha256(content)[:12]（M2，§A.5）。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]


class PromptText(str):
    """prompt 原文（仍是 str，调用方无感），额外带 ``version`` 属性。"""

    version: str

    def __new__(cls, content: str) -> "PromptText":
        obj = super().__new__(cls, content)
        obj.version = prompt_version(content)
        return obj


class PromptRegistry:
    """按文件名加载 prompt 内容并缓存。"""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._cache: Dict[str, str] = {}
        self._lock = RLock()

    def load(self, name: str) -> PromptText:
        """读取 prompt 文件原文（带缓存）。

        name: 文件名（含扩展名），例如 "insight_industry.md"
        返回值是 str 子类，``.version`` 为 sha256(content)[:12]，记入审计。
        """
        with self._lock:
            if name not in self._cache:
                path = self._root / name
                if not path.exists():
                    raise FileNotFoundError(f"Prompt file not found: {path}")
                self._cache[name] = PromptText(path.read_text(encoding="utf-8"))
            return self._cache[name]

    def version(self, name: str) -> str:
        return self.load(name).version

    def catalog(self) -> Dict[str, Any]:
        """读取 ``_catalog.json`` 登记表（只读；不含 ``_`` 开头的说明键）。"""
        path = self._root / CATALOG_FILE
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if not k.startswith("_")}

    def list_all(self) -> list[str]:
        """列出所有业务 prompt 文件（不含下划线前缀的内部文件）。"""
        return sorted(
            p.name
            for p in self._root.glob("*.md")
            if not p.name.startswith("_") and p.name.lower() != "readme.md"
        )

    def clear_cache(self) -> None:
        """清空缓存（测试或热更新 prompt 时用）。"""
        with self._lock:
            self._cache.clear()

    @property
    def root(self) -> Path:
        return self._root


# 全局单例：prompt 目录就是本模块所在目录
prompt_registry = PromptRegistry(Path(__file__).parent)
