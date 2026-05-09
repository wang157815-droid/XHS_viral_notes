"""RedMuse 用户与小红书数据源凭据的桥接层（Phase 1）。

设计目标：
- 把"RedMuse 系统身份"（``backend.app.services.redmuse_auth``）与
  "XHS 数据源凭据"（本模块）彻底解耦。
- 任何需要发起小红书抓取的代码路径，统一通过 :class:`XhsCredentialResolver`
  按 RedMuse ``user_id`` 取 cookie，杜绝隐式回退到 admin 串号。
- Cookie 物理文件仍保留在 ``datas/users/<dir>/cookies.json``，旧的扫码登录
  流程不受影响；只是在它之上增加一层 ``redmuse_user_id → cookies_path`` 的
  逻辑映射。

Phase 1 暂不接管"自动重新授权"，只提供：
- 数据存取（store）
- 解析当前 RedMuse 用户应该用哪份 cookie 字符串（resolver）
- 健康状态查询（health）

Phase 2 起将由 XhsAuthAgent 接管 expiry → re-auth 闭环。
"""

from .credential_store import (
    XhsCredential,
    XhsCredentialStore,
    get_credential_store,
)
from .credential_resolver import (
    XhsCredentialResolver,
    get_credential_resolver,
)
from .credential_health import (
    XhsCredentialHealth,
    XhsCredentialHealthChecker,
    get_credential_health_checker,
)
from .credential_binder import (
    BindResult,
    XhsCredentialBinder,
    get_credential_binder,
)
from .sms_provider import (
    PhonePurchase,
    SmsAuthError,
    SmsCancelledError,
    SmsCodeResult,
    SmsNoStockError,
    SmsProvider,
    SmsProviderError,
    SmsResponseError,
    SmsTimeoutError,
    SmsTransportError,
)
from .hero_sms_provider import HeroSmsProvider

__all__ = [
    "XhsCredential",
    "XhsCredentialStore",
    "get_credential_store",
    "XhsCredentialResolver",
    "get_credential_resolver",
    "XhsCredentialHealth",
    "XhsCredentialHealthChecker",
    "get_credential_health_checker",
    "BindResult",
    "XhsCredentialBinder",
    "get_credential_binder",
    "PhonePurchase",
    "SmsCodeResult",
    "SmsProvider",
    "SmsProviderError",
    "SmsAuthError",
    "SmsCancelledError",
    "SmsNoStockError",
    "SmsResponseError",
    "SmsTimeoutError",
    "SmsTransportError",
    "HeroSmsProvider",
]
