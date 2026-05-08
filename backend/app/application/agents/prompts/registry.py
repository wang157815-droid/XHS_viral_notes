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

from pathlib import Path
from threading import RLock
from typing import Dict


class PromptRegistry:
    """按文件名加载 prompt 内容并缓存。"""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._cache: Dict[str, str] = {}
        self._lock = RLock()

    def load(self, name: str) -> str:
        """读取 prompt 文件原文（带缓存）。

        name: 文件名（含扩展名），例如 "insight_industry.md"
        """
        with self._lock:
            if name not in self._cache:
                path = self._root / name
                if not path.exists():
                    raise FileNotFoundError(f"Prompt file not found: {path}")
                self._cache[name] = path.read_text(encoding="utf-8")
            return self._cache[name]

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
