"""
企业微信消息加解密工具

企业微信使用 AES-256-CBC 对回调消息加密，block_size=32（非标准16），
EncodingAESKey 是 43 位 Base64 编码，解码后得到 32 字节密钥。
"""
import base64
import hashlib
import os
import struct
import time

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from loguru import logger


class WXBizMsgCrypt:
    """企业微信消息加解密"""

    BLOCK_SIZE = 32  # 企业微信固定 32 字节块

    def __init__(self, token: str, encoding_aes_key: str, corp_id: str):
        self.token = token
        # EncodingAESKey 是 43 位，末尾补 '=' 后 base64 解码得 32 字节密钥
        self.key = base64.b64decode(encoding_aes_key + "=")
        self.corp_id = corp_id

    # ------------------------------------------------------------------ #
    # 签名
    # ------------------------------------------------------------------ #

    def _sha1(self, *args: str) -> str:
        items = sorted(args)
        return hashlib.sha1("".join(items).encode("utf-8")).hexdigest()

    def verify_signature(self, signature: str, timestamp: str, nonce: str, encrypt_msg: str = "") -> bool:
        expected = self._sha1(self.token, timestamp, nonce, encrypt_msg)
        ok = expected == signature
        if not ok:
            logger.warning("[wxwork] 签名校验失败 expected={} got={}", expected, signature)
        return ok

    def make_signature(self, timestamp: str, nonce: str, encrypt_msg: str) -> str:
        return self._sha1(self.token, timestamp, nonce, encrypt_msg)

    # ------------------------------------------------------------------ #
    # 解密
    # ------------------------------------------------------------------ #

    def decrypt(self, encrypt_msg: str) -> str:
        """解密企业微信加密字段，返回原始 XML 字符串"""
        cipher_bytes = base64.b64decode(encrypt_msg)
        cipher = Cipher(
            algorithms.AES(self.key),
            modes.CBC(self.key[:16]),
            backend=default_backend(),
        )
        decryptor = cipher.decryptor()
        plain = decryptor.update(cipher_bytes) + decryptor.finalize()

        # 去除 PKCS7 填充
        pad_len = plain[-1]
        plain = plain[:-pad_len]

        # 前 16 字节为随机串，接 4 字节消息长度（大端），再是消息体，最后是 CorpID
        msg_len = struct.unpack(">I", plain[16:20])[0]
        xml_msg = plain[20 : 20 + msg_len].decode("utf-8")
        return xml_msg

    # ------------------------------------------------------------------ #
    # 加密
    # ------------------------------------------------------------------ #

    def encrypt(self, reply_msg: str) -> str:
        """将回复消息加密，返回 base64 字符串"""
        random_bytes = os.urandom(16)
        msg_bytes = reply_msg.encode("utf-8")
        corp_bytes = self.corp_id.encode("utf-8")
        msg_len_bytes = struct.pack(">I", len(msg_bytes))

        plain = random_bytes + msg_len_bytes + msg_bytes + corp_bytes

        # PKCS7 填充到 32 字节块
        pad_len = self.BLOCK_SIZE - (len(plain) % self.BLOCK_SIZE)
        plain += bytes([pad_len] * pad_len)

        cipher = Cipher(
            algorithms.AES(self.key),
            modes.CBC(self.key[:16]),
            backend=default_backend(),
        )
        encryptor = cipher.encryptor()
        cipher_bytes = encryptor.update(plain) + encryptor.finalize()
        return base64.b64encode(cipher_bytes).decode("utf-8")

    def build_encrypted_reply(self, reply_xml: str) -> str:
        """加密回复 XML，构造企业微信要求的完整响应 XML"""
        timestamp = str(int(time.time()))
        nonce = os.urandom(8).hex()
        encrypted = self.encrypt(reply_xml)
        signature = self.make_signature(timestamp, nonce, encrypted)
        return (
            "<xml>"
            f"<Encrypt><![CDATA[{encrypted}]]></Encrypt>"
            f"<MsgSignature><![CDATA[{signature}]]></MsgSignature>"
            f"<TimeStamp>{timestamp}</TimeStamp>"
            f"<Nonce><![CDATA[{nonce}]]></Nonce>"
            "</xml>"
        )
