"""
小红书扫码登录服务（优化版）

基于 MediaCrawler 替换方案（阶段1）优化：
1. 浏览器窗口 1920x1080，二维码更易扫描
2. 浏览器持久化目录，复用登录态减少重复登录
3. stealth.min.js 注入，降低自动化检测风险
4. 三重登录状态检测（UI 节点 + Cookie 变化 + selfinfo 接口）
5. 服务器模式改为自动检测（不再强制 return True）
6. 登录成功后自动写回用户 Cookie
"""
import asyncio
import base64
import hashlib
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

from loguru import logger

from .qrcode_session import QRCodeSession, QRLoginStatus

LOGIN_TIMEOUT_SECONDS = 300
# 默认打开国内站；海外 RedNote 用户可设环境变量 XHS_LOGIN_START_URL=https://www.rednote.com/explore
DEFAULT_XHS_LOGIN_URL = "https://www.xiaohongshu.com/explore"

# 截图裁剪：从 QR 元素向上找到尺寸落在登录弹窗区间的祖先作为截图区域。
# 太小（< MIN）= QR 节点本身，缺乏文案；太大（> MAX）= modal backdrop 或整页容器。
_LOGIN_PANEL_QR_SELECTORS = (
    'canvas[class*="qrcode"]',
    'img[class*="qrcode"]',
    '[class*="qr-code"]',
    '[class*="QRCode"]',
)
_LOGIN_PANEL_MIN_WIDTH = 320
_LOGIN_PANEL_MAX_WIDTH = 720
_LOGIN_PANEL_MIN_HEIGHT = 320
_LOGIN_PANEL_MAX_HEIGHT = 800
_LOGIN_PANEL_PADDING_PX = 16


def _max_concurrent_sessions() -> int:
    try:
        return max(1, int(os.environ.get("XHS_LOGIN_MAX_CONCURRENT_SESSIONS", "5")))
    except ValueError:
        return 5


def _resolve_login_start_url() -> str:
    raw = (os.environ.get("XHS_LOGIN_START_URL") or "").strip()
    return raw if raw else DEFAULT_XHS_LOGIN_URL


def _login_success_close_delay_sec() -> int:
    try:
        return max(0, min(60, int(os.environ.get("XHS_LOGIN_CLOSE_DELAY_SEC", "5"))))
    except ValueError:
        return 5


def _session_result_retention_sec() -> int:
    try:
        return max(10, min(600, int(os.environ.get("XHS_LOGIN_RESULT_TTL_SEC", "120"))))
    except ValueError:
        return 120


def _login_action_timeout_ms() -> int:
    try:
        return max(500, min(10000, int(os.environ.get("XHS_LOGIN_ACTION_TIMEOUT_MS", "2000"))))
    except ValueError:
        return 2000


def _login_navigation_timeout_ms() -> int:
    try:
        return max(5000, min(30000, int(os.environ.get("XHS_LOGIN_NAV_TIMEOUT_MS", "15000"))))
    except ValueError:
        return 15000


def _should_reuse_existing_login(
    expected_user_id: Optional[str],
    client_device_id: Optional[str],
) -> bool:
    raw = (os.environ.get("XHS_LOGIN_REUSE_EXISTING") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    # 默认只允许“当前浏览器曾经登录过同一 XHS 身份”时复用服务器浏览器态。
    # 陌生设备没有 expected_user_id/client_device_id，必须重新扫码，避免共享 xhs_admin 旧 Cookie 误登录。
    return bool((expected_user_id or "").strip() and (client_device_id or "").strip())

BROWSER_VIEWPORT = {"width": 1920, "height": 1080}
# 与 xhs_utils.xhs_util.BROWSER_UA 严格对齐（Chrome 137）,
# 避免 Playwright 浏览器生成的 a1 指纹和后续请求头 UA 版本不一致被反爬识别。
# 如需升级,两处一起改。
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36"
)

STEALTH_JS_PATH = Path(__file__).resolve().parents[3] / "libs" / "stealth.min.js"
USER_DATA_BASE_DIR = Path(__file__).resolve().parents[3] / "browser_data"

BROWSER_ARGS = [
    '--disable-blink-features=AutomationControlled',
    '--no-sandbox',
    '--disable-setuid-sandbox',
    '--disable-infobars',
    '--disable-dev-shm-usage',
    '--lang=zh-CN',
]


