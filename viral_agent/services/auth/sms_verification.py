"""
短信验证码操作模块

处理小红书登录流程中的短信验证码检测、填入、提交。
支持二次短信验证弹窗（扫码后独立弹出的安全验证）和普通短信验证。
核心策略：优先检测二次弹窗 → 在弹窗内精准操作 → 回退到全局搜索。
"""
from typing import Optional
from loguru import logger

# ─── 二次弹窗检测 JS ───────────────────────────────────────
# XHS 二次弹窗使用 CSS Modules hash 类名（每次构建变化），
# 故通过标题文本「短信验证码验证」定位，再向上查找包含 input+button 的容器。
_SMS_DIALOG_KEYWORDS = [
    '短信验证码验证', '安全验证', '身份验证', '验证身份',
    '短信验证', '验证手机', '手机验证',
]

_FIND_DIALOG_JS = '''() => {
    const keywords = ['短信验证码验证', '安全验证', '身份验证', '验证身份',
                      '短信验证', '验证手机', '手机验证'];
    const walker = document.createTreeWalker(
        document.body, NodeFilter.SHOW_TEXT,
        { acceptNode: n => {
            const t = n.textContent.trim();
            return keywords.some(k => t.includes(k))
                ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
        }}
    );
    const tn = walker.nextNode();
    if (!tn) return null;
    let el = tn.parentElement;
    for (let i = 0; i < 10 && el && el !== document.body; i++) {
        if (el.querySelector('input') && el.querySelector('button')) return el;
        el = el.parentElement;
    }
    return null;
}'''

# 策略 1 的完整 JS：内嵌弹窗检测 + nativeInputValueSetter 绕过 Vue
_FILL_INPUT_JS = '''(code) => {
    function findDialog() {
        const keywords = ['短信验证码验证', '安全验证', '身份验证', '验证身份',
                          '短信验证', '验证手机', '手机验证'];
        const walker = document.createTreeWalker(
            document.body, NodeFilter.SHOW_TEXT,
            { acceptNode: n => {
                const t = n.textContent.trim();
                return keywords.some(k => t.includes(k))
                    ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
            }}
        );
        const tn = walker.nextNode();
        if (!tn) return null;
        let el = tn.parentElement;
        for (let i = 0; i < 10 && el && el !== document.body; i++) {
            if (el.querySelector('input') && el.querySelector('button')) return el;
            el = el.parentElement;
        }
        return null;
    }
    function fill(input, code) {
        input.focus();
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(input, code);
        input.dispatchEvent(new InputEvent('input', {
            bubbles: true, inputType: 'insertText', data: code }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        input.dispatchEvent(new Event('compositionend', { bubbles: true }));
        return input.value === code;
    }
    const dialog = findDialog();
    if (dialog) {
        const input = dialog.querySelector('input');
        if (input && input.offsetParent !== null) return fill(input, code);
    }
    const sels = [
        'input[placeholder*="验证码"]', 'input[placeholder*="短信"]',
        'input[placeholder*="输入"]',
        'input[class*="code"]', 'input[class*="sms"]',
        'input[type="tel"]', 'input[type="number"]',
        'input[maxlength="4"]', 'input[maxlength="6"]',
    ];
    for (const sel of sels) {
        const input = document.querySelector(sel);
        if (input && input.offsetParent !== null) return fill(input, code);
    }
    return false;
}'''

# 填入验证码时使用的选择器（宽泛，确保最大兼容性）
_SMS_INPUT_SELECTORS = [
    'input[placeholder*="验证码"]', 'input[placeholder*="短信"]',
    'input[placeholder*="输入"]',
    'input[class*="code"]', 'input[class*="sms"]', 'input[class*="verify"]',
    'input[type="tel"]', 'input[type="number"]',
    'input[maxlength="4"]', 'input[maxlength="6"]',
    '[class*="code"] input', '[class*="sms"] input',
]


async def find_secondary_sms_dialog(page) -> Optional:
    """检测并定位二次短信验证弹窗容器，返回 ElementHandle 或 None"""
    try:
        handle = await page.evaluate_handle(_FIND_DIALOG_JS)
        element = handle.as_element()
        if element:
            if await element.is_visible():
                return element
            # 是 Element 但不可见，释放
            await handle.dispose()
        else:
            # JS 返回 null，释放 JSHandle 避免轮询积累
            await handle.dispose()
    except Exception:
        pass
    return None


