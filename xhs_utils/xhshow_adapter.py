"""xhshow 签名适配层（阶段 4.1 hotfix 3）。

背景：
- 我们项目原本用 execjs 跑 static/xhs_xs_xsc_56.js 生成签名,
  但该 JS 文件是 2024 年版本,小红书签名算法已多次迭代,老算法虽然能通过
  "success=True"校验,但服务端内容风控会识别算法版本差异,返回空结果(items=[]),
  这就是"诊断脚本 success=True 但 items=0"的根本原因。
- MediaCrawler 迁移到了 `xhshow` 纯 Python 库(作者 Cloxl),社区持续跟进小红书算法。
- 本模块封装 xhshow,对外提供与旧 `generate_xs_xs_common` 等价的接口,
  通过环境变量 `XHS_SIGN_BACKEND=xhshow|execjs` 可切换(默认 xhshow)。

签名 bug 修复：
  参照 MediaCrawler/playwright_sign.py 的 `_patch_xhshow_a3_hash`,
  修复 xhshow 对 GET 请求 a3_hash 计算错误(应使用完整 content_string 的 MD5,
  而不是剥离 query 参数后的 URI)。issue: https://github.com/Cloxl/xhshow/issues/104
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Dict, Optional, Union
from urllib.parse import quote


_PATCHED = False


def _patch_xhshow_a3_hash() -> None:
    """为 GET 请求修复 xhshow 的 a3_hash 计算 bug（一次性 monkey-patch）。"""
    global _PATCHED
    if _PATCHED:
        return

    try:
        from xhshow.core.crypto import CryptoProcessor
    except Exception:
        return

    _original_build = CryptoProcessor.build_payload_array

    def _patched_build(
        self,
        hex_parameter,
        a1_value,
        app_identifier="xhs-pc-web",
        string_param="",
        timestamp=None,
        sign_state=None,
    ):
        payload = _original_build(
            self,
            hex_parameter,
            a1_value,
            app_identifier,
            string_param,
            timestamp,
            sign_state,
        )
        # 仅对 GET 请求修复（content_string 不含 "{"）
        if "{" not in string_param:
            correct_md5_hex = hashlib.md5(string_param.encode("utf-8")).hexdigest()
            correct_md5_bytes = [
                int(correct_md5_hex[i : i + 2], 16) for i in range(0, 32, 2)
            ]
            seed_byte = payload[4]
            ts_bytes = payload[8:16]
            correct_a3_hash = self._custom_hash_v2(
                list(ts_bytes) + correct_md5_bytes
            )
            for i in range(16):
                payload[128 + i] = correct_a3_hash[i] ^ seed_byte
        return payload

    CryptoProcessor.build_payload_array = _patched_build
    _PATCHED = True


def _build_get_sign_string(uri: str, data: Optional[Union[Dict, str]]) -> str:
    """构建 GET 请求的签名字符串（包含 query 参数）。"""
    if not data or (isinstance(data, dict) and not data):
        return uri
    if isinstance(data, dict):
        parts = []
        for key, value in data.items():
            if isinstance(value, list):
                value_str = ",".join(str(v) for v in value)
            elif value is not None:
                value_str = str(value)
            else:
                value_str = ""
            value_str = quote(value_str, safe=",")
            parts.append(f"{key}={value_str}")
        return f"{uri}?{'&'.join(parts)}"
    return f"{uri}?{data}"


def is_xhshow_available() -> bool:
    try:
        import xhshow  # noqa: F401

        return True
    except Exception:
        return False


def get_sign_backend() -> str:
    """读取 env `XHS_SIGN_BACKEND`,默认 xhshow。"""
    backend = (os.getenv("XHS_SIGN_BACKEND") or "xhshow").strip().lower()
    if backend not in ("xhshow", "execjs"):
        backend = "xhshow"
    if backend == "xhshow" and not is_xhshow_available():
        return "execjs"
    return backend


def sign_with_xhshow(
    uri: str,
    data: Optional[Union[Dict, str]],
    cookie_str: str,
    method: str = "POST",
) -> Dict[str, str]:
    """用 xhshow 生成签名 headers,返回 {x-s, x-t, x-s-common, x-b3-traceid}。"""
    _patch_xhshow_a3_hash()

    from xhshow import Xhshow

    client = Xhshow()
    method_upper = method.upper()

    if method_upper == "POST":
        payload = data if isinstance(data, dict) else {}
        headers = client.sign_headers_post(
            uri=uri,
            cookies=cookie_str,
            payload=payload,
        )
    else:
        # GET:按 content_string 手工构建签名(和 MediaCrawler 一致)
        content_string = _build_get_sign_string(uri, data)
        cookie_dict = client._parse_cookies(cookie_str)
        a1_value = cookie_dict.get("a1", "")
        ts = time.time()
        d_value = hashlib.md5(content_string.encode("utf-8")).hexdigest()

        payload_array = client.crypto_processor.build_payload_array(
            d_value, a1_value, "xhs-pc-web", content_string, ts
        )
        xor_result = client.crypto_processor.bit_ops.xor_transform_array(payload_array)
        config = client.config
        x3_b64 = client.crypto_processor.b64encoder.encode_x3(
            xor_result[: config.PAYLOAD_LENGTH]
        )
        sig_data = config.SIGNATURE_DATA_TEMPLATE.copy()
        sig_data["x3"] = config.X3_PREFIX + x3_b64
        x_s = config.XYS_PREFIX + client.crypto_processor.b64encoder.encode(
            json.dumps(sig_data, separators=(",", ":"), ensure_ascii=False)
        )
        headers = {
            "x-s": x_s,
            "x-s-common": client.sign_xs_common(cookie_dict),
            "x-t": str(client.get_x_t(ts)),
            "x-b3-traceid": client.get_b3_trace_id(),
        }

    return {
        "x-s": headers.get("x-s", ""),
        "x-t": str(headers.get("x-t", "")),
        "x-s-common": headers.get("x-s-common", ""),
        "x-b3-traceid": headers.get("x-b3-traceid", ""),
    }


__all__ = [
    "sign_with_xhshow",
    "is_xhshow_available",
    "get_sign_backend",
]