class QRCodeLoginService:
    """
    小红书扫码登录服务（优化版）

    相比旧版的核心改进：
    - 浏览器持久化：登录态保存到 browser_data/ 目录，下次启动可复用
    - 更大 viewport：1920x1080，二维码更清晰，交互更稳定
    - stealth 注入：降低平台对自动化浏览器的检测风险
    - 三重登录检测：UI 节点 → Cookie 变化 → selfinfo API
    """

    _instance: Optional['QRCodeLoginService'] = None

    def __init__(self):
        self._sessions: Dict[str, QRCodeSession] = {}
        self._browsers: Dict[str, 'Browser'] = {}
        self._contexts: Dict[str, 'BrowserContext'] = {}
        self._pages: Dict[str, 'Page'] = {}
        self._playwright = None
        self._lock = asyncio.Lock()
        self._playwright_available: Optional[bool] = None
        self._warmed_browser: Optional['Browser'] = None
        self._warmup_lock = asyncio.Lock()
        self._is_warming_up = False

    @classmethod
    def get_instance(cls) -> 'QRCodeLoginService':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def _has_display() -> bool:
        import sys
        if sys.platform in ('win32', 'darwin'):
            return True
        if os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'):
            return True
        return False

    @staticmethod
    def _is_server_mode() -> bool:
        return not QRCodeLoginService._has_display()

    async def check_playwright_available(self, force_recheck: bool = False) -> bool:
        if self._playwright_available is not None and not force_recheck:
            return self._playwright_available

        try:
            from playwright.async_api import async_playwright
            pw = await async_playwright().start()
            try:
                browser = await pw.chromium.launch(headless=True)
                await browser.close()
                self._playwright_available = True
                mode = "服务器模式" if self._is_server_mode() else "桌面模式"
                logger.info(f"Playwright 已就绪（{mode}）")
            except Exception as e:
                self._playwright_available = False
                logger.warning(f"Chromium 未安装: {e}")
            finally:
                await pw.stop()
        except ImportError:
            self._playwright_available = False
            logger.warning("Playwright 未安装")

        return self._playwright_available

    async def warmup(self) -> bool:
        async with self._warmup_lock:
            if self._warmed_browser is not None:
                return True
            if self._is_warming_up:
                return False
            self._is_warming_up = True

        try:
            if not await self.check_playwright_available():
                return False

            from playwright.async_api import async_playwright

            logger.info("🔥 开始预热浏览器...")
            start_time = datetime.now()

            if self._playwright is None:
                self._playwright = await async_playwright().start()

            is_server = self._is_server_mode()
            self._warmed_browser = await self._playwright.chromium.launch(
                headless=is_server,
                args=BROWSER_ARGS + ([] if is_server else ['--start-maximized']),
            )

            elapsed = (datetime.now() - start_time).total_seconds()
            logger.success(f"✅ 浏览器预热完成，耗时 {elapsed:.1f} 秒")
            return True

        except Exception as e:
            logger.error(f"浏览器预热失败: {e}")
            return False
        finally:
            self._is_warming_up = False

    async def _get_or_create_browser(self) -> 'Browser':
        async with self._warmup_lock:
            if self._warmed_browser is not None:
                browser = self._warmed_browser
                self._warmed_browser = None
                logger.info("♻️ 复用预热的浏览器")
                asyncio.create_task(self.warmup())
                return browser

        logger.info("⏳ 创建新浏览器（未预热）")
        is_server = self._is_server_mode()
        return await self._playwright.chromium.launch(
            headless=is_server,
            args=BROWSER_ARGS + ([] if is_server else ['--start-maximized']),
        )

    @staticmethod
    def _profile_suffix(client_device_id: Optional[str]) -> str:
        raw = (client_device_id or "").strip()
        if not raw:
            return ""
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _get_user_data_dir(
        self,
        username: str,
        client_device_id: Optional[str] = None,
    ) -> str:
        suffix = self._profile_suffix(client_device_id)
        dirname = f"xhs_{username}_{suffix}" if suffix else f"xhs_{username}"
        user_dir = USER_DATA_BASE_DIR / dirname
        user_dir.mkdir(parents=True, exist_ok=True)
        return str(user_dir)

    async def create_session(
        self,
        username: str,
        *,
        expected_user_id: Optional[str] = None,
        client_device_id: Optional[str] = None,
        creator_redmuse_user_id: Optional[str] = None,
    ) -> QRCodeSession:
        if not await self.check_playwright_available():
            raise RuntimeError("扫码功能未启用，请安装: pip install playwright && playwright install chromium")

        async with self._lock:
            active_sessions = [s for s in self._sessions.values() if s.is_active]
            max_sessions = _max_concurrent_sessions()
            if len(active_sessions) >= max_sessions:
                raise RuntimeError(f"当前扫码会话数已达上限 ({max_sessions})")

            clean_client_device_id = (client_device_id or "").strip() or None
            await self._cleanup_user_sessions(username, clean_client_device_id)

            session = QRCodeSession(
                username=username,
                expected_user_id=(expected_user_id or "").strip() or None,
                client_device_id=clean_client_device_id,
                creator_redmuse_user_id=(creator_redmuse_user_id or "").strip() or None,
            )
            self._sessions[session.session_id] = session

            asyncio.create_task(self._open_browser_window(session))

            logger.info(
                f"创建扫码会话: {session.session_id}, "
                f"expected_user_id={session.expected_user_id or '-'}, "
                f"profile={self._profile_suffix(session.client_device_id) or 'default'}"
            )
            return session

    def _is_session_current(self, session: QRCodeSession) -> bool:
        return (
            self._sessions.get(session.session_id) is session
            and session.status != QRLoginStatus.CANCELLED
        )

    async def _open_browser_window(self, session: QRCodeSession):
        try:
            from playwright.async_api import async_playwright

            start_time = datetime.now()

            if self._playwright is None:
                self._playwright = await async_playwright().start()

            is_server = self._is_server_mode()

            if is_server:
                logger.info(f"会话 {session.session_id}: 服务器模式，使用无头浏览器 + 截图")
            else:
                logger.info(f"会话 {session.session_id}: 桌面模式，弹出浏览器窗口")

            if not self._is_session_current(session):
                logger.info(f"会话 {session.session_id}: 会话已取消，停止打开浏览器")
                return

            chromium = self._playwright.chromium
            reuse_existing_login = _should_reuse_existing_login(
                session.expected_user_id,
                session.client_device_id,
            )
            user_data_dir = self._get_user_data_dir(
                session.username,
                session.client_device_id,
            )
            if not reuse_existing_login:
                await self._reset_user_data_dir(user_data_dir, session.session_id)

            context = await chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=is_server,
                viewport=BROWSER_VIEWPORT,
                user_agent=BROWSER_USER_AGENT,
                locale='zh-CN',
                accept_downloads=True,
                args=BROWSER_ARGS + ([] if is_server else ['--start-maximized']),
            )
            if not self._is_session_current(session):
                await context.close()
                logger.info(f"会话 {session.session_id}: 会话已取消，关闭刚创建的浏览器上下文")
                return
            context.set_default_timeout(_login_action_timeout_ms())
            context.set_default_navigation_timeout(_login_navigation_timeout_ms())
            self._contexts[session.session_id] = context

            if STEALTH_JS_PATH.exists():
                await context.add_init_script(path=str(STEALTH_JS_PATH))
                logger.debug(f"会话 {session.session_id}: 已注入 stealth.min.js")
            else:
                logger.warning(f"stealth.min.js 不存在: {STEALTH_JS_PATH}")

            page = context.pages[0] if context.pages else await context.new_page()
            self._pages[session.session_id] = page

            browser_elapsed = (datetime.now() - start_time).total_seconds()
            logger.debug(f"会话 {session.session_id}: 浏览器启动耗时 {browser_elapsed:.1f} 秒")

            start_url = _resolve_login_start_url()
            logger.info(f"会话 {session.session_id}: 打开登录页 {start_url}")
            nav_started = datetime.now()
            try:
                await page.goto(
                    start_url,
                    wait_until='domcontentloaded',
                    timeout=_login_navigation_timeout_ms(),
                )
            except Exception as e:
                logger.warning(f"会话 {session.session_id}: 打开登录页等待超时/异常，继续尝试点击登录入口: {e}")
            logger.info(
                f"会话 {session.session_id}: 登录页导航耗时 "
                f"{(datetime.now() - nav_started).total_seconds():.1f} 秒"
            )
            if not self._is_session_current(session):
                await self._cleanup_session(session.session_id, remove_from_sessions=False)
                logger.info(f"会话 {session.session_id}: 会话已取消，停止登录页处理")
                return

            already_logged_in = (
                await self._check_already_logged_in(page, context)
                if reuse_existing_login
                else False
            )
            if already_logged_in:
                logger.info(f"会话 {session.session_id}: 检测到已有登录态，尝试直接复用")
                cookies = await context.cookies()
                cookies_str = '; '.join([f"{c['name']}={c['value']}" for c in cookies])
                if await self._verify_cookie_valid(
                    cookies_str,
                    expected_user_id=session.expected_user_id,
                ):
                    session.status = QRLoginStatus.CONFIRMED
                    session.cookies_str = cookies_str
                    logger.success(
                        f"会话 {session.session_id}: 复用已有登录态成功，等待身份解析后保存 Cookie"
                    )
                    session.status = QRLoginStatus.SUCCESS
                    await self._cleanup_session(session.session_id, remove_from_sessions=False)
                    await asyncio.sleep(_login_success_close_delay_sec())
                    logger.info(
                        f"会话 {session.session_id}: 登录结果保留 {_session_result_retention_sec()} 秒，等待前端领取"
                    )
                    await asyncio.sleep(_session_result_retention_sec())
                    if session.session_id in self._sessions:
                        del self._sessions[session.session_id]
                    return
                logger.warning(
                    f"会话 {session.session_id}: 已有登录态身份不匹配或无效，清理后重新扫码"
                )
                await self._clear_login_state(page, context)
                try:
                    await page.goto(
                        start_url,
                        wait_until='domcontentloaded',
                        timeout=_login_navigation_timeout_ms(),
                    )
                except Exception as e:
                    logger.warning(f"会话 {session.session_id}: 重新打开登录页等待超时/异常，继续尝试: {e}")

            try:
                await page.wait_for_selector(
                    'div.login-btn, span.login-btn, div[class*="login-btn"]',
                    timeout=5000
                )
            except Exception:
                logger.debug(f"会话 {session.session_id}: 等待登录按钮超时，继续尝试")

            click_started = datetime.now()
            await self._click_login_button(page, session)
            logger.info(
                f"会话 {session.session_id}: 登录入口处理耗时 "
                f"{(datetime.now() - click_started).total_seconds():.1f} 秒"
            )
            if not self._is_session_current(session):
                await self._cleanup_session(session.session_id, remove_from_sessions=False)
                logger.info(f"会话 {session.session_id}: 会话已取消，停止生成截图")
                return

            session.status = QRLoginStatus.WAITING_SCAN
            session.expires_at = datetime.now() + timedelta(seconds=LOGIN_TIMEOUT_SECONDS)

            if is_server:
                await self._publish_screenshot(page, session)
                logger.success(f"会话 {session.session_id}: 页面截图已生成（{BROWSER_VIEWPORT['width']}x{BROWSER_VIEWPORT['height']}），等待扫码")
                asyncio.create_task(self._update_screenshot(session))
            else:
                session.qrcode_base64 = None
                logger.success(f"会话 {session.session_id}: 浏览器窗口已打开，等待登录")

            asyncio.create_task(self._monitor_login_status(session))

        except Exception as e:
            logger.error(f"会话 {session.session_id}: 打开浏览器失败 - {e}")
            session.status = QRLoginStatus.ERROR
            session.error_message = f"打开浏览器失败: {str(e)}"
            await self._cleanup_session(session.session_id)

    async def _publish_screenshot(self, page, session: QRCodeSession) -> bool:
        try:
            clip = await self._resolve_login_panel_clip(page)
            if clip is not None:
                # 已命中登录弹窗：jpeg 质量提到 88，二维码细节更清晰；裁剪后总字节数远小于全页。
                screenshot = await page.screenshot(
                    type='jpeg', quality=88, full_page=False, clip=clip,
                )
            else:
                # 未找到弹窗（页面初始化、滑块验证等场景）：保持原全页 fallback。
                screenshot = await page.screenshot(
                    type='jpeg', quality=80, full_page=False,
                )
            session.qrcode_base64 = base64.b64encode(screenshot).decode('utf-8')
            setattr(
                session,
                "screenshot_version",
                int(getattr(session, "screenshot_version", 0) or 0) + 1,
            )
            return True
        except Exception as e:
            logger.warning(f"会话 {session.session_id}: 截图生成失败 - {e}")
            return False

    async def _resolve_login_panel_clip(self, page) -> Optional[Dict[str, float]]:
        """
        定位二维码所在的登录弹窗，返回 page.screenshot(clip=...) 用的矩形。

        策略：找到 QR 元素后向上遍历祖先，第一个尺寸落在登录弹窗合理区间的就是。
        命中失败返回 None，由调用方回退到全页截图，保证不破坏现有可用性。
        """
        viewport = page.viewport_size or {"width": 1920, "height": 1080}
        for selector in _LOGIN_PANEL_QR_SELECTORS:
            try:
                locator = page.locator(selector).first
                if await locator.count() == 0:
                    continue
                box = await locator.evaluate(
                    """
                    (el, opts) => {
                      const { minW, maxW, minH, maxH } = opts;
                      let cur = el;
                      while (cur && cur !== document.body) {
                        const rect = cur.getBoundingClientRect();
                        if (
                          rect.width >= minW && rect.width <= maxW &&
                          rect.height >= minH && rect.height <= maxH
                        ) {
                          return {
                            x: rect.x, y: rect.y,
                            width: rect.width, height: rect.height,
                          };
                        }
                        cur = cur.parentElement;
                      }
                      return null;
                    }
                    """,
                    {
                        "minW": _LOGIN_PANEL_MIN_WIDTH,
                        "maxW": _LOGIN_PANEL_MAX_WIDTH,
                        "minH": _LOGIN_PANEL_MIN_HEIGHT,
                        "maxH": _LOGIN_PANEL_MAX_HEIGHT,
                    },
                )
                if not box:
                    continue
                pad = _LOGIN_PANEL_PADDING_PX
                clip_x = max(box["x"] - pad, 0.0)
                clip_y = max(box["y"] - pad, 0.0)
                # 边界保护，避免 clip 越界 viewport 引起 Playwright 报错。
                max_w = max(viewport.get("width", 1920) - clip_x, 1.0)
                max_h = max(viewport.get("height", 1080) - clip_y, 1.0)
                clip_w = min(box["width"] + pad * 2, max_w)
                clip_h = min(box["height"] + pad * 2, max_h)
                if clip_w <= 1 or clip_h <= 1:
                    continue
                return {
                    "x": clip_x,
                    "y": clip_y,
                    "width": clip_w,
                    "height": clip_h,
                }
            except Exception:
                continue
        return None

    async def _reset_user_data_dir(self, user_data_dir: str, session_id: str):
        def _remove_dir():
            path = Path(user_data_dir)
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
            path.mkdir(parents=True, exist_ok=True)

        await asyncio.to_thread(_remove_dir)
        logger.info(f"会话 {session_id}: 已清理旧浏览器登录态，准备生成新的扫码页")

    async def _clear_login_state(self, page, context):
        """清理持久化浏览器里的旧 Cookie/Storage，避免服务器复用失效登录态。"""
        try:
            await context.clear_cookies()
        except Exception as e:
            logger.debug(f"清理 Cookie 失败: {e}")
        try:
            await page.evaluate(
                "() => { window.localStorage.clear(); window.sessionStorage.clear(); }"
            )
        except Exception as e:
            logger.debug(f"清理浏览器 Storage 失败: {e}")

    async def _page_looks_logged_in(self, page) -> bool:
        """导航栏是否已出现「我的」入口。兼容国内「我」与国际站 RedNote 英文文案等。"""
        try:
            sel_zh = (
                "xpath=//a[contains(@href, '/user/profile/')]"
                "//span[normalize-space()='我']"
            )
            if await page.is_visible(sel_zh, timeout=400):
                return True
        except Exception:
            pass
        # RedNote 底栏常见：图标 + 文案「Me」，href 未必含 /user/profile/
        try:
            sel_me_any_a = (
                "xpath=//a[.//*[normalize-space()='Me'] or normalize-space()='Me']"
            )
            if await page.is_visible(sel_me_any_a, timeout=400):
                return True
        except Exception:
            pass
        try:
            for role in ("tab", "link"):
                loc = page.get_by_role(role, name="Me", exact=True)
                if await loc.count() > 0:
                    first = loc.first
                    if await first.is_visible(timeout=400):
                        return True
        except Exception:
            pass
        for label in ("Me", "me", "MY", "Profile"):
            try:
                sel = (
                    f"xpath=//a[contains(@href, '/user/profile/')]"
                    f"//*[normalize-space()='{label}']"
                )
                if await page.is_visible(sel, timeout=400):
                    return True
            except Exception:
                continue
        try:
            loc = page.locator(
                'header a[href*="/user/profile/"], '
                'nav a[href*="/user/profile/"], '
                '[class*="nav"] a[href*="/user/profile/"], '
                '[class*="header"] a[href*="/user/profile/"]'
            ).first
            if await loc.count() > 0 and await loc.is_visible(timeout=600):
                return True
        except Exception:
            pass
        try:
            any_p = page.locator('a[href*="/user/profile/"]').first
            if await any_p.count() > 0 and await any_p.is_visible(timeout=600):
                return True
        except Exception:
            pass
        return False

    async def _check_already_logged_in(self, page, context) -> bool:
        try:
            if await self._page_looks_logged_in(page):
                return True
        except Exception:
            pass

        try:
            cookies = await context.cookies()
            cookie_dict = {c['name']: c['value'] for c in cookies}
            web_session = cookie_dict.get('web_session', '')
            a1 = cookie_dict.get('a1', '')
            if web_session and a1:
                return True
        except Exception:
            pass

        return False

    async def _click_login_button(self, page, session: QRCodeSession):
        if await self._has_visible_qrcode(page):
            logger.info(f"会话 {session.session_id}: 页面已显示二维码，跳过点击登录入口")
            return True

        login_selectors = [
            'div.login-btn',
            'span.login-btn',
            'div[class*="login-btn"]',
            'button:has-text("登录")',
        ]
        for selector in login_selectors:
            try:
                btn = await page.wait_for_selector(selector, timeout=1500)
                if btn:
                    await btn.click(timeout=_login_action_timeout_ms())
                    logger.debug(f"会话 {session.session_id}: 点击登录按钮")
                    try:
                        await page.wait_for_selector(
                            '[class*="login-container"], [class*="login-modal"], [class*="qrcode"]',
                            timeout=_login_action_timeout_ms()
                        )
                    except Exception:
                        pass
                    break
            except Exception as e:
                logger.debug(f"会话 {session.session_id}: 登录按钮选择器 {selector} 未命中或不可点击: {e}")
                continue

        qrcode_tab_selectors = [
            'span:has-text("扫码登录")',
            'div:has-text("扫码登录")',
            '[class*="qrcode-tab"]',
            '[class*="scan-tab"]',
        ]
        for selector in qrcode_tab_selectors:
            try:
                tab = await page.wait_for_selector(selector, timeout=1500)
                if tab:
                    await tab.click(timeout=_login_action_timeout_ms())
                    logger.debug(f"会话 {session.session_id}: 切换到二维码登录")
                    try:
                        await page.wait_for_selector(
                            'img[class*="qrcode"], canvas[class*="qrcode"], [class*="qr-code"]',
                            timeout=_login_action_timeout_ms()
                        )
                    except Exception:
                        pass
                    break
            except Exception as e:
                logger.debug(f"会话 {session.session_id}: 二维码登录页签选择器 {selector} 未命中或不可点击: {e}")
                continue

        qrcode_selectors = [
            'img[class*="qrcode"]',
            'canvas[class*="qrcode"]',
            '[class*="qr-code"]',
            '[class*="QRCode"]',
        ]
        for selector in qrcode_selectors:
            try:
                qr = await page.wait_for_selector(selector, timeout=2000)
                if qr:
                    logger.success(f"会话 {session.session_id}: 二维码已加载")
                    return True
            except Exception:
                continue

        logger.warning(f"会话 {session.session_id}: 未找到二维码元素，截图可能不包含二维码")
        return False

    async def _update_screenshot(self, session: QRCodeSession):
        page = self._pages.get(session.session_id)
        if not page:
            return

        while session.is_active and session.status in (
            QRLoginStatus.WAITING_SCAN,
            QRLoginStatus.NEED_SMS_CODE,
            QRLoginStatus.SCANNED,
            QRLoginStatus.CONFIRMED,
        ):
            try:
                if not page.is_closed():
                    await self._handle_sms_submission_error(page, session)
                    interaction_type = await self._check_page_interaction(page)

                    if interaction_type == 'sms_code':
                        if not session.sms_code_submitted and session.status != QRLoginStatus.NEED_SMS_CODE:
                            session.status = QRLoginStatus.NEED_SMS_CODE
                            logger.info(f"会话 {session.session_id}: 需要输入短信验证码")
                    elif interaction_type == 'scanned':
                        if session.status == QRLoginStatus.WAITING_SCAN:
                            session.status = QRLoginStatus.SCANNED
                            logger.info(f"会话 {session.session_id}: 已扫码，等待手机确认")
                    elif interaction_type == 'slider':
                        session.error_message = '需要滑块验证，请使用手动复制 Cookie 方式'
                        logger.warning(f"会话 {session.session_id}: 需要滑块验证")

                    await self._publish_screenshot(page, session)
            except Exception as e:
                logger.warning(f"会话 {session.session_id}: 截图更新失败 - {e}")
                break

            await asyncio.sleep(2)

    async def _has_visible_qrcode(self, page) -> bool:
        """检测页面上是否存在真实的、待扫描的 QR 码。

        区分两种情形：
        - 初始登录页：QR 码图片仍在，等待扫描 → 返回 True
        - 扫码成功后：容器 div 仍在 DOM（显示头像/成功动画），
          但 QR 码图片本身已被移除或替换 → 返回 False

        对于精确的 img/canvas 元素，直接检测可见性；
        对于容器类选择器，需深入验证容器内确实含有可见的 QR 码内容。
        """
        # 精确元素：img 或 canvas 本身带 qrcode class
        exact_selectors = [
            'img[class*="qrcode"]',
            'canvas[class*="qrcode"]',
            '.qrcode-img',
        ]
        for selector in exact_selectors:
            try:
                element = await page.query_selector(selector)
                if element and await element.is_visible():
                    return True
            except Exception:
                continue

        # 容器选择器：需额外验证容器内是否有真实 QR 码内容
        # 扫码成功后容器仍在，但内部图片/canvas 已被成功态 UI 替换
        container_selectors = [
            '[class*="qr-code"]',
            '[class*="QRCode"]',
        ]
        for selector in container_selectors:
            try:
                element = await page.query_selector(selector)
                if not element or not await element.is_visible():
                    continue
                # 容器内是否有面积 > 40×40 的图片（src 非空）或 canvas
                has_qr_content = await element.evaluate('''el => {
                    const imgs = el.querySelectorAll('img');
                    for (const img of imgs) {
                        if (img.offsetWidth > 40 && img.src && img.src !== 'about:blank') {
                            return true;
                        }
                    }
                    const canvases = el.querySelectorAll('canvas');
                    for (const c of canvases) {
                        if (c.offsetWidth > 40 && c.offsetHeight > 40) return true;
                    }
                    return false;
                }''')
                if has_qr_content:
                    return True
            except Exception:
                continue

        return False

    @staticmethod
    def _sms_challenge_signal_from_snapshot(snapshot, *, qrcode_visible: bool) -> bool:
        if not isinstance(snapshot, dict):
            return False

        text = str(snapshot.get("text") or "")
        controls = " ".join(str(v or "") for v in snapshot.get("controls") or [])
        combined = f"{text} {controls}".lower()
        inputs = snapshot.get("inputs") or []

        def _contains_any(value: str, terms) -> bool:
            return any(term.lower() in value for term in terms)

        strong_terms = (
            "短信验证码",
            "手机验证码",
            "手机号验证",
            "手机验证",
            "安全验证",
            "身份验证",
            "验证码已发送",
            "已发送",
            "重新发送",
            "输入验证码",
            "请输入验证码",
            "请输入短信验证码",
            "校验码",
            "verification code",
            "sms code",
        )
        generic_form_terms = (
            "获取验证码",
            "发送验证码",
            "验证码登录",
            "login with code",
        )

        has_strong_prompt = _contains_any(combined, strong_terms)
        has_generic_form_prompt = _contains_any(combined, generic_form_terms)
        has_code_input = False

        for item in inputs:
            if not isinstance(item, dict):
                continue
            meta = " ".join(
                str(item.get(key) or "")
                for key in ("placeholder", "ariaLabel", "name", "id", "className", "type")
            ).lower()
            maxlength_raw = str(item.get("maxlength") or "").strip()
            try:
                maxlength = int(maxlength_raw)
            except ValueError:
                maxlength = 0
            input_type = str(item.get("type") or "").lower()
            explicit_code_input = _contains_any(
                meta,
                ("验证码", "短信", "sms", "verify", "verification", "code"),
            )
            short_code_input = 4 <= maxlength <= 6 and input_type in ("", "text", "tel", "number")
            if explicit_code_input or short_code_input:
                has_code_input = True
                break

        if qrcode_visible:
            return has_code_input and has_strong_prompt
        return has_code_input or (has_strong_prompt and has_generic_form_prompt)

    async def _has_visible_sms_challenge(self, page, *, qrcode_visible: bool) -> bool:
        try:
            snapshot = await page.evaluate('''() => {
                const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style && style.visibility !== 'hidden' && style.display !== 'none'
                        && rect.width > 0 && rect.height > 0;
                };
                const inputs = Array.from(document.querySelectorAll('input, textarea'))
                    .filter(isVisible)
                    .map((el) => ({
                        placeholder: el.getAttribute('placeholder') || '',
                        ariaLabel: el.getAttribute('aria-label') || '',
                        name: el.getAttribute('name') || '',
                        id: el.id || '',
                        className: typeof el.className === 'string' ? el.className : '',
                        type: el.getAttribute('type') || '',
                        maxlength: el.getAttribute('maxlength') || '',
                    }));
                const controls = Array.from(document.querySelectorAll('button, [role="button"], a, span, div'))
                    .filter(isVisible)
                    .slice(0, 400)
                    .map((el) => (el.innerText || el.textContent || '').trim())
                    .filter(Boolean)
                    .slice(0, 200);
                return {
                    text: document.body ? document.body.innerText || '' : '',
                    inputs,
                    controls,
                };
            }''')
            return self._sms_challenge_signal_from_snapshot(
                snapshot,
                qrcode_visible=qrcode_visible,
            )
        except Exception as e:
            logger.debug(f"短信验证页面信号检测失败: {e}")
            return False

    @staticmethod
    def _scan_confirmation_signal_from_snapshot(snapshot, *, qrcode_visible: bool) -> bool:
        if qrcode_visible or not isinstance(snapshot, dict):
            return False
        text = str(snapshot.get("text") or "")
        controls = " ".join(str(v or "") for v in snapshot.get("controls") or [])
        combined = f"{text} {controls}".lower()
        scan_terms = (
            "已扫码",
            "扫码成功",
            "扫描成功",
            "等待手机确认",
            "等待确认",
            "请在手机",
            "请在小红书",
            "确认登录",
            "手机上确认",
            "scan successful",
            "scanned",
            "confirm on your phone",
            "confirm login",
        )
        return any(term.lower() in combined for term in scan_terms)

    async def _has_scan_confirmation(self, page, *, qrcode_visible: bool) -> bool:
        try:
            snapshot = await page.evaluate('''() => {
                const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style && style.visibility !== 'hidden' && style.display !== 'none'
                        && rect.width > 0 && rect.height > 0;
                };
                const controls = Array.from(document.querySelectorAll('button, [role="button"], a, span, div, p'))
                    .filter(isVisible)
                    .slice(0, 500)
                    .map((el) => (el.innerText || el.textContent || '').trim())
                    .filter(Boolean)
                    .slice(0, 240);
                return {
                    text: document.body ? document.body.innerText || '' : '',
                    controls,
                };
            }''')
            return self._scan_confirmation_signal_from_snapshot(
                snapshot,
                qrcode_visible=qrcode_visible,
            )
        except Exception as e:
            logger.debug(f"扫码确认页面信号检测失败: {e}")
            return False

    @staticmethod
    def _sms_error_message_from_snapshot(snapshot) -> Optional[str]:
        if not isinstance(snapshot, dict):
            return None
        text = str(snapshot.get("text") or "")
        controls = " ".join(str(v or "") for v in snapshot.get("controls") or [])
        combined = f"{text} {controls}".lower()
        error_terms = (
            "验证码错误",
            "验证码不正确",
            "验证码有误",
            "验证码已过期",
            "验证码过期",
            "验证码失效",
            "校验码错误",
            "校验码不正确",
            "请重新输入",
            "请输入正确的验证码",
            "verification code is incorrect",
            "verification code expired",
            "invalid verification code",
            "incorrect code",
            "code expired",
        )
        if any(term.lower() in combined for term in error_terms):
            return "验证码错误或已过期，请重新输入"
        if "操作频繁" in combined or "请求过于频繁" in combined or "too many" in combined:
            return "验证码请求过于频繁，请稍后再试"
        return None

    async def _detect_sms_code_error(self, page) -> Optional[str]:
        try:
            snapshot = await page.evaluate('''() => {
                const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style && style.visibility !== 'hidden' && style.display !== 'none'
                        && rect.width > 0 && rect.height > 0;
                };
                const controls = Array.from(document.querySelectorAll('button, [role="button"], a, span, div, p'))
                    .filter(isVisible)
                    .slice(0, 500)
                    .map((el) => (el.innerText || el.textContent || '').trim())
                    .filter(Boolean)
                    .slice(0, 240);
                return {
                    text: document.body ? document.body.innerText || '' : '',
                    controls,
                };
            }''')
            return self._sms_error_message_from_snapshot(snapshot)
        except Exception as e:
            logger.debug(f"短信验证码错误提示检测失败: {e}")
            return None

    async def _handle_sms_submission_error(self, page, session: QRCodeSession) -> bool:
        if not session.sms_code_submitted:
            return False
        message = await self._detect_sms_code_error(page)
        if not message:
            return False
        session.sms_code_submitted = False
        session.status = QRLoginStatus.NEED_SMS_CODE
        session.error_message = message
        await self._publish_screenshot(page, session)
        logger.warning(f"会话 {session.session_id}: {message}")
        return True

    async def _check_page_interaction(self, page) -> Optional[str]:
        qrcode_visible = await self._has_visible_qrcode(page)
        if await self._has_visible_sms_challenge(page, qrcode_visible=qrcode_visible):
            return 'sms_code'
        if await self._has_scan_confirmation(page, qrcode_visible=qrcode_visible):
            return 'scanned'
        if qrcode_visible:
            return None

        sms_selectors = [
            'input[placeholder*="验证码"]',
            'input[placeholder*="短信"]',
            'input[placeholder*="code"]',
            'input[class*="code-input"]',
            'input[class*="sms"]',
            'input[class*="verify"]',
            'input[type="tel"]',
            'input[maxlength="4"]',
            'input[maxlength="6"]',
        ]
        for selector in sms_selectors:
            try:
                element = await page.query_selector(selector)
                if element and await element.is_visible():
                    return 'sms_code'
            except Exception:
                continue

        slider_selectors = [
            '[class*="slider"]',
            '[class*="slide-verify"]',
            '[class*="captcha"]',
        ]
        for selector in slider_selectors:
            try:
                element = await page.query_selector(selector)
                if element and await element.is_visible():
                    return 'slider'
            except Exception:
                continue

        return None

    async def _click_get_sms_code_button(self, page, session_id: str):
        get_code_selectors = [
            ':text-is("获取验证码")',
            ':text-is("发送验证码")',
            ':text-is("获取短信验证码")',
            ':has-text("获取验证码")',
            ':has-text("发送验证码")',
            'button:has-text("获取")',
            'div:has-text("获取验证码")',
            'span:has-text("获取验证码")',
            '[class*="get-code"]',
            '[class*="send-code"]',
        ]

        for selector in get_code_selectors:
            try:
                btn = await page.query_selector(selector)
                if btn and await btn.is_visible():
                    btn_text = await btn.inner_text()
                    if '获取' in btn_text or '发送' in btn_text:
                        await btn.click(force=True)
                        logger.info(f"会话 {session_id}: 已点击'{btn_text}'按钮，等待短信")
                        return True
            except Exception as e:
                logger.debug(f"会话 {session_id}: 获取验证码按钮选择器 {selector} 失败: {e}")
                continue

        logger.debug(f"会话 {session_id}: 未找到获取验证码按钮（可能已发送或不需要）")
        return False

    async def submit_sms_code(self, session_id: str, sms_code: str) -> bool:
        page = self._pages.get(session_id)
        session = self._sessions.get(session_id)

        if not page or not session:
            logger.error(f"会话 {session_id}: 页面或会话不存在")
            return False

        if session.status != QRLoginStatus.NEED_SMS_CODE:
            logger.warning(f"会话 {session_id}: 当前状态不需要短信验证码")
            return False

        try:
            session.error_message = None
            sms_selectors = [
                'input[placeholder*="验证码"]',
                'input[placeholder*="短信"]',
                'input[placeholder*="输入"]',
                'input[class*="code"]',
                'input[class*="sms"]',
                'input[class*="verify"]',
                'input[type="tel"]',
                'input[type="number"]',
                'input[maxlength="4"]',
                'input[maxlength="6"]',
                '[class*="code"] input',
                '[class*="sms"] input',
            ]
            input_filled = False
            for selector in sms_selectors:
                try:
                    input_el = await page.query_selector(selector)
                    if input_el and await input_el.is_visible():
                        await input_el.click(force=True, timeout=3000)
                        await page.wait_for_timeout(100)
                        await input_el.fill('')
                        await input_el.fill(sms_code)

                        actual_value = await input_el.input_value()
                        if actual_value == sms_code:
                            input_filled = True
                            logger.info(f"会话 {session_id}: 已填入验证码（选择器: {selector}）")
                            break

                        await input_el.click(force=True, timeout=3000)
                        await page.keyboard.press('Control+a')
                        await page.keyboard.type(sms_code, delay=50)
                        actual_value = await input_el.input_value()
                        if actual_value == sms_code:
                            input_filled = True
                            logger.info(f"会话 {session_id}: 已通过键盘输入验证码")
                            break
                except Exception as e:
                    logger.debug(f"会话 {session_id}: 输入框选择器 {selector} 失败: {e}")
                    continue

            if not input_filled:
                logger.warning(f"会话 {session_id}: 常规输入失败，尝试 JavaScript 直接操作")
                try:
                    js_result = await page.evaluate(f'''() => {{
                        const inputs = document.querySelectorAll(
                            'input[type="text"], input[type="tel"], input[type="number"], input:not([type])'
                        );
                        for (const input of inputs) {{
                            if (input.offsetParent !== null) {{
                                const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                                    window.HTMLInputElement.prototype, 'value'
                                ).set;
                                nativeInputValueSetter.call(input, "{sms_code}");
                                input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                return true;
                            }}
                        }}
                        return false;
                    }}''')
                    if js_result:
                        input_filled = True
                        logger.info(f"会话 {session_id}: 已通过 JavaScript 填入验证码")
                except Exception as e:
                    logger.debug(f"会话 {session_id}: JavaScript 输入失败: {e}")

            if not input_filled:
                try:
                    any_input = await page.query_selector('input:visible, [contenteditable="true"]')
                    if any_input:
                        await any_input.click()
                        await page.wait_for_timeout(100)
                except Exception:
                    pass
                await page.keyboard.type(sms_code, delay=50)
                input_filled = True
                logger.info(f"会话 {session_id}: 已通过键盘输入验证码")

            if not input_filled:
                logger.error(f"会话 {session_id}: 未找到验证码输入框")
                return False

            submit_selectors = [
                ':text-is("验证")',
                'button:text-is("验证")',
                'div:text-is("验证")',
                'span:text-is("验证")',
                ':text-is("确定")',
                ':text-is("确认")',
                ':text-is("登录")',
                ':text-is("提交")',
                'button:text-is("确定")',
                'button:text-is("确认")',
                '[class*="submit"]:not([class*="code"])',
                '[class*="confirm"]',
                '[class*="verify-btn"]',
            ]
            button_clicked = False
            for selector in submit_selectors:
                try:
                    btn = await page.query_selector(selector)
                    if btn and await btn.is_visible():
                        btn_text = await btn.inner_text()
                        if '获取' in btn_text or '发送' in btn_text or '重新' in btn_text:
                            continue
                        await btn.click(force=True)
                        logger.info(f"会话 {session_id}: 已点击确认按钮 '{btn_text}'")
                        button_clicked = True
                        break
                except Exception:
                    continue

            if not button_clicked:
                await page.keyboard.press('Enter')

            await page.wait_for_timeout(2000)
            session.sms_code_submitted = True
            session.status = QRLoginStatus.WAITING_SCAN
            return True

        except Exception as e:
            logger.error(f"会话 {session_id}: 提交验证码失败 - {e}")
            return False

    async def _monitor_login_status(self, session: QRCodeSession):
        page = self._pages.get(session.session_id)
        context = self._contexts.get(session.session_id)

        if not page or not context:
            return

        start_time = datetime.now()
        initial_cookies = await context.cookies()
        initial_web_session = next(
            (c['value'] for c in initial_cookies if c['name'] == 'web_session'), ''
        )
        last_verify_time = None
        verify_retry_interval = 5

        while session.is_active and self._is_session_current(session):
            elapsed = (datetime.now() - start_time).total_seconds()
            if elapsed > LOGIN_TIMEOUT_SECONDS:
                session.status = QRLoginStatus.EXPIRED
                session.error_message = "登录超时，请重试"
                break

            try:
                if page.is_closed():
                    session.status = QRLoginStatus.CANCELLED
                    session.error_message = "浏览器窗口已关闭"
                    break

                if await self._handle_sms_submission_error(page, session):
                    await asyncio.sleep(2)
                    continue

                if session.status == QRLoginStatus.NEED_SMS_CODE and not session.sms_code_submitted:
                    logger.debug(f"会话 {session.session_id}: 等待短信验证码提交，暂停 Cookie 身份验证")
                    await asyncio.sleep(2)
                    continue

                interaction_type = await self._check_page_interaction(page)
                if interaction_type == 'sms_code' and not session.sms_code_submitted:
                    if session.status != QRLoginStatus.NEED_SMS_CODE:
                        session.status = QRLoginStatus.NEED_SMS_CODE
                        logger.info(f"会话 {session.session_id}: 需要输入短信验证码，暂停 Cookie 身份验证")
                    await asyncio.sleep(2)
                    continue
                if interaction_type == 'scanned' and session.status == QRLoginStatus.WAITING_SCAN:
                    session.status = QRLoginStatus.SCANNED
                    logger.info(f"会话 {session.session_id}: 已扫码，等待手机确认")
                if interaction_type == 'slider':
                    session.error_message = '需要滑块验证，请使用手动复制 Cookie 方式'
                    logger.warning(f"会话 {session.session_id}: 需要滑块验证")
                    await asyncio.sleep(2)
                    continue

                should_verify = False

                try:
                    if await self._page_looks_logged_in(page):
                        should_verify = True
                        logger.info(f"会话 {session.session_id}: UI 节点检测到已登录")
                except Exception:
                    pass

                if not should_verify:
                    cookies = await context.cookies()
                    cookie_dict = {c['name']: c['value'] for c in cookies}
                    current_web_session = cookie_dict.get('web_session', '')
                    if current_web_session and current_web_session != initial_web_session:
                        should_verify = True
                        initial_web_session = current_web_session
                        logger.info(f"会话 {session.session_id}: Cookie web_session 变化，触发验证")

                if not should_verify and session.status == QRLoginStatus.SCANNED and last_verify_time:
                    time_since_last = (datetime.now() - last_verify_time).total_seconds()
                    if time_since_last >= verify_retry_interval:
                        should_verify = True

                if should_verify:
                    session.status = QRLoginStatus.SCANNED
                    last_verify_time = datetime.now()
                    await self._publish_screenshot(page, session)

                    cookies = await context.cookies()
                    cookies_str = '; '.join([f"{c['name']}={c['value']}" for c in cookies])

                    if await self._verify_cookie_valid(
                        cookies_str,
                        # 扫码完成后允许当前设备切换到新账号；旧 expected_user_id 只用于
                        # 复用已有浏览器态时防串号，不能阻止用户扫码登录另一个真实账号。
                        expected_user_id=None,
                    ):
                        session.status = QRLoginStatus.CONFIRMED
                        session.cookies_str = cookies_str

                        logger.success(
                            f"会话 {session.session_id}: Cookie 已验证有效，等待身份解析后保存"
                        )

                        session.status = QRLoginStatus.SUCCESS
                        break
                    else:
                        logger.info(f"会话 {session.session_id}: Cookie 暂时无效，{verify_retry_interval}秒后重试...")

            except Exception as e:
                logger.warning(f"会话 {session.session_id}: 监听出错 - {e}")

            await asyncio.sleep(2)

        await self._cleanup_session(session.session_id, remove_from_sessions=False)
        await asyncio.sleep(_login_success_close_delay_sec())
        if session.is_completed:
            logger.info(
                f"会话 {session.session_id}: 登录结果保留 {_session_result_retention_sec()} 秒，等待前端领取"
            )
            await asyncio.sleep(_session_result_retention_sec())
        if session.session_id in self._sessions:
            del self._sessions[session.session_id]

    async def _verify_cookie_valid(
        self,
        cookies_str: str,
        *,
        expected_user_id: Optional[str] = None,
    ) -> bool:
        """扫码登录必须能拿到 selfinfo 身份，不能只用搜索成功判定 Cookie 有效。

        hotfix 4: 内部 requests.post 是同步的,必须用 asyncio.to_thread 包装,
        否则 `_monitor_login_status` 的监听循环会每 2 秒阻塞事件循环最多 10 秒,
        导致前端 `/auth/me` 响应延迟,AuthGate 卡在"正在验证登录状态..."界面。
        """
        try:
            from apis.xhs_pc_apis import XHS_Apis

            def _blocking_probe() -> bool:
                xhs = XHS_Apis()
                ok, msg, data = xhs.get_user_self_info(cookies_str)
                if ok:
                    identity = self._extract_payload_identity(data)
                    if identity and self._identity_matches(identity, expected_user_id):
                        logger.info(
                            f"selfinfo Cookie 身份校验通过: "
                            f"user_id={str(identity.get('user_id') or '')[:12]}..., "
                            f"nickname={identity.get('nickname')}, source={identity.get('source')}"
                        )
                        return True
                    logger.warning(
                        f"selfinfo 成功但身份未匹配: identity={identity}, "
                        f"expected={expected_user_id or '-'}, shape={self._payload_shape(data)}"
                    )
                ok2, msg2, d2 = xhs.get_user_self_info2(cookies_str)
                if ok2:
                    identity = self._extract_payload_identity(d2)
                    if identity and self._identity_matches(identity, expected_user_id):
                        logger.info(
                            f"selfinfo2 Cookie 身份校验通过: "
                            f"user_id={str(identity.get('user_id') or '')[:12]}..., "
                            f"nickname={identity.get('nickname')}, source={identity.get('source')}"
                        )
                        return True
                    logger.warning(
                        f"selfinfo2 成功但身份未匹配: identity={identity}, "
                        f"expected={expected_user_id or '-'}, shape={self._payload_shape(d2)}"
                    )
                logger.warning(
                    f"Cookie 身份校验失败，不能用于系统登录: selfinfo={msg}, selfinfo2={msg2}"
                )
                return False

            return await asyncio.to_thread(_blocking_probe)
        except Exception as e:
            logger.warning(f"验证 Cookie 时出错: {e}")
            return False

    @staticmethod
    def _identity_matches(identity: dict, expected_user_id: Optional[str]) -> bool:
        if not expected_user_id:
            return True
        actual = str(identity.get("user_id") or "").strip()
        return actual == expected_user_id

    @staticmethod
    def _payload_shape(payload) -> dict:
        data = (payload or {}).get("data") if isinstance(payload, dict) else None

        def _shape(value, depth: int = 0):
            if depth > 2:
                return type(value).__name__
            if isinstance(value, dict):
                return {str(k): _shape(v, depth + 1) for k, v in list(value.items())[:12]}
            if isinstance(value, list):
                return [_shape(value[0], depth + 1)] if value else []
            return type(value).__name__

        return _shape(data)

    @staticmethod
    def _extract_payload_identity(payload) -> Optional[dict]:
        data = (payload or {}).get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return None

        def _text(value) -> str:
            return str(value).strip() if value is not None else ""

        def _fields(src) -> tuple[str, str]:
            if not isinstance(src, dict):
                return "", ""
            user_id = (
                src.get("user_id")
                or src.get("userId")
                or src.get("userID")
                or src.get("userid")
                or src.get("userIdStr")
            )
            nickname = src.get("nickname") or src.get("nick_name") or src.get("nickName")
            return _text(user_id), _text(nickname)

        def _nickname() -> str:
            for src in (
                data.get("basic_info"),
                data.get("basicInfo"),
                data.get("user_info"),
                data.get("userInfo"),
                data.get("result"),
                data,
            ):
                _, name = _fields(src)
                if name:
                    return name
            return ""

        def _legacy_user_id() -> tuple[str, str]:
            result = data.get("result") if isinstance(data.get("result"), dict) else {}
            basic_info = data.get("basic_info") if isinstance(data.get("basic_info"), dict) else {}
            basic_info = basic_info or (
                data.get("basicInfo") if isinstance(data.get("basicInfo"), dict) else {}
            )
            candidates = (
                ("result.data", result.get("data")),
                ("result.red_id", result.get("red_id")),
                ("result.redId", result.get("redId")),
                ("result.user_id", result.get("user_id")),
                ("result.userId", result.get("userId")),
                ("basic_info.red_id", basic_info.get("red_id")),
                ("basic_info.redId", basic_info.get("redId")),
                ("data.red_id", data.get("red_id")),
                ("data.redId", data.get("redId")),
            )
            for source, value in candidates:
                user_id = _text(value)
                if user_id and user_id.lower() not in ("true", "false", "none", "null"):
                    return user_id, source
            return "", ""

        legacy_user_id, legacy_source = _legacy_user_id()
        nickname = _nickname()
        if legacy_user_id and nickname:
            return {"user_id": legacy_user_id, "nickname": nickname, "source": legacy_source}

        known_paths = (
            ("user",),
            ("user_info",),
            ("userInfo",),
            ("basic_info",),
            ("basicInfo",),
            ("result",),
            ("result", "user"),
            ("result", "user_info"),
            ("result", "userInfo"),
            ("result", "basic_info"),
            ("result", "basicInfo"),
        )

        def _get_path(root, path):
            current = root
            for key in path:
                if not isinstance(current, dict):
                    return None
                current = current.get(key)
            return current

        for path in known_paths:
            candidate = _get_path(data, path)
            user_id, nickname = _fields(candidate)
            if user_id and nickname:
                return {
                    "user_id": user_id,
                    "nickname": nickname,
                    "source": ".".join(path) or "data",
                }

        return None

    async def get_session(self, session_id: str) -> Optional[QRCodeSession]:
        return self._sessions.get(session_id)

    async def cancel_session(self, session_id: str):
        session = self._sessions.get(session_id)
        if session:
            session.status = QRLoginStatus.CANCELLED
            await self._cleanup_session(session_id)

    async def _cleanup_session(self, session_id: str, remove_from_sessions: bool = True):
        try:
            if session_id in self._pages:
                try:
                    await self._pages[session_id].close()
                except Exception:
                    pass
                del self._pages[session_id]

            if session_id in self._contexts:
                try:
                    await self._contexts[session_id].close()
                except Exception:
                    pass
                del self._contexts[session_id]

            if session_id in self._browsers:
                try:
                    await self._browsers[session_id].close()
                    logger.info(f"会话 {session_id}: 浏览器窗口已关闭")
                except Exception:
                    pass
                del self._browsers[session_id]

            if remove_from_sessions and session_id in self._sessions:
                del self._sessions[session_id]

        except Exception as e:
            logger.warning(f"清理会话出错: {e}")

    async def _cleanup_user_sessions(
        self,
        username: str,
        client_device_id: Optional[str] = None,
    ):
        """只清理同一设备的旧扫码会话，不影响其他设备正在登录。"""
        old_sessions = [
            sid for sid, s in self._sessions.items()
            if s.username == username and s.is_active
            and (
                (client_device_id and s.client_device_id == client_device_id)
                or (not client_device_id and not s.client_device_id)
            )
        ]
        for sid in old_sessions:
            await self.cancel_session(sid)

    async def cleanup_all(self):
        for sid in list(self._sessions.keys()):
            await self._cleanup_session(sid)
        self._sessions.clear()
        self._browsers.clear()

        if self._warmed_browser:
            try:
                await self._warmed_browser.close()
            except Exception:
                pass
            self._warmed_browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None


def get_qrcode_login_service() -> QRCodeLoginService:
    return QRCodeLoginService.get_instance()
