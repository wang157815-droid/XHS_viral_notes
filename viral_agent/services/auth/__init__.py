"""
扫码登录服务模块

提供小红书扫码登录功能，自动获取Cookie。

使用 Playwright 浏览器自动化（优化版）：
- 预启动浏览器减少等待时间
- 复用浏览器实例
- 快速页面加载策略
"""
from .qrcode_session import QRCodeSession, QRLoginStatus
from .qrcode_login_service import QRCodeLoginService, get_qrcode_login_service

__all__ = [
    "QRCodeSession",
    "QRLoginStatus",
    "QRCodeLoginService",
    "get_qrcode_login_service",
]
