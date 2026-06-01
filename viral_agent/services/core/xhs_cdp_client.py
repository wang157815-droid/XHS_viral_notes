"""XhsCdpClient — 基于 Playwright CDP 的 XHS 采集客户端。

替代 XhsPcApis 的 HTTP 裸请求方案：
  - 浏览器指纹（Canvas/WebGL/TLS）由 Chrome 原生提供，WAF 无法区分
  - 请求参数（sort/filter/note_type）在路由拦截层注入，无需点击 UI
  - Cookie 复用 LiveCookieProvider 的持久化 context

搜索分页机制：
  - 第 1 页：导航到搜索页，Chrome 自动发起 search/notes API 请求
  - 第 2+ 页：向下滚动触发加载，捕获新的 API 响应
  - 每个 (query, sort_type, note_type, time_range) 维度维护一个 _SearchSession

CAPTCHA 处理：
  - 检测响应中 success=False 且 code 为 461/471 时抛出 CaptchaError
  - 检测页面重定向到 verify/captcha 页面时抛出 CaptchaError
  - viral_collector 的重试逻辑（指数退避 + cookie 刷新）照常生效

启用条件（.env）：
    CDP_DETAIL_ENABLED=true     — 总开关
    LIVE_COOKIE_ENABLED=true    — 推荐同时开启以复用已登录 context
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from loguru import logger

from apis.xhs_pc_apis import CaptchaError, SoftBlockError
from xhs_utils.common_util import xhs_web_origin

# ── 常量 ─────────────────────────────────────────────────────────────────────
_SEARCH_API_PATH = "/api/sns/web/v1/search/notes"
_FEED_API_PATH   = "/api/sns/web/v1/feed"

# sort_type_choice → XHS API sort 字符串（与 xhs_pc_apis.py 保持一致）
_SORT_MAP = {
    0: "general",
    1: "time_descending",
    2: "popularity_descending",
    3: "comment_descending",
    4: "collect_descending",
}

# note_type → XHS filter tag
_NOTE_TYPE_MAP = {0: "不限", 1: "视频笔记", 2: "普通笔记"}

# note_time → XHS filter tag
_NOTE_TIME_MAP = {0: "不限", 1: "一天内", 2: "一周内", 3: "半年内"}

_PAGE_TIMEOUT_MS  = int(os.environ.get("CDP_PAGE_TIMEOUT_MS",  "20000"))
_SCROLL_WAIT_SEC  = float(os.environ.get("CDP_SCROLL_WAIT_SEC", "2.5"))
_RESPONSE_TIMEOUT = float(os.environ.get("CDP_RESPONSE_TIMEOUT", "12.0"))


# ── 工具：生成 search_id ────────────────────────────────────────────────────
def _gen_search_id() -> str:
    import time, random, hashlib
    ts = str(int(time.time() * 1000))
    rnd = str(random.randint(10000, 99999))
    return hashlib.md5((ts + rnd).encode()).hexdigest()[:32]


# ── 搜索会话（保持页面打开，滚动翻页）────────────────────────────────────────

class _SearchSession:
    """维护一个打开的搜索页面，供多次翻页使用。"""

    def __init__(
        self,
        context,
        keyword: str,
        sort_type_choice: int,
        note_type: int,
        time_range: int,
        web_origin: str,
    ) -> None:
        self._context = context
        self._keyword = keyword
        self._sort_type_choice = sort_type_choice
        self._note_type = note_type
        self._time_range = time_range
        self._web_origin = web_origin
        self._page = None
        self._response_queue: asyncio.Queue = asyncio.Queue()
        self._page_index = 0   # 已滚动加载的次数

    async def init(self) -> None:
        """打开页面，注册路由拦截，导航到搜索 URL。"""
        self._page = await self._context.new_page()

        sort_str      = _SORT_MAP.get(self._sort_type_choice, "general")
        note_type_str = _NOTE_TYPE_MAP.get(self._note_type, "不限")
        time_str      = _NOTE_TIME_MAP.get(self._time_range, "不限")

        async def handle_search_route(route, request):
            """拦截搜索 API：修改过滤参数后发出，捕获响应。"""
            if request.method.upper() != "POST":
                await route.continue_()
                return
            try:
                raw = request.post_data or "{}"
                body: Dict[str, Any] = json.loads(raw)

                # 注入我们的过滤参数
                body["sort"] = "general"   # 固定 sort 字段，通过 filters 控制
                body["note_type"] = 0
                filters = body.get("filters", [])

                def _set_filter(tag_type: str, tag_value: str) -> None:
                    for f in filters:
                        if f.get("type") == tag_type:
                            f["tags"] = [tag_value]
                            return
                    filters.append({"tags": [tag_value], "type": tag_type})

                _set_filter("sort_type",        sort_str)
                _set_filter("filter_note_type", note_type_str)
                _set_filter("filter_note_time", time_str)
                body["filters"] = filters

                # 用修改后的 body 发出请求
                response = await route.fetch(post_data=json.dumps(body))
                try:
                    resp_json = await response.json()
                    await self._response_queue.put(resp_json)
                except Exception:
                    pass
                await route.fulfill(response=response)
            except Exception as exc:
                logger.debug(f"[CDP search route] 处理异常，直接 continue: {exc}")
                await route.continue_()

        # 注册路由拦截（仅搜索接口）
        await self._page.route(f"**{_SEARCH_API_PATH}**", handle_search_route)

        search_url = (
            f"{self._web_origin}/search_result"
            f"?keyword={quote(self._keyword)}"
            f"&source=web_search_result_notes&type=51"
        )
        try:
            await self._page.goto(
                search_url,
                wait_until="domcontentloaded",
                timeout=_PAGE_TIMEOUT_MS,
            )
        except Exception as exc:
            logger.debug(f"[CDP] page.goto 超时/异常（继续等待 API 响应）: {exc}")

    async def get_next_page(self) -> Tuple[bool, str, Optional[Dict]]:
        """获取下一页搜索结果（首页直接等，后续页先滚动再等）。"""
        if self._page_index > 0:
            # 向下滚动触发 XHR
            try:
                await self._page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight)"
                )
            except Exception:
                pass
            await asyncio.sleep(_SCROLL_WAIT_SEC)

        self._page_index += 1

        # 检查是否触发了 CAPTCHA 页面
        try:
            current_url = self._page.url
            if "verify" in current_url or "captcha" in current_url.lower():
                logger.warning(f"[CDP search] 页面重定向到验证: {current_url}")
                raise CaptchaError(f"CDP: 页面跳转到验证页 {current_url}")
        except CaptchaError:
            raise
        except Exception:
            pass

        # 等待 API 响应
        try:
            resp_json = await asyncio.wait_for(
                self._response_queue.get(),
                timeout=_RESPONSE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"[CDP search] 第 {self._page_index} 页等待响应超时 "
                f"(keyword={self._keyword!r})"
            )
            return False, "response_timeout", None

        # 检查响应内容
        if not resp_json:
            return False, "empty_response", None

        code = resp_json.get("code", 0)
        if code in (461, 471, -10000):
            raise CaptchaError(f"CDP: search API code={code}")

        success = resp_json.get("success", False)
        msg = resp_json.get("msg", "")
        data = resp_json.get("data", {})

        if not success:
            # soft block: success=False 但不是 CAPTCHA
            items = data.get("items", [])
            if not items:
                raise SoftBlockError(f"CDP: search success=False, msg={msg!r}")

        return success, msg, resp_json

    async def close(self) -> None:
        if self._page:
            try:
                await self._page.close()
            except Exception:
                pass
            self._page = None


# ── 主客户端 ─────────────────────────────────────────────────────────────────

class XhsCdpClient:
    """Playwright-based XHS client，与 XhsPcApis.search_note / get_note_info 接口兼容。

    usage（在 viral_collector 中）：
        cdp = XhsCdpClient(owner_user_id="user_xxx")
        success, msg, res = await cdp.search_note(
            query="燕麦米",
            page=1,
            sort_type_choice=2,
            note_type=0,
            note_time=0,
        )
    """

    def __init__(self, owner_user_id: str) -> None:
        self._owner_user_id = owner_user_id
        # 每个维度维护一个搜索会话：key = (query, sort, note_type, time_range)
        self._sessions: Dict[str, _SearchSession] = {}

    # ── 搜索 ─────────────────────────────────────────────────────────────────

    async def search_note(
        self,
        query: str,
        page: int = 1,
        sort_type_choice: int = 0,
        note_type: int = 0,
        note_time: int = 0,
        **_ignored,
    ) -> Tuple[bool, str, Optional[Dict]]:
        """CDP 搜索笔记，返回 (success, msg, res_json) 与 XhsPcApis.search_note 格式兼容。"""
        session_key = f"{query}|{sort_type_choice}|{note_type}|{note_time}"

        # page=1 时始终重建会话（维度切换或重试）
        if page == 1:
            await self._close_session(session_key)

        if session_key not in self._sessions:
            context = await self._get_context()
            if context is None:
                return False, "CDP context unavailable", None
            session = _SearchSession(
                context=context,
                keyword=query,
                sort_type_choice=sort_type_choice,
                note_type=note_type,
                time_range=note_time,
                web_origin=xhs_web_origin(),
            )
            await session.init()
            self._sessions[session_key] = session
            logger.info(
                f"[CDP] 新搜索会话 keyword={query!r} sort={sort_type_choice} "
                f"note_type={note_type} time={note_time}"
            )

        session = self._sessions[session_key]
        return await session.get_next_page()

    # ── 详情 ─────────────────────────────────────────────────────────────────

    async def get_note_info(
        self,
        url: str,
        cookies_str: str = "",
        **_ignored,
    ) -> Tuple[bool, str, Optional[Dict]]:
        """CDP 获取笔记详情，返回 (success, msg, res_json)。

        直接通过 PlaywrightDetailFetcher 导航笔记页提取数据，
        返回的 res_json 包装成与 XhsPcApis.get_note_info 兼容的格式。
        """
        from viral_agent.services.core.playwright_detail_fetcher import PlaywrightDetailFetcher
        try:
            fetcher = await PlaywrightDetailFetcher.get_for_identity(self._owner_user_id)
            note = await fetcher.fetch(url)
            if note:
                note_id = url.split("?")[0].rstrip("/").split("/")[-1]
                # 包装成 feed API 的响应格式，供 _get_note_detail_async 解析
                res_json = {
                    "success": True,
                    "msg": "ok",
                    "data": {
                        "items": [
                            {"id": note_id, "note_card": note}
                        ]
                    }
                }
                return True, "ok", res_json
            else:
                return False, "CDP detail fetch returned None", None
        except CaptchaError:
            raise
        except Exception as exc:
            logger.warning(f"[CDP] get_note_info 异常: {exc}, url={url}")
            return False, str(exc), None

    # ── 清理 ─────────────────────────────────────────────────────────────────

    async def close_session(self, query: str, sort_type_choice: int, note_type: int, time_range: int) -> None:
        """关闭指定维度的搜索会话（维度结束时调用）。"""
        key = f"{query}|{sort_type_choice}|{note_type}|{time_range}"
        await self._close_session(key)

    async def close_all(self) -> None:
        """关闭所有活跃搜索会话。"""
        keys = list(self._sessions.keys())
        for key in keys:
            await self._close_session(key)

    async def _close_session(self, key: str) -> None:
        session = self._sessions.pop(key, None)
        if session:
            await session.close()

    # ── context ──────────────────────────────────────────────────────────────

    async def _get_context(self):
        """获取 LiveCookieProvider 的 browser context（优先）或 PlaywrightDetailFetcher 的 context。"""
        # 方案 A：复用 LiveCookieProvider 的已登录 context
        try:
            from viral_agent.services.auth.live_cookie_provider import LiveCookieProvider
            provider = LiveCookieProvider._registry.get(self._owner_user_id)
            if provider and provider._context is not None:
                return provider._context
        except Exception:
            pass

        # 方案 B：复用 PlaywrightDetailFetcher 的 context
        try:
            from viral_agent.services.core.playwright_detail_fetcher import PlaywrightDetailFetcher
            fetcher = await PlaywrightDetailFetcher.get_for_identity(self._owner_user_id)
            ctx = await fetcher._get_context()
            if ctx is not None:
                return ctx
        except Exception:
            pass

        logger.error(
            f"[CDP] 无法获取 browser context for user={self._owner_user_id!r}. "
            "请确保已通过扫码登录建立 browser_data/<username>/ 目录，"
            "并开启 LIVE_COOKIE_ENABLED=true"
        )
        return None
