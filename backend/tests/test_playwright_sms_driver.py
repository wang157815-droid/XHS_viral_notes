"""PlaywrightSmsLoginDriver 纯逻辑单测（不启动浏览器）。

只测 ``_strip_dial_prefix`` 与 ``_country_iso_to_label`` 等纯静态方法。
"""

from __future__ import annotations

import pytest

from backend.app.services.xhs_auth.playwright_sms_login_driver import (
    PlaywrightSmsLoginDriver,
)


# ---------------------------------------------------------------------------
# _strip_dial_prefix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phone,cc,expected",
    [
        # 用户场景：hero-sms HK 号 + 8 位本地号
        ("85291234567", "HK", "91234567"),
        ("85261112222", "HK", "61112222"),
        # 显式 +852 国家码也认
        ("85291234567", "+852", "91234567"),
        # 中国大陆 11 位本地号
        ("8613812345678", "CN", "13812345678"),
        ("8613812345678", "+86", "13812345678"),
        # 美国 +1
        ("14155551234", "US", "4155551234"),
        # 已经是本地号（无前缀）：保持不变
        ("91234567", "HK", "91234567"),
        # 带空格 / 横线：先抽数字再剥
        ("852-9123 4567", "HK", "91234567"),
    ],
)
def test_strip_dial_prefix_known_country(phone, cc, expected):
    assert (
        PlaywrightSmsLoginDriver._strip_dial_prefix(phone, country_code=cc)
        == expected
    )


def test_strip_dial_prefix_falls_back_when_country_missing():
    """country_code 缺失时按常见前缀启发式判断。"""
    # 没传 country_code，但前缀 852 仍能识别
    assert (
        PlaywrightSmsLoginDriver._strip_dial_prefix("85291234567", country_code="")
        == "91234567"
    )


def test_strip_dial_prefix_keeps_when_too_short_after_strip():
    """如果剥离后剩余长度不合理（<6），回退原 digits 避免误删。"""
    # "85291" 剥 852 后只剩 "91"（2 位），不合法 → 回退原 digits
    assert (
        PlaywrightSmsLoginDriver._strip_dial_prefix("85291", country_code="HK")
        == "85291"
    )


def test_strip_dial_prefix_handles_empty_input():
    assert (
        PlaywrightSmsLoginDriver._strip_dial_prefix("", country_code="HK") == ""
    )


def test_strip_dial_prefix_unknown_country_falls_back_heuristic():
    """未知 ISO（如 'XX'）应走启发式 fallback，仍能识别 852 等前缀。"""
    assert (
        PlaywrightSmsLoginDriver._strip_dial_prefix(
            "85291234567", country_code="XX"
        )
        == "91234567"
    )


# ---------------------------------------------------------------------------
# _country_iso_to_label / _country_iso_to_dial
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "iso,label",
    [("HK", "+852"), ("CN", "+86"), ("US", "+1"), ("GB", "+44")],
)
def test_country_iso_to_label(iso, label):
    assert PlaywrightSmsLoginDriver._country_iso_to_label(iso) == label


@pytest.mark.parametrize(
    "iso,dial",
    [
        ("HK", "852"),
        ("CN", "86"),
        ("US", "1"),
        ("TW", "886"),
        ("MO", "853"),
        ("XX", ""),  # 未知 → 空串
        ("", ""),
    ],
)
def test_country_iso_to_dial(iso, dial):
    assert PlaywrightSmsLoginDriver._country_iso_to_dial(iso) == dial
