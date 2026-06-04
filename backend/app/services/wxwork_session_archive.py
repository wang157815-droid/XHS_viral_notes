"""
企业微信会话内容存档服务

加密流程（企微 → 我们）：
  企微用「我们上传的 RSA 公钥」加密一个随机 AES 密钥 → encrypt_random_key
  企微用该随机 AES 密钥加密聊天内容 → encrypt_chat_msg

解密流程（我们 → 明文）：
  Step 1: RSA PKCS1v15 解密 encrypt_random_key → 32 字节随机密钥
  Step 2: 将随机密钥 base64 编码后传给 SDK DecryptData → 明文 JSON

依赖：
  - 企业微信官方 C++ SDK（libWeWorkFinanceSdk_C.so）
    下载：企微开发者中心 → 会话内容存档 → SDK 下载
    放到 WXWORK_FINANCE_SDK_PATH 指定的路径（默认 /app/WeWorkFinanceSdk_C.so）
  - Python cryptography 库（pip install cryptography）
"""
import asyncio
import base64
import ctypes
import json
import sys
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from loguru import logger

from ..core.config import settings


# ------------------------------------------------------------------ #
# 异常
# ------------------------------------------------------------------ #

class FinanceSDKError(Exception):
    """SDK 调用或解密失败"""


# ------------------------------------------------------------------ #
# C++ SDK ctypes 封装
# ------------------------------------------------------------------ #

