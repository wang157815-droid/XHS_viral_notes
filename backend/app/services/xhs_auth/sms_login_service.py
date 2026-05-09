"""SmsLoginService（Phase 3-B）。

功能：编排 :class:`SmsProvider` + :class:`SmsLoginDriver`，把"虚拟号自动登录小红书"
拆成可观测的多个状态。**核心循环**：

    每个手机号最多发送 N 次（默认 N=2，即首发 + 1 次重发，间隔 3 分钟），
    全部发送都未收到验证码 → 释放本号、换新号；最多换 M 个号（默认 M=3）。
    最坏情况总耗时 ≈ M × N × 180s。

状态机：

    initializing
        ↓
    acquiring_phone   ←─────────────────┐  (循环换号入口)
        ↓                               │
    sending_sms       (fill_phone + click_send_sms)
        ↓                               │
    waiting_sms ─ 收到 ─→ submitting_sms ─→ extracting_cookies ─→ success
        ↓                               │
        │ 180s 超时                     │
        ↓                               │
    resending_sms      (click_resend_sms，倒计时归零后再点)
        ↓                               │
    waiting_sms ─ 收到 → submitting_sms ─→ ...
        ↓                               │
        │ 再 180s 超时                  │
        ↓                               │
    switching_phone   (释放本号) ───────┘
        ↓ 已尝试 M 个号
    error (SMS_TIMEOUT)

任意阶段错误 → ``error / cancelled / expired``，并记录 ``error_code / error_message``。

API 契约：

- :meth:`create_session(redmuse_user_id)`：异步启动后台 task；立即返回 session 元信息。
- :meth:`get_session(session_id)`：拉最新状态（前端轮询）。
- :meth:`cancel_session(session_id)`：取消正在进行的 session（关浏览器、释放号）。

session 仅保存在内存。重启后端会丢失；Phase 3 不需要持久化。
"""

from __future__ import annotations

import asyncio
import os
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Optional

from loguru import logger

from .phone_reservation_store import (
    PhoneReservation,
    PhoneReservationStore,
    get_phone_reservation_store,
)
from .sms_login_driver import CookieSnapshot, SmsLoginDriver
from .sms_provider import (
    PhonePurchase,
    SmsAuthError,
    SmsCancelledError,
    SmsCodeResult,
    SmsNoStockError,
    SmsProvider,
    SmsProviderError,
    SmsTimeoutError,
)


# ---------------------------------------------------------------------------
# 重试参数（env 可覆盖）
# ---------------------------------------------------------------------------

def _read_int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning(f"[sms_login] 非法 {name}={raw!r}，使用默认值 {default}")
        return default
    return max(minimum, value)


_RESEND_INTERVAL_SEC = _read_int_env("XHS_SMS_RESEND_INTERVAL_SEC", 180, minimum=30)
"""单次「获取/重新获取」后等多久（默认 3 分钟）。
小红书发送验证码的最小间隔是 3 分钟，所以 180s 是 hard floor。"""

_MAX_SENDS_PER_PHONE = _read_int_env("XHS_SMS_MAX_SENDS_PER_PHONE", 2, minimum=1)
"""同一个号最多按几次发送按钮（含首次）。默认 2，即首发 + 1 次重发。
2 次 × 180s = 6 分钟，覆盖小红书最大重发 cooldown。"""

_MAX_PHONES_PER_SESSION = _read_int_env(
    "XHS_SMS_MAX_PHONES_PER_SESSION", 3, minimum=1
)
"""一个 session 最多换几个号。最坏耗时 ≈ M × N × 180s。
默认 3 → 最坏 18 分钟；用户主动 cancel 会立即终止。"""

# 默认 TTL = 最坏完整循环 + 2 分钟余量给 cookie 抓取等收尾
_DEFAULT_SESSION_TTL_SEC = (
    _MAX_PHONES_PER_SESSION * _MAX_SENDS_PER_PHONE * _RESEND_INTERVAL_SEC + 120
)


