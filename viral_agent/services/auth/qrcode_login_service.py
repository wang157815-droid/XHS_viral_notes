"""
小红书扫码登录服务（可视化浏览器版）

工作方式：
1. 点击按钮后弹出真实浏览器窗口
2. 用户在窗口中完成所有登录操作（扫码、短信验证等）
3. 系统自动检测登录成功并捕获 Cookie
4. 登录完成后自动关闭窗口
"""
import asyncio
import base64
from datetime import datetime, timedelta
from typing import Dict, Optional

from loguru import logger

from .qrcode_session import QRCodeSession, QRLoginStatus

# 配置常量
LOGIN_TIMEOUT_SECONDS = 180  # 3分钟超时（用户需要时间完成验证）
MAX_CONCURRENT_SESSIONS = 1   # 可视化模式只允许一个会话
XHS_LOGIN_URL = "https://www.xiaohongshu.com/explore"


class QRCodeLoginService:
    """
    小红书扫码登录服务（可视化浏览器版）

    打开真实浏览器窗口，用户可完成所有登录操作，系统自动捕获 Cookie。

    优化特性：
    - 浏览器预热：应用启动时预创建浏览器，首次扫码请求时直接复用
    - 智能等待：用元素等待替代固定延迟，大幅缩短二维码出现时间
    """

    _instance: Optional['QRCodeLoginService'] = None

    def __init__(self):
        self._sessions: Dict[str, QRCodeSession] = {}
        self._browsers: Dict[str, 'Browser'] = {}  # 每个会话独立浏览器
        self._contexts: Dict[str, 'BrowserContext'] = {}
        self._pages: Dict[str, 'Page'] = {}
        self._playwright = None
        self._lock = asyncio.Lock()
        self._playwright_available: Optional[bool] = None
        # 深度预热相关
        self._warmed_browser: Optional['Browser'] = None
        self._warmed_context: Optional['BrowserContext'] = None
        self._warmed_page: Optional['Page'] = None
        self._warmup_completed_at: Optional[datetime] = None
        self._warmup_lock = asyncio.Lock()
        self._is_warming_up = False

    @classmethod
    def get_instance(cls) -> 'QRCodeLoginService':
        """单例模式获取服务实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def _has_display() -> bool:
        """检查是否有图形显示环境"""
        import os
        import sys

        # Windows 总是有图形界面
        if sys.platform == 'win32':
            return True

        # macOS 通常有图形界面
        if sys.platform == 'darwin':
            return True

        # Linux：检查 DISPLAY 环境变量
        if os.environ.get('DISPLAY'):
            return True

        # 检查是否有 Wayland
        if os.environ.get('WAYLAND_DISPLAY'):
            return True

        return False

    @staticmethod
    def _is_server_mode() -> bool:
        """检查是否在服务器模式（无图形界面）"""
        # 强制使用服务器模式（截图方式），方便测试
        # 如需恢复自动检测，取消下面的注释并删除 return True
        return True
        # import sys
        # if sys.platform == 'win32':
        #     return False
        # return not QRCodeLoginService._has_display()

    async def check_playwright_available(self, force_recheck: bool = False) -> bool:
        """检查 Playwright 是否可用"""
        if self._playwright_available is not None and not force_recheck:
            return self._playwright_available

        try:
            from playwright.async_api import async_playwright

            pw = await async_playwright().start()
            try:
                # 服务器模式使用无头浏览器
                browser = await pw.chromium.launch(headless=True)
                await browser.close()
                self._playwright_available = True

                if self._is_server_mode():
                    logger.info("Playwright 已就绪（服务器模式：截图方式）")
                else:
                    logger.info("Playwright 已就绪（桌面模式：弹出窗口）")
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
        """
        深度预热：提前启动浏览器并导航到二维码页面。

        调用时机：应用启动时（在后台异步执行，不阻塞启动）
        效果：首次扫码请求时从 8-22 秒优化到 <3 秒

        深度预热流程：启动浏览器 → 导航页面 → 点击登录 → 切换二维码 → 截图缓存
        失败时自动降级为浅预热（仅启动浏览器）。
        """
        async with self._warmup_lock:
            if self._warmed_page is not None:
                logger.debug("已有深度预热页面，跳过")
                return True
            if self._warmed_browser is not None:
                logger.debug("已有浅预热浏览器，跳过")
                return True
            if self._is_warming_up:
                logger.debug("预热正在进行中，跳过")
                return False
            self._is_warming_up = True

        try:
            from playwright.async_api import async_playwright

            logger.info("🔥 开始深度预热（浏览器 + 页面导航 + 二维码）...")
            start_time = datetime.now()

            # 初始化 Playwright
            if self._playwright is None:
                self._playwright = await async_playwright().start()

            # 启动浏览器（合并了 check_playwright_available 的检测逻辑）
            is_server = self._is_server_mode()
            try:
                browser = await self._playwright.chromium.launch(
                    headless=is_server,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                    ] + ([] if is_server else ['--start-maximized'])
                )
            except Exception as e:
                self._playwright_available = False
                logger.warning(f"Chromium 启动失败: {e}")
                # 关闭悬挂的 Playwright 驱动进程
                if self._playwright:
                    try:
                        await self._playwright.stop()
                    except Exception:
                        pass
                    self._playwright = None
                return False

            self._playwright_available = True
            browser_elapsed = (datetime.now() - start_time).total_seconds()
            logger.debug(f"浏览器启动耗时 {browser_elapsed:.1f} 秒")

            # 深度预热：导航到二维码页面
            try:
                context = await browser.new_context(
                    viewport={'width': 1200, 'height': 800},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                    locale='zh-CN',
                )
                page = await context.new_page()

                await page.goto(XHS_LOGIN_URL, wait_until='domcontentloaded', timeout=30000)
                try:
                    await page.wait_for_selector('div.login-btn, span.login-btn, div[class*="login-btn"]', timeout=5000)
                except Exception:
                    pass

                # 预点击登录按钮 + 切换二维码 Tab
                await self._warmup_click_to_qrcode(page)

                # 验证二维码元素是否存在（深度预热成功的硬性标准）
                qr_found = await self._verify_qrcode_visible(page)
                if not qr_found:
                    logger.warning("深度预热：二维码元素未找到，降级为浅预热")
                    try:
                        await context.close()
                    except Exception:
                        pass
                    self._warmed_browser = browser
                    self._warmed_context = None
                    self._warmed_page = None
                    self._warmup_completed_at = None
                    return True

                # 保存深度预热状态
                self._warmed_browser = browser
                self._warmed_context = context
                self._warmed_page = page
                self._warmup_completed_at = datetime.now()

                total_elapsed = (datetime.now() - start_time).total_seconds()
                logger.success(f"✅ 深度预热完成，耗时 {total_elapsed:.1f} 秒（浏览器+页面+二维码全部就绪）")
                return True

            except Exception as e:
                # 深度预热失败，降级为浅预热（仅保留浏览器）
                logger.warning(f"深度预热页面导航失败，降级为浅预热: {e}")
                # 关闭已创建的 context/page，避免资源泄漏
                if 'context' in locals() and context:
                    try:
                        await context.close()
                    except Exception:
                        pass
                self._warmed_browser = browser
                self._warmed_context = None
                self._warmed_page = None

                self._warmup_completed_at = None
                return True  # 浅预热仍算成功

        except ImportError:
            self._playwright_available = False
            logger.warning("Playwright 未安装，无法预热")
            return False
        except Exception as e:
            logger.error(f"预热失败: {e}")
            # 如果 browser 已创建但未保存到实例变量，关闭它
            if 'browser' in locals() and browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            return False
        finally:
            self._is_warming_up = False

    async def _warmup_click_to_qrcode(self, page):
        """预热专用：点击登录按钮并切换到二维码（不需要 session 参数）"""
        # 点击登录按钮
        login_selectors = [
            'div.login-btn', 'span.login-btn',
            'div[class*="login-btn"]', 'button:has-text("登录")',
        ]
        for selector in login_selectors:
            try:
                btn = await page.wait_for_selector(selector, timeout=1500)
                if btn:
                    await btn.click()
                    try:
                        await page.wait_for_selector('[class*="login-container"], [class*="login-modal"], [class*="qrcode"]', timeout=3000)
                    except Exception:
                        pass
                    break
            except Exception:
                continue

        # 切换到二维码 Tab
        qrcode_tab_selectors = [
            'span:has-text("扫码登录")', 'div:has-text("扫码登录")',
            '[class*="qrcode-tab"]', '[class*="scan-tab"]',
        ]
        for selector in qrcode_tab_selectors:
            try:
                tab = await page.wait_for_selector(selector, timeout=1500)
                if tab:
                    await tab.click()
                    try:
                        await page.wait_for_selector('img[class*="qrcode"], canvas[class*="qrcode"], [class*="qr-code"]', timeout=2000)
                    except Exception:
                        pass
                    break
            except Exception:
                continue

    async def _verify_qrcode_visible(self, page) -> bool:
        """验证页面上二维码元素是否可见"""
        qrcode_selectors = [
            'img[class*="qrcode"]', 'canvas[class*="qrcode"]',
            '[class*="qr-code"]', '[class*="QRCode"]',
        ]
        for selector in qrcode_selectors:
            try:
                el = await page.query_selector(selector)
                if el and await el.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _get_or_create_browser(self) -> 'Browser':
        """获取预热的浏览器，或创建新浏览器（回退路径使用）"""
        async with self._warmup_lock:
            if self._warmed_browser is not None:
                browser = self._warmed_browser
                warmed_page = self._warmed_page
                warmed_context = self._warmed_context
                # 先清空所有引用
                self._warmed_browser = None
                self._warmed_context = None
                self._warmed_page = None

                self._warmup_completed_at = None

                # 按顺序关闭深度预热的资源（先 page 后 context）
                if warmed_page:
                    try:
                        await warmed_page.close()
                    except Exception:
                        pass
                if warmed_context:
                    try:
                        await warmed_context.close()
                    except Exception:
                        pass
                logger.info("♻️ 复用预热的浏览器（回退到浅预热路径）")
                asyncio.create_task(self.warmup())
                return browser

        # 没有预热的浏览器，创建新的
        logger.info("⏳ 创建新浏览器（未预热）")
        is_server = self._is_server_mode()
        return await self._playwright.chromium.launch(
            headless=is_server,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-setuid-sandbox',
            ] + ([] if is_server else ['--start-maximized'])
        )

    async def _try_use_warmed_page(self, session: QRCodeSession) -> bool:
        """
        尝试使用深度预热的页面（快速路径）。

        如果深度预热就绪，直接取走预热的 browser/context/page，
        用户点击扫码时 <1-3 秒即可看到二维码。

        Returns:
            bool: True 表示成功使用深度预热，False 表示需回退到完整流程
        """
        async with self._warmup_lock:
            if self._warmed_page is None or self._warmed_browser is None:
                return False

            # 取走所有预热资源
            browser = self._warmed_browser
            context = self._warmed_context
            page = self._warmed_page
            warmup_time = self._warmup_completed_at

            self._warmed_browser = None
            self._warmed_context = None
            self._warmed_page = None
            self._warmup_completed_at = None

        try:
            # 检查页面是否仍然有效
            if page.is_closed():
                logger.warning("深度预热页面已关闭，回退到完整流程")
                try:
                    await browser.close()
                except Exception:
                    pass
                asyncio.create_task(self.warmup())
                return False

            # 注册到会话
            self._browsers[session.session_id] = browser
            self._contexts[session.session_id] = context
            self._pages[session.session_id] = page

            # 检查 QR 码新鲜度
            age_seconds = (datetime.now() - warmup_time).total_seconds() if warmup_time else 999
            if age_seconds > 90:
                logger.info(f"⚡ 深度预热页面已就绪（QR码已 {age_seconds:.0f}s，尝试刷新）")
                await self._refresh_qrcode(page)
            else:
                logger.info(f"⚡ 深度预热页面已就绪（QR码 {age_seconds:.0f}s，新鲜可用）")

            # 验证二维码元素仍然可见（防止虚假成功）
            if not await self._verify_qrcode_visible(page):
                logger.warning("深度预热页面上二维码不可见，回退到完整流程")
                # 资源保留在 session dict，由 _open_browser_window 的回退路径清理后重建
                self._browsers.pop(session.session_id, None)
                self._contexts.pop(session.session_id, None)
                self._pages.pop(session.session_id, None)
                if page:
                    try:
                        await page.close()
                    except Exception:
                        pass
                if context:
                    try:
                        await context.close()
                    except Exception:
                        pass
                if browser:
                    try:
                        await browser.close()
                    except Exception:
                        pass
                asyncio.create_task(self.warmup())
                return False

            # 更新会话状态
            session.status = QRLoginStatus.WAITING_SCAN
            session.expires_at = datetime.now() + timedelta(seconds=LOGIN_TIMEOUT_SECONDS)

            # 重新截图
            if self._is_server_mode():
                screenshot = await page.screenshot(type='jpeg', quality=70)
                session.qrcode_base64 = base64.b64encode(screenshot).decode('utf-8')
                asyncio.create_task(self._update_screenshot(session))
            else:
                session.qrcode_base64 = None

            # 启动登录状态监听
            asyncio.create_task(self._monitor_login_status(session))

            # 后台启动新一轮深度预热
            asyncio.create_task(self.warmup())

            return True

        except Exception as e:
            logger.warning(f"使用深度预热页面失败: {e}")
            # 先从 session dict 中移除（避免其他协程访问失效资源）
            self._browsers.pop(session.session_id, None)
            self._contexts.pop(session.session_id, None)
            self._pages.pop(session.session_id, None)
            # 按顺序独立关闭每个资源（任一关闭失败不影响其余）
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            asyncio.create_task(self.warmup())
            return False

    async def _refresh_qrcode(self, page):
        """
        在已有页面上刷新过期的二维码。

        比全页面重新导航快得多（~1-2 秒 vs 5-10 秒）。
        查找刷新/过期相关按钮并点击，或重新切换到二维码 Tab。
        """
        # 尝试点击刷新按钮（小红书 QR 过期后通常显示刷新按钮）
        refresh_selectors = [
            ':text-is("点击刷新")',
            ':text-is("刷新二维码")',
            ':has-text("刷新")',
            ':has-text("重新获取")',
            '[class*="refresh"]',
            '[class*="expired"] >> button',
        ]
        for selector in refresh_selectors:
            try:
                btn = await page.query_selector(selector)
                if btn and await btn.is_visible():
                    await btn.click()
                    logger.info(f"🔄 点击二维码刷新按钮")
                    # 等待新二维码加载
                    try:
                        await page.wait_for_selector(
                            'img[class*="qrcode"], canvas[class*="qrcode"], [class*="qr-code"]',
                            timeout=3000
                        )
                    except Exception:
                        pass
                    return
            except Exception:
                continue

        # 没找到刷新按钮，尝试重新切换 Tab 触发刷新
        logger.debug("未找到刷新按钮，尝试重新切换二维码 Tab")
        await self._warmup_click_to_qrcode(page)

    async def create_session(self, username: str) -> QRCodeSession:
        """创建扫码登录会话（打开可见浏览器窗口）"""
        if not await self.check_playwright_available():
            raise RuntimeError("扫码功能未启用，请安装: pip install playwright && playwright install chromium")

        async with self._lock:
            active_sessions = [s for s in self._sessions.values() if s.is_active]
            if len(active_sessions) >= MAX_CONCURRENT_SESSIONS:
                raise RuntimeError("已有浏览器窗口打开，请先完成或关闭当前登录")

            await self._cleanup_user_sessions(username)

            session = QRCodeSession(username=username)
            self._sessions[session.session_id] = session

            # 启动浏览器窗口
            asyncio.create_task(self._open_browser_window(session))

            logger.info(f"创建扫码会话: {session.session_id}")
            return session

    async def _open_browser_window(self, session: QRCodeSession):
        """打开浏览器（优化版：优先使用深度预热页面，回退到完整流程）"""
        try:
            # ⚡ 优先尝试使用深度预热的页面（<1-3 秒）
            if await self._try_use_warmed_page(session):
                elapsed = (datetime.now() - session.created_at).total_seconds()
                logger.success(f"⚡ 会话 {session.session_id}: 使用深度预热，QR码就绪（{elapsed:.1f}s）")
                return

            # 回退到完整流程
            logger.info(f"会话 {session.session_id}: 深度预热不可用，使用完整流程")
            from playwright.async_api import async_playwright

            start_time = datetime.now()

            # 初始化 Playwright
            if self._playwright is None:
                self._playwright = await async_playwright().start()

            # 判断运行模式
            is_server = self._is_server_mode()

            if is_server:
                logger.info(f"会话 {session.session_id}: 服务器模式，使用无头浏览器 + 截图")
            else:
                logger.info(f"会话 {session.session_id}: 桌面模式，弹出浏览器窗口")

            # 优化：优先使用预热的浏览器
            browser = await self._get_or_create_browser()
            self._browsers[session.session_id] = browser

            browser_elapsed = (datetime.now() - start_time).total_seconds()
            logger.debug(f"会话 {session.session_id}: 浏览器获取耗时 {browser_elapsed:.1f} 秒")

            # 创建浏览器上下文
            context = await browser.new_context(
                viewport={'width': 1200, 'height': 800},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                locale='zh-CN',
            )
            self._contexts[session.session_id] = context

            page = await context.new_page()
            self._pages[session.session_id] = page

            # 打开小红书页面
            await page.goto(XHS_LOGIN_URL, wait_until='domcontentloaded', timeout=30000)
            # 优化：等待登录按钮出现，而非固定延迟
            try:
                await page.wait_for_selector('div.login-btn, span.login-btn, div[class*="login-btn"]', timeout=5000)
            except Exception:
                logger.debug(f"会话 {session.session_id}: 等待登录按钮超时，继续尝试")

            # 尝试点击登录按钮触发登录弹窗
            await self._click_login_button(page, session)

            # 更新状态
            session.status = QRLoginStatus.WAITING_SCAN
            session.expires_at = datetime.now() + timedelta(seconds=LOGIN_TIMEOUT_SECONDS)

            # 服务器模式：截取页面截图（统一使用 jpeg 格式）
            if is_server:
                screenshot = await page.screenshot(type='jpeg', quality=70)
                session.qrcode_base64 = base64.b64encode(screenshot).decode('utf-8')
                logger.success(f"会话 {session.session_id}: 页面截图已生成，等待扫码")
                # 启动截图更新任务
                asyncio.create_task(self._update_screenshot(session))
            else:
                session.qrcode_base64 = None
                logger.success(f"会话 {session.session_id}: 浏览器窗口已打开，等待登录")

            # 启动登录状态监听
            asyncio.create_task(self._monitor_login_status(session))

        except Exception as e:
            logger.error(f"会话 {session.session_id}: 打开浏览器失败 - {e}")
            session.status = QRLoginStatus.ERROR
            session.error_message = f"打开浏览器失败: {str(e)}"
            await self._cleanup_session(session.session_id)

    async def _click_login_button(self, page, session: QRCodeSession):
        """尝试点击登录按钮并切换到二维码登录（优化版：移除硬编码延迟）"""
        # Step 1: 点击登录按钮弹出登录框
        login_selectors = [
            'div.login-btn',
            'span.login-btn',
            'div[class*="login-btn"]',
            'button:has-text("登录")',
        ]
        for selector in login_selectors:
            try:
                btn = await page.wait_for_selector(selector, timeout=1500)  # 优化：缩短超时
                if btn:
                    await btn.click()
                    logger.debug(f"会话 {session.session_id}: 点击登录按钮")
                    # 优化：等待登录框出现，而非固定延迟
                    try:
                        await page.wait_for_selector('[class*="login-container"], [class*="login-modal"], [class*="qrcode"]', timeout=3000)
                    except Exception:
                        pass
                    break
            except Exception:
                continue

        # Step 2: 切换到二维码登录 Tab
        qrcode_tab_selectors = [
            'span:has-text("扫码登录")',
            'div:has-text("扫码登录")',
            '[class*="qrcode-tab"]',
            '[class*="scan-tab"]',
        ]
        for selector in qrcode_tab_selectors:
            try:
                tab = await page.wait_for_selector(selector, timeout=1500)  # 优化：缩短超时
                if tab:
                    await tab.click()
                    logger.debug(f"会话 {session.session_id}: 切换到二维码登录")
                    # 优化：等待二维码加载，而非固定延迟
                    try:
                        await page.wait_for_selector('img[class*="qrcode"], canvas[class*="qrcode"], [class*="qr-code"]', timeout=2000)
                    except Exception:
                        pass
                    break
            except Exception:
                continue

        # Step 3: 验证二维码元素是否存在
        qrcode_selectors = [
            'img[class*="qrcode"]',
            'canvas[class*="qrcode"]',
            '[class*="qr-code"]',
            '[class*="QRCode"]',
        ]
        for selector in qrcode_selectors:
            try:
                qr = await page.wait_for_selector(selector, timeout=2000)  # 优化：缩短超时
                if qr:
                    logger.success(f"会话 {session.session_id}: 二维码已加载")
                    return True
            except Exception:
                continue

        logger.warning(f"会话 {session.session_id}: 未找到二维码元素，截图可能不包含二维码")
        return False

    async def _update_screenshot(self, session: QRCodeSession):
        """定期更新页面截图（服务器模式）"""
        page = self._pages.get(session.session_id)
        if not page:
            return

        # 在 WAITING_SCAN 和 NEED_SMS_CODE 状态下都持续更新截图
        while session.is_active and session.status in (
            QRLoginStatus.WAITING_SCAN, QRLoginStatus.NEED_SMS_CODE
        ):
            try:
                if not page.is_closed():
                    # 检测页面状态
                    interaction_type = await self._check_page_interaction(page)

                    if interaction_type == 'sms_code':
                        # 检测到短信验证码输入框
                        if session.status != QRLoginStatus.NEED_SMS_CODE:
                            session.status = QRLoginStatus.NEED_SMS_CODE
                            logger.info(f"会话 {session.session_id}: 需要输入短信验证码")
                            # 自动点击"获取验证码"按钮发送短信
                            await self._click_get_sms_code_button(page, session.session_id)
                    elif interaction_type == 'slider':
                        session.error_message = '需要滑块验证，请使用手动复制 Cookie 方式'
                        logger.warning(f"会话 {session.session_id}: 需要滑块验证")

                    # 截图（降低质量以减少体积）
                    screenshot = await page.screenshot(type='jpeg', quality=70)
                    session.qrcode_base64 = base64.b64encode(screenshot).decode('utf-8')
            except Exception as e:
                logger.warning(f"会话 {session.session_id}: 截图更新失败 - {e}")
                break

            await asyncio.sleep(3)  # 每3秒更新一次截图

    async def _check_page_interaction(self, page) -> Optional[str]:
        """检测页面需要什么类型的交互"""
        # 检测短信验证码输入框
        sms_selectors = [
            'input[placeholder*="验证码"]',
            'input[placeholder*="短信"]',
            'input[class*="code-input"]',
            'input[class*="sms"]',
        ]
        for selector in sms_selectors:
            try:
                element = await page.query_selector(selector)
                if element and await element.is_visible():
                    return 'sms_code'
            except Exception:
                continue

        # 检测滑块验证
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
        """
        自动点击"获取验证码"按钮发送短信
        """
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
                    # 确认是获取验证码按钮，不是其他按钮
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
        """提交短信验证码（模拟真人键盘输入，兼容 Vue v-model）"""
        page = self._pages.get(session_id)
        session = self._sessions.get(session_id)

        if not page or not session:
            logger.error(f"会话 {session_id}: 页面或会话不存在")
            return False

        if session.status != QRLoginStatus.NEED_SMS_CODE:
            logger.warning(f"会话 {session_id}: 当前状态不需要短信验证码")
            return False

        try:
            input_filled = await self._fill_sms_input(page, session_id, sms_code)

            if not input_filled:
                logger.error(f"会话 {session_id}: 所有输入方式均失败")
                return False

            # 填入验证码后，XHS 可能自动提交登录（无需点击按钮）。
            # _monitor_login_status 可能已检测到 Cookie 有效并关闭了浏览器。
            # 因此需要先检查页面/会话状态。
            if page.is_closed() or session.is_completed:
                logger.info(f"会话 {session_id}: 验证码填入后登录已自动完成，无需点击提交")
                return True

            # 点击确认/提交按钮
            await self._click_submit_button(page, session_id)

            # 等待页面响应
            if not page.is_closed():
                await page.wait_for_timeout(2000)

            # 切换回等待状态，让监控逻辑继续检测登录结果
            if session.is_active:
                session.status = QRLoginStatus.WAITING_SCAN
            return True

        except Exception as e:
            # 如果是浏览器已关闭且登录已成功，不算失败
            if session.is_completed:
                logger.info(f"会话 {session_id}: 验证码提交过程中登录已完成")
                return True
            logger.error(f"会话 {session_id}: 提交验证码失败 - {e}")
            return False

    async def _fill_sms_input(self, page, session_id: str, sms_code: str) -> bool:
        """
        填入短信验证码（三重策略，兼容 Vue v-model）。

        策略 1：增强 JavaScript — 使用 nativeInputValueSetter 绕过 Vue setter，
                触发 InputEvent + change + compositionend，实测小红书最可靠。
        策略 2：模拟真人键盘 — click → 全选 → keyboard.type(delay=80)
                触发完整键盘事件链，部分框架场景下作为备选。
        策略 3：盲打兜底 — 点击任意可见输入框后直接键盘输入。
        """
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

        # 策略 1：增强版 JavaScript（实测小红书最可靠，绕过 Vue setter）
        try:
            js_result = await page.evaluate('''(code) => {
                const selectors = [
                    'input[placeholder*="验证码"]', 'input[placeholder*="短信"]',
                    'input[placeholder*="输入"]',
                    'input[class*="code"]', 'input[class*="sms"]',
                    'input[type="tel"]', 'input[type="number"]',
                    'input[maxlength="4"]', 'input[maxlength="6"]',
                ];
                for (const sel of selectors) {
                    const input = document.querySelector(sel);
                    if (!input || input.offsetParent === null) continue;

                    input.focus();

                    // 使用原生 setter 绕过 Vue 的 property 劫持
                    const nativeSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    ).set;
                    nativeSetter.call(input, code);

                    // 触发 InputEvent（Vue 3 监听的事件类型）
                    input.dispatchEvent(new InputEvent('input', {
                        bubbles: true, inputType: 'insertText', data: code
                    }));
                    // 触发 change（Vue 2 的 lazy 模式和部分组件库需要）
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    // 触发 compositionend（中文输入法兼容）
                    input.dispatchEvent(new Event('compositionend', { bubbles: true }));

                    return input.value === code;
                }
                return false;
            }''', sms_code)
            if js_result:
                logger.info(f"会话 {session_id}: ✅ JavaScript 填入验证码成功")
                return True
        except Exception as e:
            logger.debug(f"会话 {session_id}: JavaScript 输入失败: {e}")

        # 策略 2：模拟真人键盘输入（备选）
        logger.warning(f"会话 {session_id}: JavaScript 输入失败，尝试键盘输入")
        for selector in sms_selectors:
            try:
                input_el = await page.query_selector(selector)
                if not input_el or not await input_el.is_visible():
                    continue

                # 步骤 1：点击聚焦（模拟真人点击输入框）
                await input_el.click(force=True, timeout=3000)
                await page.wait_for_timeout(200)

                # 步骤 2：全选并清空已有内容
                await page.keyboard.press('Control+a')
                await page.keyboard.press('Backspace')
                await page.wait_for_timeout(100)

                # 步骤 3：逐字符键盘输入（触发 keydown/keypress/input/keyup 完整事件链）
                await page.keyboard.type(sms_code, delay=80)
                await page.wait_for_timeout(200)

                # 步骤 4：触发 blur/change 事件（确保 Vue 更新绑定值）
                await page.keyboard.press('Tab')
                await page.wait_for_timeout(100)
                # 重新聚焦回输入框（Tab 可能跳到下一个元素）
                await input_el.click(force=True, timeout=1000)

                # 步骤 5：验证输入值
                actual_value = await input_el.input_value()
                if actual_value == sms_code:
                    logger.info(f"会话 {session_id}: ✅ 键盘输入验证码成功 (选择器: {selector})")
                    return True
                else:
                    logger.warning(f"会话 {session_id}: 键盘输入后值不匹配，期望 '{sms_code}'，实际 '{actual_value}'")
            except Exception as e:
                logger.debug(f"会话 {session_id}: 键盘输入选择器 {selector} 失败: {e}")
                continue

        # 策略 3：盲打兜底（聚焦任意可见输入框后直接键盘输入）
        logger.warning(f"会话 {session_id}: 所有定位失败，尝试盲打兜底")
        try:
            any_input = await page.query_selector('input:visible, [contenteditable="true"]')
            if any_input:
                await any_input.click(force=True)
                await page.wait_for_timeout(200)
            await page.keyboard.press('Control+a')
            await page.keyboard.press('Backspace')
            await page.keyboard.type(sms_code, delay=80)
            logger.info(f"会话 {session_id}: 已通过盲打输入验证码")
            return True
        except Exception as e:
            logger.debug(f"会话 {session_id}: 盲打输入失败: {e}")

        return False

    async def _click_submit_button(self, page, session_id: str):
        """
        在验证码弹窗容器内查找并点击确认按钮。

        先定位弹窗容器，在容器范围内搜索按钮，避免误点页面上其他"登录"按钮。
        """
        # 先尝试定位验证码所在的弹窗/对话框容器
        container_selectors = [
            '[class*="login-container"]', '[class*="login-modal"]',
            '[class*="verify-modal"]', '[class*="sms-modal"]',
            '[class*="dialog"]', '[class*="modal"]',
            '[role="dialog"]',
        ]
        container = None
        for sel in container_selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    container = el
                    break
            except Exception:
                continue

        # 在容器内（或全局回退）搜索提交按钮
        submit_texts = ['验证', '确定', '确认', '登录', '提交']
        exclude_texts = ['获取', '发送', '重新', '扫码']

        search_scope = container if container else page
        scope_label = "弹窗容器内" if container else "全局"

        # 方法 1：在容器内按文本查找
        for text in submit_texts:
            try:
                buttons = await search_scope.query_selector_all(
                    f'button, [role="button"], div[class*="btn"], span[class*="btn"]'
                )
                for btn in buttons:
                    if not await btn.is_visible():
                        continue
                    btn_text = (await btn.inner_text()).strip()
                    if btn_text == text or (text in btn_text and len(btn_text) <= 6):
                        if any(ex in btn_text for ex in exclude_texts):
                            continue
                        await btn.click(force=True)
                        logger.info(f"会话 {session_id}: 已点击{scope_label}确认按钮 '{btn_text}'")
                        return
            except Exception:
                continue

        # 方法 2：class 选择器查找
        class_selectors = [
            '[class*="submit"]:not([class*="code"])',
            '[class*="confirm"]', '[class*="verify-btn"]',
        ]
        for sel in class_selectors:
            try:
                btn = await search_scope.query_selector(sel)
                if btn and await btn.is_visible():
                    btn_text = (await btn.inner_text()).strip()
                    if any(ex in btn_text for ex in exclude_texts):
                        continue
                    await btn.click(force=True)
                    logger.info(f"会话 {session_id}: 已点击{scope_label}确认按钮 '{btn_text}'")
                    return
            except Exception:
                continue

        logger.warning(f"会话 {session_id}: 未找到确认按钮，按回车键提交")
        await page.keyboard.press('Enter')

    async def _monitor_login_status(self, session: QRCodeSession):
        """监听登录状态（验证 Cookie 真正有效）"""
        page = self._pages.get(session.session_id)
        context = self._contexts.get(session.session_id)

        if not page or not context:
            return

        start_time = datetime.now()
        initial_cookies = await context.cookies()
        initial_web_session = next(
            (c['value'] for c in initial_cookies if c['name'] == 'web_session'), ''
        )
        last_verify_time = None  # 上次验证时间，用于 SCANNED 状态下定期重试
        verify_retry_interval = 5  # 验证重试间隔（秒）

        while session.is_active:
            elapsed = (datetime.now() - start_time).total_seconds()
            if elapsed > LOGIN_TIMEOUT_SECONDS:
                session.status = QRLoginStatus.EXPIRED
                session.error_message = "登录超时，请重试"
                break

            try:
                # 检查浏览器是否已关闭
                if page.is_closed():
                    session.status = QRLoginStatus.CANCELLED
                    session.error_message = "浏览器窗口已关闭"
                    break

                cookies = await context.cookies()
                cookie_dict = {c['name']: c['value'] for c in cookies}
                current_web_session = cookie_dict.get('web_session', '')

                # 判断是否需要验证 Cookie
                should_verify = False
                if current_web_session and current_web_session != initial_web_session:
                    # Cookie 发生变化，立即验证
                    should_verify = True
                    initial_web_session = current_web_session
                elif session.status == QRLoginStatus.SCANNED and last_verify_time:
                    # 已在 SCANNED 状态，定期重试验证
                    time_since_last_verify = (datetime.now() - last_verify_time).total_seconds()
                    if time_since_last_verify >= verify_retry_interval:
                        should_verify = True
                        logger.debug(f"会话 {session.session_id}: 定期重试验证 Cookie...")

                if should_verify:
                    session.status = QRLoginStatus.SCANNED
                    last_verify_time = datetime.now()
                    logger.info(f"会话 {session.session_id}: 验证 Cookie 有效性...")

                    cookies_str = '; '.join([f"{c['name']}={c['value']}" for c in cookies])

                    # 验证 Cookie 是否真正有效（调用 API 测试）
                    if await self._verify_cookie_valid(cookies_str):
                        session.status = QRLoginStatus.CONFIRMED
                        session.cookies_str = cookies_str

                        # 保存 Cookie
                        try:
                            from viral_agent.services.user_data_service import get_user_data_service
                            user_data = get_user_data_service(session.username)
                            user_data.save_cookie(cookies_str)
                            logger.success(f"会话 {session.session_id}: Cookie 已验证有效并保存")
                        except Exception as e:
                            logger.error(f"保存 Cookie 失败: {e}")

                        session.status = QRLoginStatus.SUCCESS
                        break
                    else:
                        # Cookie 暂时无效，继续等待（可能用户还在验证短信）
                        logger.info(f"会话 {session.session_id}: Cookie 暂时无效，{verify_retry_interval}秒后重试...")

            except Exception as e:
                logger.warning(f"会话 {session.session_id}: 监听出错 - {e}")

            await asyncio.sleep(2)

        # 清理资源
        await self._cleanup_session(session.session_id, remove_from_sessions=False)
        await asyncio.sleep(5)
        if session.session_id in self._sessions:
            del self._sessions[session.session_id]

    async def _verify_cookie_valid(self, cookies_str: str) -> bool:
        """验证 Cookie 是否真正有效（通过 API 调用测试）"""
        try:
            from apis.xhs_pc_apis import XHS_Apis
            xhs = XHS_Apis()
            # 尝试搜索一个简单关键词
            success, msg, data = xhs.search_some_note("测试", 1, cookies_str)
            return success
        except Exception as e:
            logger.warning(f"验证 Cookie 时出错: {e}")
            return False

    async def get_session(self, session_id: str) -> Optional[QRCodeSession]:
        """获取会话状态"""
        return self._sessions.get(session_id)

    async def cancel_session(self, session_id: str):
        """取消会话"""
        session = self._sessions.get(session_id)
        if session:
            session.status = QRLoginStatus.CANCELLED
            await self._cleanup_session(session_id)

    async def _cleanup_session(self, session_id: str, remove_from_sessions: bool = True):
        """清理会话资源（关闭浏览器窗口）"""
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

            # 关闭独立浏览器实例
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

    async def _cleanup_user_sessions(self, username: str):
        """清理用户的旧会话"""
        old_sessions = [
            sid for sid, s in self._sessions.items()
            if s.username == username and s.is_active
        ]
        for sid in old_sessions:
            await self.cancel_session(sid)

    async def cleanup_all(self):
        """清理所有资源（包括深度预热的浏览器/页面）"""
        for sid in list(self._sessions.keys()):
            await self._cleanup_session(sid)
        self._sessions.clear()
        self._browsers.clear()

        # 在锁保护下取走深度预热的资源（防止与后台 warmup 竞态）
        async with self._warmup_lock:
            warmed_page = self._warmed_page
            warmed_context = self._warmed_context
            warmed_browser = self._warmed_browser
            self._warmed_browser = None
            self._warmed_context = None
            self._warmed_page = None
            self._warmup_completed_at = None

        # 在锁外执行实际关闭操作（按顺序：page → context → browser）
        if warmed_page:
            try:
                await warmed_page.close()
            except Exception:
                pass
        if warmed_context:
            try:
                await warmed_context.close()
            except Exception:
                pass
        if warmed_browser:
            try:
                await warmed_browser.close()
            except Exception:
                pass

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None


def get_qrcode_login_service() -> QRCodeLoginService:
    """获取扫码登录服务单例"""
    return QRCodeLoginService.get_instance()
