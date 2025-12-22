"""
数字处理工具函数
"""
import re
from typing import Union


def parse_chinese_number(value: Union[str, int, float]) -> int:
    """
    解析中文数字格式为整数

    支持格式：
    - "2.3万" → 23000
    - "5.6千" → 5600
    - "1234" → 1234
    - 1234 → 1234

    Args:
        value: 待转换的值，可以是字符串、整数或浮点数

    Returns:
        转换后的整数值

    Examples:
        >>> parse_chinese_number("2.3万")
        23000
        >>> parse_chinese_number("5.6千")
        5600
        >>> parse_chinese_number("1234")
        1234
        >>> parse_chinese_number(1234)
        1234
    """
    # 如果已经是数字类型，直接返回
    if isinstance(value, (int, float)):
        return int(value)

    # 如果是None或空字符串，返回0
    if not value:
        return 0

    # 转换为字符串并去除空格
    value_str = str(value).strip()

    # 如果是空字符串，返回0
    if not value_str:
        return 0

    # 定义中文单位对应的倍数
    units = {
        '万': 10000,
        'w': 10000,
        'W': 10000,
        '千': 1000,
        'k': 1000,
        'K': 1000,
        '百': 100,
    }

    # 查找数字部分和单位部分
    # 匹配模式：数字（可能带小数点） + 可选的单位
    match = re.match(r'^([\d.]+)([万wWkK千百]?)$', value_str)

    if match:
        number_part = match.group(1)
        unit_part = match.group(2)

        try:
            # 转换数字部分为浮点数
            number = float(number_part)

            # 如果有单位，乘以对应的倍数
            if unit_part and unit_part in units:
                number *= units[unit_part]

            # 返回整数
            return int(number)
        except (ValueError, TypeError):
            # 转换失败，返回0
            return 0

    # 如果不匹配模式，尝试直接转换为整数
    try:
        return int(float(value_str))
    except (ValueError, TypeError):
        # 无法转换，返回0
        return 0


def format_number_chinese(value: int) -> str:
    """
    将数字格式化为中文显示

    Args:
        value: 整数值

    Returns:
        格式化后的字符串

    Examples:
        >>> format_number_chinese(23000)
        '2.3万'
        >>> format_number_chinese(5600)
        '5.6千'
        >>> format_number_chinese(1234)
        '1234'
    """
    if value >= 10000:
        # 万级别
        result = value / 10000
        if result == int(result):
            return f"{int(result)}万"
        else:
            return f"{result:.1f}万"
    elif value >= 1000:
        # 千级别
        result = value / 1000
        if result == int(result):
            return f"{int(result)}千"
        else:
            return f"{result:.1f}千"
    else:
        # 直接显示
        return str(value)
