"""联网检索（发现热点等场景）。未配置 API Key 时返回空串，由上层降级为纯模型回答。"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Dict, List

from loguru import logger


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return (v or "").strip() or default


async def fetch_web_context(query: str, *, max_results: int = 5) -> str:
    """返回可拼进 prompt 的纯文本摘要；失败或未配置时返回空。"""
    q = (query or "").strip()
    if not q:
        return ""
    key = _env("WEB_SEARCH_API_KEY")
    if not key:
        return ""
    provider = (_env("WEB_SEARCH_PROVIDER", "tavily") or "tavily").lower()
    try:
        if provider == "tavily":
            return await _tavily_search(q, key, max_results=max_results)
    except Exception as exc:  # pragma: no cover - 网络/解析
        logger.warning("Web search failed: {}", exc)
    return ""


async def _tavily_search(query: str, api_key: str, *, max_results: int) -> str:
    import asyncio

    body = json.dumps(
        {
            "api_key": api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": max(1, min(max_results, 10)),
            "include_answer": True,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    def _post() -> Dict[str, Any]:
        req = urllib.request.Request(
            "https://api.tavily.com/search",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode("utf-8"))

    data = await asyncio.to_thread(_post)
    lines: List[str] = []
    ans = data.get("answer")
    if isinstance(ans, str) and ans.strip():
        lines.append(ans.strip())
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        content = str(item.get("content") or item.get("snippet") or "").strip()
        if not content and not title:
            continue
        lines.append(f"- {title} {url}\n  {content[:400]}")
    return "\n".join(lines).strip()
