"""SmsLoginService（Phase 3-B）。

功能：编排 :class:`SmsProvider` + :class:`SmsLoginDriver`，把"虚拟号自动登录小红书"
拆成可观测的 8 个状态：

    initializing
        ↓
    acquiring_phone   (调 SmsProvider.acquire_phone)
        ↓
    sending_sms       (driver.fill_phone + driver.click_send_sms)
        ↓
    waiting_sms       (调 SmsProvider.wait_sms_code)
        ↓
    submitting_sms    (driver.fill_and_submit_sms)
        ↓
    extracting_cookies (driver.wait_for_login_success)
        ↓
    success           (cookies_str 就绪，可交给 binder)

任意阶段错误 → ``error / cancelled / expired``，并记录 ``error_code / error_message``。

API 契约：

- :meth:`create_session(redmuse_user_id)`：异步启动后台 task；立即返回 session 元信息。
- :meth:`get_session(session_id)`：拉最新状态（前端轮询）。
- :meth:`cancel_session(session_id)`：取消正在进行的 session（关浏览器、释放号）。

session 仅保存在内存。重启后端会丢失；Phase 3 不需要持久化。
"""

from __future__ import annotations

import asyncio
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
    SmsCancelledError,
    SmsCodeResult,
    SmsNoStockError,
    SmsProvider,
    SmsProviderError,
    SmsTimeoutError,
)


_DEFAULT_SESSION_TTL_SEC = 8 * 60  # 8 分钟，覆盖号码 + SMS 等待 + 最后跳转


class SmsLoginStatus(str, Enum):
    INITIALIZING = "initializing"
    ACQUIRING_PHONE = "acquiring_phone"
    SENDING_SMS = "sending_sms"
    WAITING_SMS = "waiting_sms"
    SUBMITTING_SMS = "submitting_sms"
    EXTRACTING_COOKIES = "extracting_cookies"
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
            "is_terminal": self.status.is_terminal,
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


class SmsLoginService:
    """有限状态机驱动的自动 SMS 登录会话管理器。"""

    def __init__(
        self,
        sms_provider: SmsProvider,
        driver_factory: DriverFactory,
        *,
        session_ttl_sec: int = _DEFAULT_SESSION_TTL_SEC,
        reservation_store: Optional[PhoneReservationStore] = None,
    ) -> None:
        self._provider = sms_provider
        self._driver_factory = driver_factory
        self._session_ttl_sec = session_ttl_sec
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

            # 1. 购号 / 复用号
            await self._set_status(session, SmsLoginStatus.ACQUIRING_PHONE)
            purchase, seen_codes = await self._acquire_or_reuse_phone(session)
            session.phone = purchase.phone
            session.phone_country = purchase.country_code
            session.order_id = purchase.order_id
            await self._touch(session)

            # 2. 填手机号 + 发送验证码
            await self._set_status(session, SmsLoginStatus.SENDING_SMS)
            await driver.fill_phone(
                country_code=purchase.country_code, phone=purchase.phone
            )
            await driver.click_send_sms()

            # 3. 等验证码（复用号时把历史已见过的码当作旧码过滤）
            await self._set_status(session, SmsLoginStatus.WAITING_SMS)
            sms: SmsCodeResult = await self._provider.wait_sms_code(
                purchase.order_id, seen_codes=seen_codes
            )
            # 一旦真收到验证码 → 该号永久不可复用
            self._reservation_store.mark_sms_received(
                session.redmuse_user_id, code=sms.code
            )

            # 4. 填验证码
            await self._set_status(session, SmsLoginStatus.SUBMITTING_SMS)
            await driver.fill_and_submit_sms(sms.code)

            # 5. 抓 cookie
            await self._set_status(session, SmsLoginStatus.EXTRACTING_COOKIES)
            snap: CookieSnapshot = await driver.wait_for_login_success()
            session.cookies_str = snap.cookies_str

            await self._mark_terminal(
                session, SmsLoginStatus.SUCCESS, None, "登录成功"
            )
            # 登录闭环成功 → cookies 已落地，号没意义，删 reservation
            self._reservation_store.delete(session.redmuse_user_id)
            logger.info(f"[sms_login] {session.session_id} 登录成功")

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
            # 显式拒绝复用 → 走新购号
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
        import os
        from .hero_sms_provider import HeroSmsProvider
        from .playwright_sms_login_driver import PlaywrightSmsLoginDriver

        provider = HeroSmsProvider()
        if not getattr(provider, "_api_key", ""):
            return None

        async def _factory(_session: SmsLoginSession) -> SmsLoginDriver:
            return PlaywrightSmsLoginDriver()

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
            provider, _factory, reservation_store=store
        )
        return _default_service


def set_default_service(service: Optional[SmsLoginService]) -> None:
    """测试钩子：注入隔离的 service 实例。"""
    global _default_service
    _default_service = service
