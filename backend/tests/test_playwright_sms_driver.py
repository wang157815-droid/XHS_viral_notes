"""PlaywrightSmsLoginDriver 纯逻辑单测（不启动浏览器）。

只测 ``_strip_dial_prefix`` 与 ``_country_iso_to_label`` 等纯静态方法。
"""

from __future__ import annotations

import importlib
import os

import pytest

import backend.app.services.xhs_auth.playwright_sms_login_driver as driver_mod
from backend.app.services.xhs_auth.playwright_sms_login_driver import (
    PlaywrightSmsLoginDriver,
)


class FakeLocatorItem:
    def __init__(self, *, visible=True, text="", click_error=None):
        self.visible = visible
        self.text = text
        self.click_error = click_error
        self.clicked = False

    async def is_visible(self, timeout=500):
        return self.visible

    async def click(self, timeout=3000):
        if self.click_error:
            raise self.click_error
        if not self.visible:
            raise RuntimeError("element is hidden")
        self.clicked = True

    async def inner_text(self, timeout=500):
        return self.text


class FakeLocator:
    def __init__(self, items):
        self.items = items

    async def count(self):
        return len(self.items)

    def nth(self, idx):
        return self.items[idx]

    @property
    def first(self):
        return self.items[0]


class FakePage:
    def __init__(self, mapping):
        self.mapping = mapping
        self.locator_calls = []
        self.click_fallback_called = False

    def locator(self, selector):
        self.locator_calls.append(selector)
        return FakeLocator(self.mapping.get(selector, []))

    async def click(self, selector, timeout=3000):
        self.click_fallback_called = True
        raise RuntimeError(f"fallback click failed: {selector}")


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
    [
        ("HK", "+852"),
        ("CN", "+86"),
        ("US", "+1"),
        ("GB", "+44"),
        ("TW", "+886"),
        ("MO", "+853"),
        ("SG", "+65"),
        ("MY", "+60"),
    ],
)
def test_country_iso_to_label(iso, label):
    assert PlaywrightSmsLoginDriver._country_iso_to_label(iso) == label


def test_country_selectors_include_rednote_fallbacks():
    assert "Search" in driver_mod._SELECTORS["country_search_input"]
    assert ":text(\"+852\")" in driver_mod._SELECTORS["country_option_template"]
    assert "Log in with phone" in driver_mod._SELECTORS["phone_tab"]


def test_selector_candidates_splits_composite_selector():
    assert PlaywrightSmsLoginDriver._selector_candidates("a, b , , c") == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_safe_click_tries_later_visible_candidate():
    driver = PlaywrightSmsLoginDriver()
    first = FakeLocatorItem(visible=False)
    second = FakeLocatorItem(visible=True)
    driver._page = FakePage({"bad": [first], "good": [second]})

    assert await driver._safe_click("bad, good", optional=True) is True
    assert second.clicked is True
    assert driver._page.click_fallback_called is False


@pytest.mark.asyncio
async def test_country_selected_check_uses_selector_text(monkeypatch):
    driver = PlaywrightSmsLoginDriver()
    monkeypatch.setitem(driver_mod._SELECTORS, "country_selector", "button.country-trigger")
    driver._page = FakePage(
        {"button.country-trigger": [FakeLocatorItem(visible=True, text="+852")]}
    )

    assert await driver._is_country_code_already_selected("+852") is True


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


def test_login_url_defaults_to_rednote_when_env_points_to_rednote(monkeypatch):
    original = os.environ.get("XHS_LOGIN_START_URL")
    monkeypatch.setenv("XHS_LOGIN_START_URL", "https://www.rednote.com/explore")

    import backend.app.services.xhs_auth.playwright_sms_login_driver as driver_mod

    try:
        reloaded = importlib.reload(driver_mod)
        assert reloaded._LOGIN_URL == "https://www.rednote.com/explore"
    finally:
        if original is None:
            monkeypatch.delenv("XHS_LOGIN_START_URL", raising=False)
        else:
            monkeypatch.setenv("XHS_LOGIN_START_URL", original)
        importlib.reload(driver_mod)
