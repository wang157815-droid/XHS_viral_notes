"""数字解析单测：确保 parse_chinese_number 处理小红书各种 API 返回格式。"""

from __future__ import annotations

import pytest

from viral_agent.utils.number_utils import parse_chinese_number


class TestBasicFormats:
    def test_plain_int(self):
        assert parse_chinese_number(1234) == 1234

    def test_plain_float(self):
        assert parse_chinese_number(1234.56) == 1234

    def test_numeric_string(self):
        assert parse_chinese_number("1234") == 1234
        assert parse_chinese_number("0") == 0


class TestChineseUnits:
    def test_wan(self):
        assert parse_chinese_number("2.3万") == 23000
        assert parse_chinese_number("10万") == 100000

    def test_qian(self):
        assert parse_chinese_number("5.6千") == 5600

    def test_english_w_k(self):
        assert parse_chinese_number("2.3w") == 23000
        assert parse_chinese_number("1.5k") == 1500
        assert parse_chinese_number("3W") == 30000


class TestPlusSuffix:
    """小红书 API 返回 '100+' / '999+' 表示"xx 以上",按下界处理。"""

    def test_number_with_plus(self):
        assert parse_chinese_number("100+") == 100
        assert parse_chinese_number("999+") == 999
        assert parse_chinese_number("1000+") == 1000

    def test_number_with_unit_and_plus(self):
        assert parse_chinese_number("2.3w+") == 23000
        assert parse_chinese_number("10万+") == 100000

    def test_full_width_plus(self):
        """全角加号 '＋' 也应被剥离。"""
        assert parse_chinese_number("100＋") == 100


class TestEdgeCases:
    def test_empty_and_none(self):
        assert parse_chinese_number("") == 0
        assert parse_chinese_number(None) == 0  # type: ignore[arg-type]

    def test_whitespace(self):
        assert parse_chinese_number("   ") == 0
        assert parse_chinese_number("  1234  ") == 1234

    def test_non_numeric_chinese(self):
        """纯中文(如 '赞')或无意义输入,返回 0 而不是报错。"""
        assert parse_chinese_number("赞") == 0
        assert parse_chinese_number("评论") == 0

    def test_only_plus(self):
        """只有加号,去掉后为空串,返回 0。"""
        assert parse_chinese_number("+") == 0
        assert parse_chinese_number("++") == 0

    def test_negative_not_supported_but_safe(self):
        """不支持负数,但不能崩溃。"""
        assert parse_chinese_number("-100") == 0 or parse_chinese_number("-100") == -100


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