async def check_page_interaction(page) -> Optional[str]:
    """
    检测页面需要什么类型的交互。

    检测策略（按优先级）：
    1. 文本匹配：通过弹窗标题关键词定位（多关键词，覆盖异地登录等场景）
    2. 特征检测兜底：即使 XHS 换了全新的弹窗标题，只要有
       「验证码输入框 + 获取/发送按钮」的组合就判定为 SMS 验证
       排除条件：QR 码可见时不触发（避免误判扫码页的手机登录区）
    3. 滑块验证检测
    """
    # ── 策略 1：标题文本匹配 ──
    try:
        has_dialog = await page.evaluate('(' + _FIND_DIALOG_JS + ')() !== null')
    except Exception:
        has_dialog = False
    if has_dialog:
        logger.debug("检测到二次短信验证弹窗（文本匹配）")
        return 'sms_code'

    # ── 策略 2：DOM 特征检测兜底 ──
    try:
        has_sms_feature = await page.evaluate('''() => {
            // 排除：如果 QR 码图片可见，说明还在扫码阶段，不应误判手机登录区
            const qrImgs = document.querySelectorAll(
                'img[src*="qrcode"], img[class*="qr"], canvas[class*="qr"], [class*="qrcode"]'
            );
            for (const qr of qrImgs) {
                if (qr.offsetParent !== null && qr.offsetWidth > 50) return false;
            }

            // 特征 A：找到可见的验证码输入框
            const inputSels = [
                'input[placeholder*="验证码"]', 'input[placeholder*="短信"]',
                'input[maxlength="4"]', 'input[maxlength="6"]',
            ];
            let hasCodeInput = false;
            for (const sel of inputSels) {
                const inp = document.querySelector(sel);
                if (inp && inp.offsetParent !== null) { hasCodeInput = true; break; }
            }
            if (!hasCodeInput) return false;

            // 特征 B：找到含「获取/发送」文字的按钮
            const btns = document.querySelectorAll(
                'button, [role="button"], span, div, a'
            );
            for (const btn of btns) {
                if (btn.offsetParent === null) continue;
                const t = (btn.innerText || '').trim();
                if ((t.includes('获取') || t.includes('发送')) && t.length < 15) {
                    return true;  // 输入框 + 发送按钮 = SMS 验证
                }
            }
            return false;
        }''')
    except Exception:
        has_sms_feature = False
    if has_sms_feature:
        logger.debug("检测到二次短信验证弹窗（特征检测：验证码输入框 + 发送按钮）")
        return 'sms_code'

    # ── 策略 3：滑块验证 ──
    for selector in ['[class*="slider"]', '[class*="slide-verify"]', '[class*="captcha"]']:
        try:
            el = await page.query_selector(selector)
            if el and await el.is_visible():
                return 'slider'
        except Exception:
            continue
    return None


async def click_get_sms_code_button(page, session_id: str) -> bool:
    """自动点击「获取验证码」按钮（优先在二次弹窗内查找）"""
    dialog = await find_secondary_sms_dialog(page)
    if dialog:
        try:
            # 优先直接匹配 button/role=button；对 span/div 用 closest 向上找可交互按钮
            for el in await dialog.query_selector_all(
                'button, [role="button"], span, div, a'
            ):
                if not await el.is_visible():
                    continue
                text = (await el.inner_text()).strip()
                aria = await el.evaluate('e => e.getAttribute("aria-label") || ""')
                combined = text + aria
                if '获取' in combined or '发送' in combined:
                    tag = await el.evaluate('e => e.tagName.toLowerCase()')
                    if tag in ('button', 'a') or await el.evaluate(
                        'e => e.getAttribute("role") === "button"'
                    ):
                        await el.click(force=True)
                    else:
                        # span/div 文本节点 → 向上找最近的 button 容器
                        parent_btn = await el.evaluate_handle(
                            'e => e.closest("button,[role=button]")'
                        )
                        target = parent_btn.as_element()
                        if target:
                            await target.click(force=True)
                        else:
                            await parent_btn.dispose()
                            await el.click(force=True)  # 兜底：直接点
                    logger.info(f"会话 {session_id}: 已点击二次弹窗内'{text}'按钮")
                    return True
        except Exception as e:
            logger.debug(f"会话 {session_id}: 二次弹窗内查找获取验证码按钮失败: {e}")

    for selector in [
        ':text-is("获取验证码")', ':text-is("发送验证码")',
        ':text-is("获取短信验证码")',
        ':has-text("获取验证码")', ':has-text("发送验证码")',
        'button:has-text("获取")',
        'div:has-text("获取验证码")', 'span:has-text("获取验证码")',
        '[class*="get-code"]', '[class*="send-code"]',
    ]:
        try:
            btn = await page.query_selector(selector)
            if btn and await btn.is_visible():
                btn_text = await btn.inner_text()
                if '获取' in btn_text or '发送' in btn_text:
                    await btn.click(force=True)
                    logger.info(f"会话 {session_id}: 已点击'{btn_text}'按钮，等待短信")
                    return True
        except Exception:
            continue
    logger.debug(f"会话 {session_id}: 未找到获取验证码按钮（可能已发送或不需要）")
    return False


