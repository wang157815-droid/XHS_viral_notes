"""
扫码登录会话模型

定义扫码登录的状态枚举和会话数据结构。
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import uuid


class QRLoginStatus(Enum):
    """扫码登录状态枚举"""
    INITIALIZING = "initializing"      # 正在初始化浏览器
    WAITING_SCAN = "waiting_scan"      # 等待用户扫码
    NEED_SMS_CODE = "need_sms_code"    # 需要输入短信验证码
    SCANNED = "scanned"                # 已扫码，等待确认
    CONFIRMED = "confirmed"            # 用户已确认，正在获取Cookie
    SUCCESS = "success"                # 登录成功，Cookie已保存
    EXPIRED = "expired"                # 二维码过期
    CANCELLED = "cancelled"            # 用户取消
    ERROR = "error"                    # 发生错误


@dataclass
class QRCodeSession:
    """扫码登录会话"""
    session_id: str = field(default_factory=lambda: f"qr_{uuid.uuid4().hex[:12]}")
    username: str = ""                 # 关联的用户名
    status: QRLoginStatus = QRLoginStatus.INITIALIZING
    qrcode_base64: Optional[str] = None  # 二维码图片 base64
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None  # 二维码过期时间（通常2分钟）
    error_message: Optional[str] = None
    cookies_str: Optional[str] = None  # 提取到的Cookie字符串

    @property
    def is_active(self) -> bool:
        """会话是否仍在进行中"""
        return self.status in (
            QRLoginStatus.INITIALIZING,
            QRLoginStatus.WAITING_SCAN,
            QRLoginStatus.NEED_SMS_CODE,
            QRLoginStatus.SCANNED,
            QRLoginStatus.CONFIRMED
        )

    @property
    def is_completed(self) -> bool:
        """会话是否已完成（成功或失败）"""
        return self.status in (
            QRLoginStatus.SUCCESS,
            QRLoginStatus.EXPIRED,
            QRLoginStatus.CANCELLED,
            QRLoginStatus.ERROR
        )

    def to_dict(self) -> dict:
        """转换为字典，用于API响应"""
        return {
            "session_id": self.session_id,
            "username": self.username,
            "status": self.status.value,
            "qrcode_base64": self.qrcode_base64,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "error_message": self.error_message,
            "is_active": self.is_active,
            "is_completed": self.is_completed,
        }
