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
        # 预热相关
        self._warmed_browser: Optional['Browser'] = None  # 预热的浏览器实例
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
        预热：提前启动浏览器，等待首次扫码请求时复用。

        调用时机：应用启动时（在后台异步执行，不阻塞启动）
        效果：首次扫码请求时节省 3-5 秒的浏览器启动时间

        Returns:
            bool: 预热是否成功
        """
        async with self._warmup_lock:
            if self._warmed_browser is not None:
                logger.debug("浏览器已预热，跳过")
                return True

            if self._is_warming_up:
                logger.debug("预热正在进行中，跳过")
                return False

            self._is_warming_up = True

        try:
            if not await self.check_playwright_available():
                logger.warning("Playwright 不可用，无法预热")
                return False

            from playwright.async_api import async_playwright

            logger.info("🔥 开始预热浏览器...")
            start_time = datetime.now()

            # 初始化 Playwright
            if self._playwright is None:
                self._playwright = await async_playwright().start()

            # 启动浏览器（服务器模式用无头）
            is_server = self._is_server_mode()
            self._warmed_browser = await self._playwright.chromium.launch(
                headless=is_server,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                ] + ([] if is_server else ['--start-maximized'])
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
        """获取预热的浏览器，或创建新浏览器"""
        # 尝试使用预热的浏览器
        async with self._warmup_lock:
            if self._warmed_browser is not None:
                browser = self._warmed_browser
                self._warmed_browser = None  # 使用后清空，下次需要重新预热
                logger.info("♻️ 复用预热的浏览器")
                # 在后台启动新的预热
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
        """打开浏览器（优化版：优先复用预热的浏览器）"""
        try:
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

    async def submit_sms_code(self, session_id: str, sms_code: str) -> bool:
        """提交短信验证码"""
        page = self._pages.get(session_id)
        session = self._sessions.get(session_id)

        if not page or not session:
            logger.error(f"会话 {session_id}: 页面或会话不存在")
            return False

        if session.status != QRLoginStatus.NEED_SMS_CODE:
            logger.warning(f"会话 {session_id}: 当前状态不需要短信验证码")
            return False

        try:
            # 查找验证码输入框并填入
            sms_selectors = [
                'input[placeholder*="验证码"]',
                'input[placeholder*="短信"]',
                'input[placeholder*="输入"]',
                'input[class*="code"]',
                'input[class*="sms"]',
                'input[class*="verify"]',
                'input[type="tel"]',           # 数字输入框常用 type=tel
                'input[type="number"]',
                'input[maxlength="4"]',        # 4位验证码
                'input[maxlength="6"]',        # 6位验证码
                '[class*="code"] input',       # 嵌套输入框
                '[class*="sms"] input',
            ]
            input_filled = False
            for selector in sms_selectors:
                try:
                    input_el = await page.query_selector(selector)
                    if input_el and await input_el.is_visible():
                        # 先点击聚焦
                        await input_el.click()
                        await page.wait_for_timeout(100)
                        # 清空已有内容
                        await input_el.fill('')
                        # 尝试 fill 方法
                        await input_el.fill(sms_code)
                        input_filled = True
                        logger.info(f"会话 {session_id}: 已填入验证码 (选择器: {selector})")
                        break
                except Exception as e:
                    logger.debug(f"会话 {session_id}: 输入框选择器 {selector} 失败: {e}")
                    continue

            # 如果 fill 失败，尝试键盘逐字输入
            if not input_filled:
                logger.warning(f"会话 {session_id}: 标准输入框未找到，尝试键盘输入")
                # 尝试点击页面上可能的输入区域
                try:
                    # 查找任何可聚焦的输入元素
                    any_input = await page.query_selector('input:visible, [contenteditable="true"]')
                    if any_input:
                        await any_input.click()
                        await page.wait_for_timeout(100)
                except Exception:
                    pass
                # 直接用键盘输入验证码
                await page.keyboard.type(sms_code, delay=50)
                input_filled = True
                logger.info(f"会话 {session_id}: 已通过键盘输入验证码")

            if not input_filled:
                logger.error(f"会话 {session_id}: 未找到验证码输入框")
                return False

            # 查找并点击确认/提交按钮
            # 注意：小红书按钮可能是 div/span 而非 button，使用通用选择器
            submit_selectors = [
                ':has-text("验证")',         # 任意元素包含"验证"文字
                'div:has-text("验证")',
                'span:has-text("验证")',
                'button:has-text("验证")',
                ':has-text("确定")',
                ':has-text("确认")',
                ':has-text("登录")',
                'button:has-text("确定")',
                'button:has-text("确认")',
                'button:has-text("登录")',
                '[class*="submit"]',
                '[class*="confirm"]',
                '[class*="verify"]',
                '[class*="btn"]',
            ]
            button_clicked = False
            for selector in submit_selectors:
                try:
                    btn = await page.query_selector(selector)
                    if btn and await btn.is_visible():
                        await btn.click()
                        logger.info(f"会话 {session_id}: 已点击确认按钮 (选择器: {selector})")
                        button_clicked = True
                        break
                except Exception as e:
                    logger.debug(f"会话 {session_id}: 选择器 {selector} 失败: {e}")
                    continue

            if not button_clicked:
                logger.warning(f"会话 {session_id}: 未找到确认按钮，尝试按回车键提交")
                # 尝试按回车键提交
                await page.keyboard.press('Enter')

            # 等待页面响应
            await page.wait_for_timeout(2000)

            # 切换回等待状态，让监控逻辑继续检测登录结果
            session.status = QRLoginStatus.WAITING_SCAN
            return True

        except Exception as e:
            logger.error(f"会话 {session_id}: 提交验证码失败 - {e}")
            return False

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
        """清理所有资源（包括预热的浏览器）"""
        for sid in list(self._sessions.keys()):
            await self._cleanup_session(sid)
        self._sessions.clear()
        self._browsers.clear()

        # 清理预热的浏览器
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
    """获取扫码登录服务单例"""
    return QRCodeLoginService.get_instance()
