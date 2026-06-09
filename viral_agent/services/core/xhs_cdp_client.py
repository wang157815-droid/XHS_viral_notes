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
from xhs_utils.xhs_util import xhs_web_origin

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
        try:
            self._page = await self._context.new_page()
        except Exception as exc:
            # Playwright transport 断连（浏览器崩溃）：转换为可识别的异常供上层降级
            from viral_agent.services.core.playwright_detail_fetcher import CDPContextUnavailableError
            raise CDPContextUnavailableError(
                f"CDP context 已断连，无法创建新页面（浏览器可能已崩溃）: {exc}"
            ) from exc

        sort_str      = _SORT_MAP.get(self._sort_type_choice, "general")
        note_type_str = _NOTE_TYPE_MAP.get(self._note_type, "不限")
        time_str      = _NOTE_TIME_MAP.get(self._time_range, "不限")

        async def handle_search_route(route, request):
            """拦截搜索 API：仅修改 POST body，让浏览器自己发请求。

            不使用 route.fetch()，避免产生"代理请求"被服务端检测或触发 socket hang up。
            响应由独立的 response 监听器捕获（见 handle_search_response）。
            """
            if request.method.upper() != "POST":
                await route.continue_()
                return
            try:
                raw = request.post_data or "{}"
                body: Dict[str, Any] = json.loads(raw)

                # 注入我们的过滤参数
                body["sort"] = "general"
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

                # 让浏览器用修改后的 body 发请求，不用 route.fetch
                await route.continue_(post_data=json.dumps(body))
            except Exception as exc:
                logger.debug(f"[CDP search route] 改写 body 异常，直接 continue: {exc}")
                await route.continue_()

        async def handle_search_response(response):
            """监听搜索 API 的原生响应，捕获到队列（避免 route.fetch 的 socket hang up）。"""
            if _SEARCH_API_PATH not in response.url:
                return
            try:
                resp_json = await response.json()
                await self._response_queue.put(resp_json)
            except Exception as exc:
                logger.debug(f"[CDP search response] 解析响应异常: {exc}")

        # 路由拦截只负责改写 body；响应由 on("response") 独立捕获
        await self._page.route(f"**{_SEARCH_API_PATH}**", handle_search_route)
        self._page.on("response", handle_search_response)

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
        from viral_agent.services.core.playwright_detail_fetcher import (
            CDPContextUnavailableError,
            PlaywrightDetailFetcher,
        )
        try:
            fetcher = await PlaywrightDetailFetcher.get_for_identity(self._owner_user_id)
            note = await fetcher.fetch(url)
            if note:
                # __INITIAL_STATE__ 的 note 是 camelCase，与 API note_card 结构不同。
                # 用 _direct_note 标记通知 _get_note_detail_async 跳过 handle_note_info，
                # 直接返回 note dict（与 _html_fallback 路径行为一致）。
                return True, "ok", {"_direct_note": note}
            else:
                return False, "CDP detail fetch returned None", None
        except CDPContextUnavailableError as exc:
            # browser_data 目录不存在（虚拟号登录用户）→ 与 search_note 保持一致的标签，
            # 让上层 viral_collector 检测后禁用 CDP 并降级 HTTP。
            logger.info(f"[CDP] context 不可用（虚拟号/无浏览器配置）: {exc}")
            return False, "CDP context unavailable", None
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
        """获取 LiveCookieProvider 的 browser context（优先）或 PlaywrightDetailFetcher 的 context。

        关键：LiveCookieProvider._registry 的键是 **username**（如 "admin"），
        而 self._owner_user_id 是 RedMuse user_id（如 "u_xxx"）。
        必须先通过 credential_resolver 把 owner_user_id 映射到 username，再查 registry。
        """
        username = await self._resolve_username()

        # 方案 A：复用 LiveCookieProvider 的已登录 context（通过 username 查）
        if username:
            try:
                from viral_agent.services.auth.live_cookie_provider import LiveCookieProvider
                provider = LiveCookieProvider._registry.get(username)
                if provider:
                    # 通过 _ensure_context 取 context：
                    #   1. 会做存活探活（pages 属性）
                    #   2. context 已死则自动重建
                    #   3. 不会裸返回可能已断连的旧对象
                    await provider._ensure_context()
                    if provider._context is not None:
                        logger.debug(f"[CDP] 复用 LiveCookieProvider context (username={username!r})")
                        return provider._context
            except Exception as _lcp_err:
                logger.debug(f"[CDP] LiveCookieProvider context 获取失败: {_lcp_err}")
                pass

        # 方案 B：复用 PlaywrightDetailFetcher 的 context（用 username 或 owner_user_id）
        lookup_key = username or self._owner_user_id
        try:
            from viral_agent.services.core.playwright_detail_fetcher import PlaywrightDetailFetcher
            fetcher = await PlaywrightDetailFetcher.get_for_identity(lookup_key)
            ctx = await fetcher._get_context()
            if ctx is not None:
                return ctx
        except Exception:
            pass

        logger.error(
            f"[CDP] 无法获取 browser context (owner={self._owner_user_id!r}, username={username!r}). "
            "请确保已通过扫码登录建立 browser_data/<username>/ 目录，"
            "并开启 LIVE_COOKIE_ENABLED=true"
        )
        return None

    async def _resolve_username(self) -> str:
        """将 owner_user_id 映射到 browser_data 目录使用的 username。"""
        try:
            from backend.app.services.xhs_auth.credential_resolver import get_credential_resolver
            resolver = get_credential_resolver()
            username, _ = resolver._resolve_username_and_cookies_path(self._owner_user_id)
            if username:
                return username
        except Exception:
            pass
        # fallback：owner_user_id 本身可能就是 username（单用户场景）
        return self._owner_user_id
