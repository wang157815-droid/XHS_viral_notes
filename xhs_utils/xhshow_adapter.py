"""xhshow 签名适配层（阶段 4.1 hotfix 3，兼容 0.1.x / 0.2.x）。

背景：
- 我们项目原本用 execjs 跑 static/xhs_xs_xsc_56.js 生成签名,
  但该 JS 文件是 2024 年版本,小红书签名算法已多次迭代,老算法虽然能通过
  "success=True"校验,但服务端内容风控会识别算法版本差异,返回空结果(items=[]),
  这就是"诊断脚本 success=True 但 items=0"的根本原因。
- MediaCrawler 迁移到了 `xhshow` 纯 Python 库(作者 Cloxl),社区持续跟进小红书算法。
- 本模块封装 xhshow,对外提供与旧 `generate_xs_xs_common` 等价的接口,
  通过环境变量 `XHS_SIGN_BACKEND=xhshow|execjs` 可切换(默认 xhshow)。

版本兼容：
- xhshow < 0.2.0：GET 请求走手动调内部 build_payload_array 的老路径，
  并 monkey-patch 修复 a3_hash bug（issue #104）。
- xhshow >= 0.2.0：GET 请求直接用 sign_headers_get 高层接口，
  POST 请求用 sign_headers_post，两者均无需 patch。
  0.2.0 在 build_payload_array 新增了 hex_md5_path 参数，
  若仍需 patch（老版本降级场景），需匹配新签名。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import time
from typing import Any, Dict, Optional, Union
from urllib.parse import quote


_PATCHED = False


def _patch_xhshow_a3_hash() -> None:
    """为 GET 请求修复 xhshow 的 a3_hash 计算 bug（一次性 monkey-patch）。
    
    自动检测 xhshow API 版本（0.1.x vs 0.2.x），匹配不同签名。
    xhshow >= 0.2.0 已提供 sign_headers_get 高层接口，此 patch 仅作兜底。
    """
    global _PATCHED
    if _PATCHED:
        return

    try:
        from xhshow.core.crypto import CryptoProcessor
    except Exception:
        return

    _original_build = CryptoProcessor.build_payload_array

    # 探测 API 版本：0.2.0 新增了 hex_md5_path 参数
    sig_params = list(inspect.signature(_original_build).parameters.keys())
    is_v2_api = "hex_md5_path" in sig_params

    if is_v2_api:
        # xhshow >= 0.2.0 签名：(self, hex_parameter, hex_md5_path, a1_value, ...)
        def _patched_build(
            self,
            hex_parameter: str,
            hex_md5_path: str,
            a1_value: str,
            app_identifier: str = "xhs-pc-web",
            string_param: str = "",
            timestamp=None,
            sign_state=None,
        ):
            payload = _original_build(
                self, hex_parameter, hex_md5_path, a1_value,
                app_identifier, string_param, timestamp, sign_state,
            )
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
    else:
        # xhshow < 0.2.0 签名：(self, hex_parameter, a1_value, ...)
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
                self, hex_parameter, a1_value, app_identifier,
                string_param, timestamp, sign_state,
            )
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
    """用 xhshow 生成签名 headers，返回 {x-s, x-t, x-s-common, x-b3-traceid}。
    
    兼容策略：
    - POST：始终用 sign_headers_post。
    - GET + xhshow >= 0.2.0（有 sign_headers_get）：直接调高层接口。
    - GET + xhshow < 0.2.0：使用手动内部调用路径（+a3_hash patch）。
    """
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

    elif hasattr(client, "sign_headers_get"):
        # xhshow >= 0.2.0：使用高层 GET 接口，内部已正确处理 query 参数和时间戳
        params = data if isinstance(data, dict) else {}
        headers = client.sign_headers_get(
            uri=uri,
            cookies=cookie_str,
            params=params,
        )

    else:
        # xhshow < 0.2.0：手动调内部方法（保留旧路径作兜底）
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
