"""
小红书扫码登录服务（API 方式）

使用小红书官方 API 实现扫码登录：
1. 获取初始 Cookie（包含 a1）
2. 调用创建二维码 API
3. 将 URL 转换为二维码图片
4. 轮询状态 API 检测登录结果
5. 登录成功后提取 Cookie

优势：速度快（2-3秒显示二维码），资源占用低，稳定性高
"""
import asyncio
import base64
import io
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

from loguru import logger

from .qrcode_session import QRCodeSession, QRLoginStatus

# 配置常量
QRCODE_TIMEOUT_SECONDS = 120        # 二维码有效期（秒）
MAX_CONCURRENT_SESSIONS = 5         # 最大并发会话数（API 方式资源消耗小，可以更多）
POLL_INTERVAL_SECONDS = 1.5         # 状态轮询间隔


class APIQRCodeLoginService:
    """
    小红书扫码登录服务（API 方式）

    使用官方 API 实现二维码登录，速度快、稳定性高。
    """

    _instance: Optional['APIQRCodeLoginService'] = None

    def __init__(self):
        self._sessions: Dict[str, QRCodeSession] = {}
        self._session_data: Dict[str, dict] = {}  # 存储 qr_id, code 等
        self._lock = asyncio.Lock()
        self._qrcode_available: Optional[bool] = None

    @classmethod
    def get_instance(cls) -> 'APIQRCodeLoginService':
        """单例模式获取服务实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def check_available(self, force_recheck: bool = False) -> bool:
        """
        检查扫码登录功能是否可用

        Args:
            force_recheck: 是否强制重新检测
        """
        if self._qrcode_available is not None and not force_recheck:
            return self._qrcode_available

        try:
            import qrcode
            self._qrcode_available = True
            logger.info("二维码生成库已就绪，API 扫码登录功能可用")
        except ImportError:
            self._qrcode_available = False
            logger.warning("qrcode 库未安装，请运行: pip install qrcode")

        return self._qrcode_available

    def reset_check(self):
        """重置可用性缓存"""
        self._qrcode_available = None
        logger.info("已重置 API 扫码登录可用性缓存")

    async def create_session(self, username: str) -> QRCodeSession:
        """
        创建新的扫码登录会话

        Args:
            username: 关联的用户名

        Returns:
            QRCodeSession 会话对象
        """
        if not await self.check_available():
            raise RuntimeError("扫码功能未启用，请安装依赖: pip install qrcode")

        async with self._lock:
            # 检查并发限制
            active_sessions = [s for s in self._sessions.values() if s.is_active]
            if len(active_sessions) >= MAX_CONCURRENT_SESSIONS:
                raise RuntimeError(f"当前扫码会话数已达上限 ({MAX_CONCURRENT_SESSIONS})")

            # 清理该用户的旧会话
            await self._cleanup_user_sessions(username)

            # 创建新会话
            session = QRCodeSession(username=username)
            self._sessions[session.session_id] = session

            # 启动后台任务：获取二维码
            asyncio.create_task(self._fetch_qrcode(session))

            logger.info(f"创建 API 扫码会话: {session.session_id} (用户: {username})")
            return session

    async def _fetch_qrcode(self, session: QRCodeSession):
        """获取二维码（通过 API）"""
        try:
            from apis.xhs_pc_apis import XHS_Apis

            xhs_api = XHS_Apis()

            # 步骤1：获取初始 Cookie
            logger.info(f"会话 {session.session_id}: 正在获取初始 Cookie...")
            success, msg, cookies_dict, cookies_str = XHS_Apis.get_initial_cookies()

            if not success:
                session.status = QRLoginStatus.ERROR
                session.error_message = f"获取初始 Cookie 失败: {msg}"
                logger.error(f"会话 {session.session_id}: {session.error_message}")
                return

            # 步骤2：创建二维码
            logger.info(f"会话 {session.session_id}: 正在创建二维码...")
            success, msg, qrcode_data = xhs_api.create_login_qrcode(cookies_str)

            if not success or not qrcode_data:
                session.status = QRLoginStatus.ERROR
                session.error_message = f"创建二维码失败: {msg}"
                logger.error(f"会话 {session.session_id}: {session.error_message}")
                return

            # 提取二维码数据
            qr_url = qrcode_data.get("url", "")
            qr_id = qrcode_data.get("qr_id", "")
            code = qrcode_data.get("code", "")

            if not qr_url:
                session.status = QRLoginStatus.ERROR
                session.error_message = "二维码 URL 为空"
                return

            # 保存会话数据（用于后续轮询）
            self._session_data[session.session_id] = {
                "qr_id": qr_id,
                "code": code,
                "cookies_str": cookies_str,
                "cookies_dict": cookies_dict,
            }

            # 步骤3：生成二维码图片
            qrcode_base64 = await self._generate_qrcode_image(qr_url)

            if not qrcode_base64:
                session.status = QRLoginStatus.ERROR
                session.error_message = "生成二维码图片失败"
                return

            # 更新会话状态
            session.qrcode_base64 = qrcode_base64
            session.status = QRLoginStatus.WAITING_SCAN
            session.expires_at = datetime.now() + timedelta(seconds=QRCODE_TIMEOUT_SECONDS)
            logger.success(f"会话 {session.session_id}: 二维码已生成，等待扫码")

            # 启动状态轮询
            asyncio.create_task(self._poll_login_status(session))

        except Exception as e:
            logger.error(f"会话 {session.session_id}: 获取二维码失败 - {e}")
            session.status = QRLoginStatus.ERROR
            session.error_message = f"获取二维码失败: {str(e)}"

    async def _generate_qrcode_image(self, url: str) -> Optional[str]:
        """将 URL 转换为二维码图片 base64"""
        try:
            import qrcode
            from PIL import Image

            # 创建二维码
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=2,
            )
            qr.add_data(url)
            qr.make(fit=True)

            # 生成图片
            img = qr.make_image(fill_color="black", back_color="white")

            # 转换为 base64
            buffer = io.BytesIO()
            img.save(buffer, format='PNG')
            buffer.seek(0)
            return base64.b64encode(buffer.read()).decode('utf-8')

        except Exception as e:
            logger.error(f"生成二维码图片失败: {e}")
            return None

    async def _poll_login_status(self, session: QRCodeSession):
        """轮询登录状态"""
        from apis.xhs_pc_apis import XHS_Apis

        xhs_api = XHS_Apis()
        session_data = self._session_data.get(session.session_id, {})

        qr_id = session_data.get("qr_id", "")
        code = session_data.get("code", "")
        cookies_str = session_data.get("cookies_str", "")
        cookies_dict = session_data.get("cookies_dict", {})

        start_time = datetime.now()

        while session.is_active:
            # 检查超时
            elapsed = (datetime.now() - start_time).total_seconds()
            if elapsed > QRCODE_TIMEOUT_SECONDS:
                session.status = QRLoginStatus.EXPIRED
                session.error_message = "二维码已过期，请重新获取"
                logger.info(f"会话 {session.session_id}: 二维码已过期")
                break

            try:
                # 调用状态检查 API
                success, msg, status_data, new_cookies = xhs_api.check_qrcode_status(
                    qr_id, code, cookies_str
                )

                if not success:
                    logger.warning(f"会话 {session.session_id}: 检查状态失败 - {msg}")
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                    continue

                code_status = status_data.get("code_status", -1)

                # 状态码说明：
                # 0: 等待扫码
                # 1: 已扫码，等待确认
                # 2: 登录成功
                # 3: 二维码过期

                if code_status == 0:
                    # 等待扫码
                    pass

                elif code_status == 1:
                    # 已扫码，等待确认
                    if session.status != QRLoginStatus.SCANNED:
                        session.status = QRLoginStatus.SCANNED
                        logger.info(f"会话 {session.session_id}: 用户已扫码，等待确认")

                elif code_status == 2:
                    # 登录成功
                    session.status = QRLoginStatus.CONFIRMED
                    logger.info(f"会话 {session.session_id}: 登录成功，正在提取 Cookie")

                    # 合并 Cookie
                    final_cookies = self._merge_cookies(cookies_dict, new_cookies or {})
                    session.cookies_str = '; '.join([f"{k}={v}" for k, v in final_cookies.items()])

                    # 保存到用户配置
                    await self._save_cookie_to_user(session)

                    session.status = QRLoginStatus.SUCCESS
                    logger.success(f"会话 {session.session_id}: Cookie 已保存")
                    break

                elif code_status == 3:
                    # 二维码过期
                    session.status = QRLoginStatus.EXPIRED
                    session.error_message = "二维码已过期，请重新获取"
                    logger.info(f"会话 {session.session_id}: 二维码已过期")
                    break

            except Exception as e:
                logger.warning(f"会话 {session.session_id}: 轮询出错 - {e}")

            await asyncio.sleep(POLL_INTERVAL_SECONDS)

        # 清理会话数据
        await self._cleanup_session(session.session_id, remove_from_sessions=False)

        # 延迟删除会话对象
        await asyncio.sleep(5)
        if session.session_id in self._sessions:
            del self._sessions[session.session_id]
            logger.debug(f"会话 {session.session_id}: 延迟清理完成")

    def _merge_cookies(self, initial: dict, new: dict) -> dict:
        """合并 Cookie，新 Cookie 优先"""
        result = dict(initial)
        result.update(new)
        return result

    async def _save_cookie_to_user(self, session: QRCodeSession):
        """保存 Cookie 到用户配置"""
        try:
            from viral_agent.services.user_data_service import get_user_data_service
            user_data = get_user_data_service(session.username)
            user_data.save_cookie(session.cookies_str)
            logger.success(f"会话 {session.session_id}: Cookie 已保存到用户配置")
        except Exception as e:
            logger.error(f"会话 {session.session_id}: 保存 Cookie 失败 - {e}")

    async def get_session(self, session_id: str) -> Optional[QRCodeSession]:
        """获取会话状态"""
        return self._sessions.get(session_id)

    async def cancel_session(self, session_id: str):
        """取消会话"""
        session = self._sessions.get(session_id)
        if session:
            session.status = QRLoginStatus.CANCELLED
            logger.info(f"会话 {session_id}: 已取消")
            await self._cleanup_session(session_id)

    async def _cleanup_session(self, session_id: str, remove_from_sessions: bool = True):
        """清理会话资源"""
        try:
            # 清理会话数据
            if session_id in self._session_data:
                del self._session_data[session_id]

            # 从会话字典中移除
            if remove_from_sessions and session_id in self._sessions:
                del self._sessions[session_id]
                logger.debug(f"会话 {session_id}: 已从会话列表移除")

            logger.debug(f"会话 {session_id}: 资源已清理")
        except Exception as e:
            logger.warning(f"清理会话 {session_id} 时出错: {e}")

    async def _cleanup_user_sessions(self, username: str):
        """清理用户的所有旧会话"""
        old_sessions = [
            sid for sid, s in self._sessions.items()
            if s.username == username and s.is_active
        ]
        for sid in old_sessions:
            await self.cancel_session(sid)

    async def cleanup_all(self):
        """清理所有会话资源"""
        session_ids = list(self._sessions.keys())
        for sid in session_ids:
            await self._cleanup_session(sid)
        self._sessions.clear()
        self._session_data.clear()
        logger.info("API 扫码登录服务已清理所有资源")


# 全局服务实例获取函数
def get_api_qrcode_login_service() -> APIQRCodeLoginService:
    """获取 API 扫码登录服务单例"""
    return APIQRCodeLoginService.get_instance()