class SmsLoginStatus(str, Enum):
    INITIALIZING = "initializing"
    ACQUIRING_PHONE = "acquiring_phone"
    SENDING_SMS = "sending_sms"
    WAITING_SMS = "waiting_sms"
    RESENDING_SMS = "resending_sms"
    SWITCHING_PHONE = "switching_phone"
    SUBMITTING_SMS = "submitting_sms"
    EXTRACTING_COOKIES = "extracting_cookies"
    BINDING = "binding"
    """已拿到 cookies，正在自动绑定到当前 RedMuse 用户（调 selfinfo + 落盘）。"""
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

    @property
    def is_terminal(self) -> bool:
        return self in (
            SmsLoginStatus.SUCCESS,
            SmsLoginStatus.ERROR,
            SmsLoginStatus.CANCELLED,
            SmsLoginStatus.EXPIRED,
        )


@dataclass
class SmsLoginSession:
    session_id: str
    redmuse_user_id: str
    status: SmsLoginStatus = SmsLoginStatus.INITIALIZING
    phone: Optional[str] = None
    phone_country: Optional[str] = None
    order_id: Optional[str] = None
    cookies_str: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    phone_reused: bool = False
    """True 表示本次会话复用了上一次失败的 reservation（未再调 acquire_phone）。"""
    phone_attempt: int = 0
    """当前是该 session 第几个手机号（1-based；0 表示尚未购号）。"""
    send_attempt: int = 0
    """当前手机号第几次按发送按钮（1-based；0 表示尚未发送）。"""
    bind_result: Optional[Dict[str, Any]] = None
    """SUCCESS 时：自动绑定成功后 binder 返回的 public dict
    （含 xhs_user_id / xhs_nickname / credential 等）。
    None 表示 service 没装 hook 或 bind 失败（此时 status=ERROR）。"""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
        + timedelta(seconds=_DEFAULT_SESSION_TTL_SEC)
    )
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def public_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "redmuse_user_id": self.redmuse_user_id,
            "status": self.status.value,
            "phone": _mask_phone(self.phone) if self.phone else None,
            "phone_country": self.phone_country,
            "order_id": self.order_id,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "phone_reused": self.phone_reused,
            "phone_attempt": self.phone_attempt,
            "send_attempt": self.send_attempt,
            "is_terminal": self.status.is_terminal,
            "bind_result": self.bind_result,
            "auto_bound": self.bind_result is not None,
            "cookies_ready": bool(self.cookies_str),
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


def _mask_phone(phone: str) -> str:
    if len(phone) <= 4:
        return phone
    return f"{phone[:2]}***{phone[-4:]}"


DriverFactory = Callable[[SmsLoginSession], Awaitable[SmsLoginDriver]]
"""依据 session 创建一个新的 driver；通常返回 PlaywrightSmsLoginDriver()。"""

LoginSuccessHook = Callable[
    [SmsLoginSession, str], Awaitable[Optional[Dict[str, Any]]]
]
"""``driver.wait_for_login_success`` 拿到 cookies 后立刻调的回调。

约定：
- 入参：当前 session + cookies_str
- 出参：成功时返回非空 dict（写入 ``session.bind_result``，前端直接展示）；
  返回 ``None`` 或抛异常 → service 把 session 标 ``ERROR + BIND_FAILED``。
- 用途：自动把 cookies 绑到 RedMuse 用户身份，省掉用户点 "绑定到当前账号" 的一步；
  同时提供「真登录态判别」—— guest cookies 进 selfinfo 会失败，hook 直接拒绝。
"""