async def fill_sms_input(page, session_id: str, sms_code: str) -> bool:
    """
    填入短信验证码（三重策略 + 二次弹窗优先）。
    策略1: JS nativeInputValueSetter  策略2: 模拟键盘  策略3: 盲打兜底
    """
    # 策略 1：增强 JS
    try:
        if await page.evaluate(_FILL_INPUT_JS, sms_code):
            logger.info(f"会话 {session_id}: ✅ JavaScript 填入验证码成功")
            return True
    except Exception as e:
        logger.debug(f"会话 {session_id}: JavaScript 输入失败: {e}")

    # 策略 2：模拟真人键盘
    logger.warning(f"会话 {session_id}: JavaScript 输入失败，尝试键盘输入")
    input_el = await _find_sms_input_element(page)
    if input_el and await _keyboard_fill_input(page, input_el, session_id, sms_code):
        return True

    # 策略 3：盲打兜底
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


async def _find_sms_input_element(page):
    """查找 SMS 输入框 ElementHandle（优先二次弹窗）"""
    dialog = await find_secondary_sms_dialog(page)
    if dialog:
        try:
            input_el = await dialog.query_selector('input')
            if input_el and await input_el.is_visible():
                return input_el
        except Exception:
            pass
    for selector in _SMS_INPUT_SELECTORS:
        try:
            input_el = await page.query_selector(selector)
            if input_el and await input_el.is_visible():
                return input_el
        except Exception:
            continue
    return None


async def _keyboard_fill_input(page, input_el, session_id: str, sms_code: str) -> bool:
    """通过模拟键盘在指定 input 中填入验证码"""
    try:
        await input_el.click(force=True, timeout=3000)
        await page.wait_for_timeout(200)
        await page.keyboard.press('Control+a')
        await page.keyboard.press('Backspace')
        await page.wait_for_timeout(100)
        await page.keyboard.type(sms_code, delay=80)
        await page.wait_for_timeout(200)
        await page.keyboard.press('Tab')
        await page.wait_for_timeout(100)
        await input_el.click(force=True, timeout=1000)
        actual_value = await input_el.input_value()
        if actual_value == sms_code:
            logger.info(f"会话 {session_id}: ✅ 键盘输入验证码成功")
            return True
        logger.warning(
            f"会话 {session_id}: 键盘输入后值不匹配，"
            f"期望 '{sms_code}'，实际 '{actual_value}'"
        )
    except Exception as e:
        logger.debug(f"会话 {session_id}: 键盘输入失败: {e}")
    return False


async def click_submit_button(page, session_id: str):
    """查找并点击验证码提交按钮（优先二次弹窗的空文字按钮，回退普通容器）"""
    exclude_kw = ['获取', '发送', '重新', '扫码']

    # 优先：二次弹窗（处理空文字按钮和自定义按钮）
    dialog = await find_secondary_sms_dialog(page)
    if dialog:
        try:
            candidates = []
            for btn in await dialog.query_selector_all(
                'button, [role="button"], div[class*="btn"], span[class*="btn"]'
            ):
                if not await btn.is_visible():
                    continue
                text = (await btn.inner_text()).strip()
                # 排除「获取验证码」类按钮（含 icon-only：检查 aria-label）
                aria = await btn.evaluate('e => e.getAttribute("aria-label") || ""')
                combined = text + aria
                if any(kw in combined for kw in exclude_kw):
                    continue
                candidates.append(btn)
            if candidates:
                await candidates[-1].click(force=True)
                logger.info(f"会话 {session_id}: ✅ 已点击二次弹窗内提交按钮")
                return
        except Exception as e:
            logger.debug(f"会话 {session_id}: 二次弹窗按钮查找失败: {e}")

    # 回退：原有容器检测
    container = None
    for sel in [
        '[class*="login-container"]', '[class*="login-modal"]',
        '[class*="verify-modal"]', '[class*="sms-modal"]',
        '[class*="dialog"]', '[class*="modal"]', '[role="dialog"]',
    ]:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                container = el
                break
        except Exception:
            continue

    scope = container or page
    label = "弹窗容器内" if container else "全局"

    for text in ['验证', '确定', '确认', '登录', '提交']:
        try:
            for btn in await scope.query_selector_all(
                'button, [role="button"], div[class*="btn"], span[class*="btn"]'
            ):
                if not await btn.is_visible():
                    continue
                t = (await btn.inner_text()).strip()
                if (t == text or (text in t and len(t) <= 6)) \
                        and not any(kw in t for kw in exclude_kw):
                    await btn.click(force=True)
                    logger.info(f"会话 {session_id}: 已点击{label}确认按钮 '{t}'")
                    return
        except Exception:
            continue

    for sel in ['[class*="submit"]:not([class*="code"])', '[class*="confirm"]',
                '[class*="verify-btn"]']:
        try:
            btn = await scope.query_selector(sel)
            if btn and await btn.is_visible():
                t = (await btn.inner_text()).strip()
                if not any(kw in t for kw in exclude_kw):
                    await btn.click(force=True)
                    logger.info(f"会话 {session_id}: 已点击{label}确认按钮 '{t}'")
                    return
        except Exception:
            continue

    logger.warning(f"会话 {session_id}: 未找到确认按钮，按回车键提交")
    await page.keyboard.press('Enter')