class WXWorkFinanceSDK:
    """
    企业微信会话内容存档 C++ SDK 的 Python ctypes 封装。

    SDK 函数签名参考官方文档：
      https://developer.work.weixin.qq.com/document/path/91774
    """

    def __init__(self, sdk_path: str, corp_id: str, finance_secret: str):
        sdk_file = Path(sdk_path)
        if not sdk_file.exists():
            raise FinanceSDKError(
                f"SDK 文件不存在: {sdk_path}\n"
                "请从企微开发者中心下载 libWeWorkFinanceSdk_C.so 并放到该路径"
            )

        self._lib = ctypes.CDLL(str(sdk_file))
        self._setup_func_types()

        self._sdk_ptr = self._lib.NewSdk()
        if not self._sdk_ptr:
            raise FinanceSDKError("NewSdk() 返回空指针，SDK 可能已损坏")

        ret = self._lib.Init(
            ctypes.c_void_p(self._sdk_ptr),
            corp_id.encode("utf-8"),
            finance_secret.encode("utf-8"),
        )
        if ret != 0:
            raise FinanceSDKError(f"SDK Init 失败，错误码: {ret}，请检查 CorpID / Finance Secret")

        logger.info("[wxwork-session] Finance SDK 初始化成功")

    # -------------------------------------------------------------- #
    # 类型声明
    # -------------------------------------------------------------- #

    def _setup_func_types(self):
        lib = self._lib

        lib.NewSdk.restype = ctypes.c_void_p
        lib.NewSdk.argtypes = []

        lib.Init.restype = ctypes.c_int
        lib.Init.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]

        lib.NewSlice.restype = ctypes.c_void_p
        lib.NewSlice.argtypes = []

        lib.FreeSlice.restype = None
        lib.FreeSlice.argtypes = [ctypes.c_void_p]

        lib.GetContentFromSlice.restype = ctypes.c_char_p
        lib.GetContentFromSlice.argtypes = [ctypes.c_void_p]

        lib.GetLenFromSlice.restype = ctypes.c_uint
        lib.GetLenFromSlice.argtypes = [ctypes.c_void_p]

        lib.GetChatData.restype = ctypes.c_int
        lib.GetChatData.argtypes = [
            ctypes.c_void_p,     # sdk
            ctypes.c_ulonglong,  # seq
            ctypes.c_uint,       # limit
            ctypes.c_char_p,     # proxy
            ctypes.c_char_p,     # passwd
            ctypes.c_int,        # timeout
            ctypes.c_void_p,     # chatDatas (Slice_t*)
        ]

        lib.DecryptData.restype = ctypes.c_int
        lib.DecryptData.argtypes = [
            ctypes.c_char_p,  # encrypt_key（RSA解密后再base64编码的字符串）
            ctypes.c_char_p,  # encrypt_msg（encrypt_chat_msg 原始值）
            ctypes.c_void_p,  # msg (Slice_t*)
        ]

        lib.DestroySdk.restype = None
        lib.DestroySdk.argtypes = [ctypes.c_void_p]

    # -------------------------------------------------------------- #
    # 公开接口
    # -------------------------------------------------------------- #

    def get_chat_data(
        self,
        seq: int = 0,
        limit: int = 100,
        proxy: str = "",
        passwd: str = "",
        timeout: int = 30,
    ) -> dict:
        """
        拉取加密会话记录。

        seq:   从此序号之后开始拉取（首次填 0，之后填上次返回的最大 seq）
        limit: 每次最多拉取条数，上限 1000
        返回:  企微原始 JSON（含 chatdata 加密数组）
        """
        slice_ptr = self._lib.NewSlice()
        try:
            ret = self._lib.GetChatData(
                ctypes.c_void_p(self._sdk_ptr),
                ctypes.c_ulonglong(seq),
                ctypes.c_uint(limit),
                proxy.encode("utf-8") if proxy else None,   # NULL = 不使用代理
                passwd.encode("utf-8") if passwd else None,  # NULL = 无密码
                ctypes.c_int(timeout),
                ctypes.c_void_p(slice_ptr),
            )
            if ret != 0:
                raise FinanceSDKError(f"GetChatData 失败，错误码: {ret}")
            raw = self._lib.GetContentFromSlice(ctypes.c_void_p(slice_ptr))
            return json.loads(raw.decode("utf-8"))
        finally:
            self._lib.FreeSlice(ctypes.c_void_p(slice_ptr))

    def decrypt_msg(self, aes_key_b64: str, encrypt_chat_msg: str) -> dict:
        """
        解密单条会话内容。

        aes_key_b64:      RSA 解密后再 base64 编码的 AES 密钥字符串
                          （官方流程：raw_bytes → base64.b64encode → str）
        encrypt_chat_msg: GetChatData 返回的 encrypt_chat_msg 字段原始值
        返回:             明文消息 JSON（含 from/tolist/msgtype/text 等字段）
        """
        slice_ptr = self._lib.NewSlice()
        try:
            ret = self._lib.DecryptData(
                aes_key_b64.encode("utf-8"),
                encrypt_chat_msg.encode("utf-8"),
                ctypes.c_void_p(slice_ptr),
            )
            if ret != 0:
                raise FinanceSDKError(f"DecryptData 失败，错误码: {ret}")
            raw = self._lib.GetContentFromSlice(ctypes.c_void_p(slice_ptr))
            return json.loads(raw.decode("utf-8"))
        finally:
            self._lib.FreeSlice(ctypes.c_void_p(slice_ptr))

    def __del__(self):
        if hasattr(self, "_sdk_ptr") and self._sdk_ptr and hasattr(self, "_lib"):
            try:
                self._lib.DestroySdk(ctypes.c_void_p(self._sdk_ptr))
            except Exception:
                pass


@lru_cache(maxsize=1)
def _get_sdk() -> WXWorkFinanceSDK:
    """惰性单例，首次调用时初始化 SDK"""
    return WXWorkFinanceSDK(
        sdk_path=settings.wxwork_finance_sdk_path,
        corp_id=settings.wxwork_corp_id,
        finance_secret=settings.wxwork_finance_secret,
    )


# ------------------------------------------------------------------ #
# RSA 解密工具
# ------------------------------------------------------------------ #

def _load_private_key_pem() -> bytes:
    """从文件路径或环境变量中读取 RSA 私钥 PEM 内容"""
    path = settings.wxwork_rsa_private_key_path
    content = settings.wxwork_rsa_private_key

    if path and Path(path).exists():
        return Path(path).read_bytes()
    if content:
        return content.replace("\\n", "\n").encode("utf-8")
    raise ValueError(
        "RSA 私钥未配置：请设置 WXWORK_RSA_PRIVATE_KEY_PATH（文件路径）"
        " 或 WXWORK_RSA_PRIVATE_KEY（PEM 内容）"
    )