class SmsLoginService:
    """有限状态机驱动的自动 SMS 登录会话管理器。"""

    def __init__(
        self,
        sms_provider: SmsProvider,
        driver_factory: DriverFactory,
        *,
        session_ttl_sec: Optional[int] = None,
        reservation_store: Optional[PhoneReservationStore] = None,
        resend_interval_sec: Optional[int] = None,
        max_sends_per_phone: Optional[int] = None,
        max_phones_per_session: Optional[int] = None,
        on_login_success: Optional[LoginSuccessHook] = None,
    ) -> None:
        self._provider = sms_provider
        self._driver_factory = driver_factory
        self._on_login_success = on_login_success
        # 重试参数：测试可显式覆盖，生产从 env 读默认
        self._resend_interval_sec = (
            resend_interval_sec
            if resend_interval_sec is not None
            else _RESEND_INTERVAL_SEC
        )
        self._max_sends_per_phone = (
            max_sends_per_phone
            if max_sends_per_phone is not None
            else _MAX_SENDS_PER_PHONE
        )
        self._max_phones_per_session = (
            max_phones_per_session
            if max_phones_per_session is not None
            else _MAX_PHONES_PER_SESSION
        )
        # TTL：未显式指定时根据上面 3 个参数推导
        self._session_ttl_sec = (
            session_ttl_sec
            if session_ttl_sec is not None
            else self._max_phones_per_session
            * self._max_sends_per_phone
            * self._resend_interval_sec
            + 120
        )
        # 测试可注入隔离的 store；生产走 ``get_phone_reservation_store()``
        self._reservation_store = reservation_store or get_phone_reservation_store()

        self._sessions: Dict[str, SmsLoginSession] = {}
        self._tasks: Dict[str, asyncio.Task[Any]] = {}
        self._drivers: Dict[str, SmsLoginDriver] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    async def create_session(self, redmuse_user_id: str) -> SmsLoginSession:
        if not redmuse_user_id:
            raise ValueError("redmuse_user_id 不能为空")
        session_id = "sms_" + secrets.token_hex(8)
        now = datetime.now(timezone.utc)
        session = SmsLoginSession(
            session_id=session_id,
            redmuse_user_id=redmuse_user_id,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(seconds=self._session_ttl_sec),
        )
        async with self._lock:
            self._sessions[session_id] = session
            task = asyncio.create_task(self._run_session(session))
            self._tasks[session_id] = task

        logger.info(
            f"[sms_login] 新建会话 {session_id} for redmuse_user_id={redmuse_user_id}"
        )
        return session

    async def get_session(self, session_id: str) -> Optional[SmsLoginSession]:
        async with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            return None
        # 自动过期（仅在还未终止时）
        if (
            not session.status.is_terminal
            and datetime.now(timezone.utc) >= session.expires_at
        ):
            await self._mark_terminal(session, SmsLoginStatus.EXPIRED, "SESSION_EXPIRED", "会话超时")
            await self._cleanup_driver(session_id)
        return session

    async def cancel_session(self, session_id: str) -> bool:
        async with self._lock:
            session = self._sessions.get(session_id)
        if not session:
            return False
        if session.status.is_terminal:
            return True
        await self._mark_terminal(
            session,
            SmsLoginStatus.CANCELLED,
            "SESSION_CANCELLED",
            "用户主动取消",
        )
        # 取消后台 task
        async with self._lock:
            task = self._tasks.get(session_id)
        if task and not task.done():
            task.cancel()
        await self._cleanup_driver(session_id)
        return True

    async def consume_cookies(self, session_id: str) -> Optional[str]:
        """成功的 session 取一次 cookies_str；取后清空避免重用。"""
        async with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.status != SmsLoginStatus.SUCCESS:
                return None
            cookies = session.cookies_str
            session.cookies_str = None
            session.updated_at = datetime.now(timezone.utc)
        return cookies

    # ------------------------------------------------------------------
    # 内部状态推进
    # ------------------------------------------------------------------

    async def _run_session(self, session: SmsLoginSession) -> None:
        try:
            driver = await self._driver_factory(session)
            async with self._lock:
                self._drivers[session.session_id] = driver

            await self._set_status(session, SmsLoginStatus.INITIALIZING)
            await driver.open_login_page()

            # 1-4. 循环换号 + 多次发送，直到收到验证码或耗尽预算
            sms = await self._acquire_phone_and_wait_sms(session, driver)

            # 5. 填验证码
            await self._set_status(session, SmsLoginStatus.SUBMITTING_SMS)
            await driver.fill_and_submit_sms(sms.code)

            # 6. 抓 cookie
            await self._set_status(session, SmsLoginStatus.EXTRACTING_COOKIES)
            snap: CookieSnapshot = await driver.wait_for_login_success()
            session.cookies_str = snap.cookies_str

            # 7. 自动绑定到 RedMuse 用户（如果挂了 hook）
            #    这样省掉用户手动点"绑定到当前账号"，并且 selfinfo 失败会
            #    立刻把 session 标 ERROR，避免假 SUCCESS 误导前端。
            if self._on_login_success is not None:
                await self._set_status(session, SmsLoginStatus.BINDING)
                try:
                    bind_result = await self._on_login_success(
                        session, snap.cookies_str
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        f"[sms_login] {session.session_id} 自动绑定 hook 异常: {exc}"
                    )
                    await self._mark_terminal(
                        session,
                        SmsLoginStatus.ERROR,
                        "BIND_FAILED",
                        f"自动绑定异常: {exc}",
                    )
                    return

                if not bind_result:
                    await self._mark_terminal(
                        session,
                        SmsLoginStatus.ERROR,
                        "BIND_FAILED",
                        "自动绑定失败：selfinfo 返回 guest 身份或解析失败",
                    )
                    return

                session.bind_result = bind_result
                # binder 已经把 cookies 落盘到 datas/users/<username>/，
                # 防止旧 /bind 接口或他处再次消费同一 cookies。
                session.cookies_str = None

            await self._mark_terminal(
                session, SmsLoginStatus.SUCCESS, None, "登录成功"
            )
            # 登录闭环成功 → cookies 已落地，号没意义，删 reservation
            self._reservation_store.delete(session.redmuse_user_id)
            logger.info(
                f"[sms_login] {session.session_id} 登录成功"
                f"{'（已自动绑定）' if session.bind_result else ''}"
            )

        except asyncio.CancelledError:
            # cancel_session 已经更新过状态，这里仅日志
            logger.info(f"[sms_login] {session.session_id} 已取消")
            raise
        except SmsTimeoutError as exc:
            await self._mark_terminal(
                session, SmsLoginStatus.ERROR, exc.code, str(exc)
            )
        except SmsCancelledError as exc:
            await self._mark_terminal(
                session, SmsLoginStatus.CANCELLED, exc.code, str(exc)
            )
        except SmsNoStockError as exc:
            await self._mark_terminal(
                session, SmsLoginStatus.ERROR, exc.code, str(exc)
            )
        except SmsProviderError as exc:
            await self._mark_terminal(
                session, SmsLoginStatus.ERROR, exc.code, str(exc)
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[sms_login] {session.session_id} 异常: {exc}")
            await self._mark_terminal(
                session, SmsLoginStatus.ERROR, "DRIVER_ERROR", str(exc)
            )
        finally:
            await self._cleanup_driver(session.session_id)

    async def _acquire_phone_and_wait_sms(
        self,
        session: SmsLoginSession,
        driver: SmsLoginDriver,
    ) -> SmsCodeResult:
        """循环换号 + 多次发送，直到收到验证码。

        语义：
        - 每个号最多按 ``self._max_sends_per_phone`` 次发送按钮（含首次）；
          每次按完等 ``self._resend_interval_sec`` 秒（默认 180s = 3 分钟）。
        - 同号 N 次都没收到验证码 → 释放该号，换新号；最多换
          ``self._max_phones_per_session`` 个号。
        - 中途用户调 ``cancel_session`` → ``asyncio.CancelledError`` 自然冒泡。

        全部预算耗尽仍未收到 → 抛 :class:`SmsTimeoutError` 让 ``_run_session``
        终态化为 ``ERROR``。
        """
        last_phone: Optional[str] = None
        for phone_attempt in range(1, self._max_phones_per_session + 1):
            # ---- 1. 购号 / 复用号 ----
            await self._set_status(session, SmsLoginStatus.ACQUIRING_PHONE)
            purchase, seen_codes = await self._acquire_or_reuse_phone(session)
            session.phone = purchase.phone
            session.phone_country = purchase.country_code
            session.order_id = purchase.order_id
            session.phone_attempt = phone_attempt
            await self._touch(session)
            logger.info(
                f"[sms_login] {session.session_id} 第 {phone_attempt}/"
                f"{self._max_phones_per_session} 个号 phone=…{purchase.phone[-4:]}"
            )

            # ---- 2. 填手机号（playwright fill 自带清空再填，幂等） ----
            await self._set_status(session, SmsLoginStatus.SENDING_SMS)
            await driver.fill_phone(
                country_code=purchase.country_code, phone=purchase.phone
            )
            last_phone = purchase.phone

            # ---- 3. 同号最多 N 次发送 ----
            sms_received: Optional[SmsCodeResult] = None
            for send_attempt in range(1, self._max_sends_per_phone + 1):
                if send_attempt == 1:
                    await self._set_status(session, SmsLoginStatus.SENDING_SMS)
                    await driver.click_send_sms()
                    logger.info(
                        f"[sms_login] {session.session_id} 首次发送 "
                        f"(phone={phone_attempt}, send={send_attempt})"
                    )
                else:
                    await self._set_status(session, SmsLoginStatus.RESENDING_SMS)
                    await driver.click_resend_sms()
                    logger.info(
                        f"[sms_login] {session.session_id} 重新发送 "
                        f"(phone={phone_attempt}, send={send_attempt}/"
                        f"{self._max_sends_per_phone})"
                    )
                session.send_attempt = send_attempt
                await self._touch(session)

                # 等待本轮验证码（180s）
                await self._set_status(session, SmsLoginStatus.WAITING_SMS)
                try:
                    sms_received = await self._provider.wait_sms_code(
                        purchase.order_id,
                        timeout_seconds=self._resend_interval_sec,
                        seen_codes=seen_codes,
                    )
                    break  # 收到了，跳出 send 循环
                except SmsTimeoutError:
                    logger.info(
                        f"[sms_login] {session.session_id} 第 {send_attempt}/"
                        f"{self._max_sends_per_phone} 次发送 "
                        f"{self._resend_interval_sec}s 后未收到，"
                        f"准备{'重发' if send_attempt < self._max_sends_per_phone else '换号'}"
                    )
                    # 如果还可以重发就继续 send 循环；否则跳出去 switch_phone
                    continue

            if sms_received is not None:
                # 收到验证码 → 标记本号已消费 + 返回
                self._reservation_store.mark_sms_received(
                    session.redmuse_user_id, code=sms_received.code
                )
                return sms_received

            # ---- 4. 同号 N 次都失败 → 释放本号、换下一个 ----
            if phone_attempt >= self._max_phones_per_session:
                break  # 不再尝试，下面抛 timeout
            await self._set_status(session, SmsLoginStatus.SWITCHING_PHONE)
            self._reservation_store.delete(session.redmuse_user_id)
            logger.warning(
                f"[sms_login] {session.session_id} 号 …{purchase.phone[-4:]} "
                f"共 {self._max_sends_per_phone} 次发送均超时，删除 reservation 后换新号 "
                f"(还剩 {self._max_phones_per_session - phone_attempt} 个号可尝试)"
            )

        # 所有号 × 所有发送都超时
        last_suffix = f"…{last_phone[-4:]}" if last_phone else "(未购到)"
        raise SmsTimeoutError(
            f"已尝试 {self._max_phones_per_session} 个号 × "
            f"{self._max_sends_per_phone} 次发送（每次等 "
            f"{self._resend_interval_sec}s），均未收到验证码 (最后号 {last_suffix})"
        )

    async def _acquire_or_reuse_phone(
        self, session: SmsLoginSession
    ) -> tuple[PhonePurchase, list[str]]:
        """决定是复用上次的虚拟号还是重新购号。

        返回 ``(purchase, seen_codes)``：
        - ``purchase``：本次会话使用的号（新购或复用）
        - ``seen_codes``：复用时上次已见过的验证码（给 ``wait_sms_code`` 过滤旧码）

        诊断日志：每次决策都会打 INFO 级日志说明为什么复用 / 为什么购号，
        方便联调时确认 sms_received / age / window 的真实状态。
        """
        window_sec = self._reservation_store.reuse_window_sec
        existing = self._reservation_store.get(session.redmuse_user_id)

        # 检查复用条件并打印明确原因
        if existing is None:
            logger.info(
                f"[sms_login] {session.session_id} 无历史 reservation，准备新购号"
            )
        else:
            reason = existing.reusable_reason(window_sec=window_sec)
            if existing.is_reusable(window_sec=window_sec):
                # **关键加固**：本地 sms_received 标记可能漏写（异步竞态 / 进程崩 /
                # 调用顺序），所以即使本地认为「未消费」，也必须先调一次 hero-sms
                # peek 接口确认 order 上是否已经有过验证码。这是 source-of-truth。
                if await self._reservation_already_consumed(existing, session):
                    # peek 已把 sms_received 写入 store，下面走新购号路径
                    logger.info(
                        f"[sms_login] {session.session_id} 跳过复用 "
                        f"(peek 发现 hero-sms 端已收过验证码)，准备新购号 "
                        f"phone=…{existing.phone[-4:]}"
                    )
                else:
                    # 复用分支：兜底校验 country_code，缺失时回退默认 HK
                    if not existing.country_code:
                        logger.warning(
                            f"[sms_login] {session.session_id} reservation "
                            f"country_code 缺失，回退默认 HK 以保证 driver 能切区号"
                        )
                        existing.country_code = "HK"
                        self._reservation_store.save(existing)

                    session.phone_reused = True
                    await self._touch(session)
                    logger.info(
                        f"[sms_login] {session.session_id} ♻ 复用 reservation: "
                        f"order_id={existing.order_id} phone=…{existing.phone[-4:]} "
                        f"country={existing.country_code} {reason}"
                    )
                    return existing.to_purchase(), list(existing.seen_codes)
            else:
                # 显式拒绝复用（本地标记 / 超窗口 / 数据残缺）→ 走新购号
                logger.info(
                    f"[sms_login] {session.session_id} 跳过复用 (原因：{reason})，"
                    f"准备新购号 phone=…{existing.phone[-4:]}"
                )

        # 不可复用 → 真扣费购号；新号写 reservation
        purchase = await self._provider.acquire_phone()
        if not purchase.country_code:
            # acquire_phone 极少返回空 country_code，但保险起见兜底
            logger.warning(
                f"[sms_login] {session.session_id} provider 未给 country_code，"
                f"按默认 HK 处理（driver 会用启发式去 852 前缀）"
            )
            purchase.country_code = "HK"
        reservation = PhoneReservation(
            redmuse_user_id=session.redmuse_user_id,
            order_id=purchase.order_id,
            phone=purchase.phone,
            country_code=purchase.country_code,
            raw=dict(purchase.raw or {}),
        )
        self._reservation_store.save(reservation)
        logger.info(
            f"[sms_login] {session.session_id} 已购新号 order_id={purchase.order_id} "
            f"phone=…{purchase.phone[-4:]} country={purchase.country_code} → reservation 落盘"
        )
        return purchase, []

    async def _reservation_already_consumed(
        self,
        existing: PhoneReservation,
        session: SmsLoginSession,
    ) -> bool:
        """复用前的健康检查：调一次 hero-sms peek 接口确认 order 上是否已经
        收到过任何验证码。若已收过 → 该号已被消费，必须放弃复用。

        异常处理策略：
        - SmsCancelledError：订单已被取消 → 删除 reservation，返回 True 触发新购
        - SmsAuthError：API key 失效，复用没意义，向上抛由 _run_session 终态化
        - 其它（网络抖动 / 5xx / 未知响应）：记 warning，**保守地**返回 True
          触发新购，避免基于不可信状态做错误复用决策
        """
        try:
            probe = await self._provider.peek_sms_code(existing.order_id)
        except SmsCancelledError as exc:
            logger.info(
                f"[sms_login] {session.session_id} peek 发现订单已取消 "
                f"order_id={existing.order_id}，删除 reservation 后新购 ({exc})"
            )
            self._reservation_store.delete(session.redmuse_user_id)
            return True
        except SmsAuthError:
            # API key 失效，让上层 _run_session 终态化为 ERROR
            raise
        except SmsProviderError as exc:
            logger.warning(
                f"[sms_login] {session.session_id} peek 失败 "
                f"order_id={existing.order_id} ({exc})；保守起见放弃复用"
            )
            return True

        if probe is not None:
            # hero-sms 端已存在验证码 → 该号已被消费，本地持久化以阻断后续复用
            self._reservation_store.mark_sms_received(
                session.redmuse_user_id, code=probe.code
            )
            return True
        return False

    async def _set_status(
        self, session: SmsLoginSession, status: SmsLoginStatus
    ) -> None:
        async with self._lock:
            session.status = status
            session.updated_at = datetime.now(timezone.utc)
        logger.debug(f"[sms_login] {session.session_id} -> {status.value}")

    async def _mark_terminal(
        self,
        session: SmsLoginSession,
        status: SmsLoginStatus,
        error_code: Optional[str],
        message: str,
    ) -> None:
        async with self._lock:
            if session.status.is_terminal:
                return
            session.status = status
            session.error_code = error_code if status != SmsLoginStatus.SUCCESS else None
            session.error_message = message if status != SmsLoginStatus.SUCCESS else None
            session.updated_at = datetime.now(timezone.utc)

    async def _touch(self, session: SmsLoginSession) -> None:
        async with self._lock:
            session.updated_at = datetime.now(timezone.utc)

    async def _cleanup_driver(self, session_id: str) -> None:
        async with self._lock:
            driver = self._drivers.pop(session_id, None)
            self._tasks.pop(session_id, None)
        if driver is None:
            return
        try:
            await driver.cleanup()
        except Exception as exc:
            logger.warning(f"[sms_login] {session_id} driver cleanup 异常: {exc}")


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------


_default_service: Optional[SmsLoginService] = None
_default_service_lock = threading.Lock()


def get_sms_login_service() -> Optional[SmsLoginService]:
    """启动时未配置 SMS_PROVIDER_API_KEY 则返回 None；
    路由层据此判断是否禁用入口。"""
    global _default_service
    if _default_service is not None:
        return _default_service
    with _default_service_lock:
        if _default_service is not None:
            return _default_service
        from .credential_binder import get_credential_binder
        from .hero_sms_provider import HeroSmsProvider
        from .playwright_sms_login_driver import PlaywrightSmsLoginDriver

        provider = HeroSmsProvider()
        if not getattr(provider, "_api_key", ""):
            return None

        async def _factory(_session: SmsLoginSession) -> SmsLoginDriver:
            return PlaywrightSmsLoginDriver()

        async def _auto_bind_hook(
            session: SmsLoginSession, cookies_str: str
        ) -> Optional[Dict[str, Any]]:
            """登录成功后立刻把 cookies 绑到 session.redmuse_user_id。

            返回 binder 的 ``to_dict()`` —— 含 xhs_user_id / xhs_nickname 等
            前端可以直接展示的字段；任何失败返回 None 让 service 标 ERROR。
            """
            binder = get_credential_binder()
            try:
                result = await binder.bind_with_cookies(
                    session.redmuse_user_id, cookies_str
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    f"[sms_login] auto-bind 调用 binder 异常: {exc}"
                )
                return None
            if not result.success:
                logger.warning(
                    f"[sms_login] auto-bind 失败 "
                    f"redmuse_user_id={session.redmuse_user_id} "
                    f"code={result.error_code} msg={result.error_message}"
                )
                return None
            logger.info(
                f"[sms_login] ✓ auto-bind 成功 "
                f"redmuse_user_id={session.redmuse_user_id} "
                f"xhs_user_id={result.xhs_user_id} "
                f"xhs_nickname={result.xhs_nickname}"
            )
            return result.to_dict()

        # 允许 env 覆盖虚拟号复用窗口（默认 20 分钟，PhoneReservationStore 内置）
        reuse_window = os.getenv("SMS_PHONE_REUSE_WINDOW_SEC", "").strip()
        store: Optional[PhoneReservationStore] = None
        if reuse_window:
            try:
                store = PhoneReservationStore(
                    reuse_window_sec=max(0, int(reuse_window))
                )
            except ValueError:
                logger.warning(
                    f"[sms_login] 非法 SMS_PHONE_REUSE_WINDOW_SEC={reuse_window!r}，使用默认值"
                )

        _default_service = SmsLoginService(
            provider,
            _factory,
            reservation_store=store,
            on_login_success=_auto_bind_hook,
        )
        return _default_service


def set_default_service(service: Optional[SmsLoginService]) -> None:
    """测试钩子：注入隔离的 service 实例。"""
    global _default_service
    _default_service = service
