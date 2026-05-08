"""
统一 JSON 对象提取工具（阶段 4.1）。

替换 Agent 里各自实现的 `_try_parse_json`。策略按先严后宽 5 层降级：

1. 去掉 `<think>...</think>` 块（某些推理模型会输出思考过程）
2. 匹配 ```json ... ``` 代码块（最常见的 LLM 输出模式）
3. 裸 ```...``` 代码块（未标注语言）
4. 整段 `json.loads`
5. 贪婪大括号正则（截取第一段到最后一段之间所有内容）
6. strict=False 时再试"窄"正则（对某些夹杂文字输出有效）

所有返回都保证 `Optional[Dict[str, Any]]`（非 dict 结构返回 None）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional


_THINK_BLOCK = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)
_CODE_FENCE_JSON = re.compile(r"```(?:json|JSON)\s*\n?([\s\S]*?)```")
_CODE_FENCE_ANY = re.compile(r"```\s*\n?([\s\S]*?)```")
_GREEDY_OBJ = re.compile(r"\{[\s\S]*\}")
_NARROW_OBJ = re.compile(r"\{[^{}]*\}")


def _try_load(candidate: str) -> Optional[Dict[str, Any]]:
    candidate = candidate.strip()
    if not candidate:
        return None
    try:
        data = json.loads(candidate)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def extract_json_object(text: Any, *, strict: bool = False) -> Optional[Dict[str, Any]]:
    """从 LLM 输出的文本里尽力提取一个 JSON 对象（dict）。

    参数:
        text: 原始文本（None / 非字符串 / 空串都会返回 None）
        strict: True 时只走前 4 层；False 再额外启用窄正则兜底

    返回:
        成功提取的 dict，否则 None。
    """
    if not isinstance(text, str) or not text.strip():
        return None

    cleaned = _THINK_BLOCK.sub("", text).strip()

    match = _CODE_FENCE_JSON.search(cleaned)
    if match:
        result = _try_load(match.group(1))
        if result is not None:
            return result

    match = _CODE_FENCE_ANY.search(cleaned)
    if match:
        result = _try_load(match.group(1))
        if result is not None:
            return result

    result = _try_load(cleaned)
    if result is not None:
        return result

    match = _GREEDY_OBJ.search(cleaned)
    if match:
        result = _try_load(match.group(0))
        if result is not None:
            return result

    if not strict:
        for narrow_match in _NARROW_OBJ.finditer(cleaned):
            result = _try_load(narrow_match.group(0))
            if result is not None:
                return result

    return None


__all__ = ["extract_json_object"]