def _rsa_decrypt_random_key(encrypt_random_key_b64: str) -> str:
    """
    用 RSA 私钥（PKCS1v15）解密 encrypt_random_key，返回 base64 编码的 AES 密钥字符串。

    官方正确流程：
      a) base64 decode encrypt_random_key → 密文字节
      b) RSA PKCS1v15 私钥解密 → 原始 AES 密钥字节
      c) 对原始字节再做 base64 编码 → 字符串（供 SDK DecryptData 使用）

    注意：DecryptData 的第一个参数是 char*，期望 base64 字符串，
    直接传裸字节会因含 \x00 导致 free(): invalid pointer 崩溃。
    """
    pem = _load_private_key_pem()
    private_key = serialization.load_pem_private_key(
        pem, password=None, backend=default_backend()
    )
    encrypted_bytes = base64.b64decode(encrypt_random_key_b64)
    raw_aes_key = private_key.decrypt(encrypted_bytes, padding.PKCS1v15())
    return base64.b64encode(raw_aes_key).decode("utf-8")


# ------------------------------------------------------------------ #
# 主入口：拉取 + 批量解密
# ------------------------------------------------------------------ #

def fetch_and_decrypt(seq: int = 0, limit: int = 100) -> tuple[list[dict], int]:
    """
    拉取并解密一批会话记录。

    返回 (messages, max_seq)：
      messages: 解密后的明文消息列表
      max_seq:  本批次最大 seq（用于下次分页）
    """
    sdk = _get_sdk()
    result = sdk.get_chat_data(seq=seq, limit=limit)

    if result.get("errcode", 0) != 0:
        raise FinanceSDKError(
            f"拉取失败 errcode={result.get('errcode')} errmsg={result.get('errmsg')}"
        )

    messages: list[dict] = []
    max_seq = seq

    for item in result.get("chatdata", []):
        item_seq = item.get("seq", 0)
        if item_seq > max_seq:
            max_seq = item_seq
        try:
            aes_key_b64 = _rsa_decrypt_random_key(item["encrypt_random_key"])
            msg = sdk.decrypt_msg(aes_key_b64, item["encrypt_chat_msg"])
            msg["_seq"] = item_seq
            msg["_msgid"] = item.get("msgid", "")
            messages.append(msg)
        except Exception as exc:
            logger.error(
                "[wxwork-session] 解密失败 seq={} msgid={}: {}",
                item_seq, item.get("msgid"), exc,
            )

    logger.info(
        "[wxwork-session] 拉取 {} 条，解密成功 {} 条，max_seq={}",
        len(result.get("chatdata", [])), len(messages), max_seq,
    )
    return messages, max_seq


# ------------------------------------------------------------------ #
# 进程隔离封装：防止 SDK SIGSEGV 崩溃污染主进程
# ------------------------------------------------------------------ #

async def safe_fetch_and_decrypt(
    seq: int = 0,
    limit: int = 100,
    timeout: int = 30,
) -> tuple[list[dict], int]:
    """
    在独立子进程中调用 Finance SDK，安全获取并解密会话记录。

    即使 SDK 因 SIGSEGV / free(): invalid pointer 崩溃，
    也只会杀死子进程，主应用进程不受影响。

    返回 (messages, max_seq)，与 fetch_and_decrypt 完全相同的语义。
    """
    worker_module = str(Path(__file__).parent / "sdk_worker.py")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, worker_module, str(seq), str(limit),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise FinanceSDKError(f"Finance SDK 子进程超时（{timeout}s）")

    if proc.returncode not in (0, None):
        err_text = stderr.decode("utf-8", errors="replace").strip()
        raise FinanceSDKError(
            f"Finance SDK 子进程崩溃 (exit={proc.returncode}): {err_text[:300]}"
        )

    raw = stdout.decode("utf-8").strip()
    if not raw:
        raise FinanceSDKError("Finance SDK 子进程无输出")

    result = json.loads(raw)
    if not result.get("ok"):
        raise FinanceSDKError(result.get("error", "未知错误"))

    return result["messages"], result["max_seq"]
