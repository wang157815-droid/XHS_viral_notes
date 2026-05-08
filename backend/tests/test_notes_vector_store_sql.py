"""notes_vector_store SQL 文本正则检查（阶段 4.α-fix）。

目的：防止 `:name::type` 这种 "SQLAlchemy 命名参数 + PG 类型转换" 混用语法复发,
因为 asyncpg 无法解析它,会抛 `syntax error at or near ":"`。

策略：
- 反射读出 `add_notes` / `search_notes` / `count_recent_for_keywords` 里构造的 SQL 文本,
- 用正则扫描,若出现 `:\w+::\w+` 立即失败。

这是一种"编译时保险丝",不需要真连 Postgres 就能跑,保证未来任何人改 SQL
都不会不小心又写出这种歧义语法。
"""

from __future__ import annotations

import re

import pytest


_DANGEROUS_PATTERN = re.compile(r":\w+::\w+")


def _extract_text_blocks(source: str) -> list[str]:
    """粗略切出 sql_text(" ... ") 里的字符串块,足以覆盖单测检查需求。"""
    blocks: list[str] = []
    in_string = False
    quote_char = ""
    buf: list[str] = []
    depth = 0
    for i, ch in enumerate(source):
        if not in_string:
            if source[i : i + 3] == '"""':
                in_string = True
                quote_char = '"""'
                continue
            if ch in ('"', "'"):
                in_string = True
                quote_char = ch
                continue
        else:
            if quote_char == '"""' and source[i : i + 3] == '"""':
                in_string = False
                quote_char = ""
                blocks.append("".join(buf))
                buf = []
                continue
            if quote_char in ('"', "'") and ch == quote_char:
                in_string = False
                quote_char = ""
                blocks.append("".join(buf))
                buf = []
                continue
            buf.append(ch)
    return blocks


def test_no_colon_type_cast_pattern():
    """扫描源码,不应出现 `:name::type` 歧义语法。"""
    from backend.app.infrastructure.storage import notes_vector_store

    source = open(notes_vector_store.__file__, encoding="utf-8").read()
    matches = _DANGEROUS_PATTERN.findall(source)

    # 容忍注释里出现（解释 bug 的时候要提到这个串）
    # 实际方式：剥离 Python 注释和文档串 → 再扫
    stripped_lines = []
    for line in source.splitlines():
        code_part = line.split("#", 1)[0]
        stripped_lines.append(code_part)
    code_only = "\n".join(stripped_lines)
    # 再去掉三引号文档块（粗略,足够）
    code_no_docstrings = re.sub(r'""".*?"""', "", code_only, flags=re.S)

    matches_in_code = _DANGEROUS_PATTERN.findall(code_no_docstrings)

    assert not matches_in_code, (
        f"检测到禁用的 `:name::type` 歧义语法: {matches_in_code}。"
        "请改为 `CAST(:name AS type)` 写法,否则 asyncpg 会抛 syntax error."
    )


def test_cast_as_vector_present_in_upsert():
    """确认 upsert SQL 用的是 CAST(... AS vector),而不是 :name::vector。"""
    from backend.app.infrastructure.storage import notes_vector_store

    source = open(notes_vector_store.__file__, encoding="utf-8").read()
    assert "CAST(:embedding AS vector)" in source
    assert ":embedding::vector" not in source


def test_cast_as_text_array_present_in_search():
    """确认 search/count SQL 用的是 CAST(:keywords AS text[])。"""
    from backend.app.infrastructure.storage import notes_vector_store

    source = open(notes_vector_store.__file__, encoding="utf-8").read()
    assert "CAST(:keywords AS text[])" in source
    assert ":keywords::text[]" not in source


def test_case_when_null_branch_also_casts_embedding():
    """CASE WHEN 两分支必须都给 :embedding 同样的类型约束。

    背景：当 :embedding 为 NULL 时,若 IS NULL 分支裸写 `:embedding IS NULL`,
    PostgreSQL 无法从 IS NULL 推断类型,而 ELSE 分支的 CAST(:embedding AS vector)
    推断为 text, 两边不一致会抛 `could not determine data type of parameter`。
    必须两边都 CAST。
    """
    from backend.app.infrastructure.storage import notes_vector_store

    source = open(notes_vector_store.__file__, encoding="utf-8").read()

    # 只剥离行内 # 注释,保留 sql_text("""...""") 里的 SQL 字面量
    code_lines = [line.split("#", 1)[0] for line in source.splitlines()]
    code_only = "\n".join(code_lines)

    # 禁用 pattern: :embedding 紧跟 IS NULL (裸命名参数 + IS NULL,无 CAST 类型约束)
    # 允许的是 CAST(:embedding AS text) IS NULL,此时 :embedding 后跟的是 " AS text"
    bad = re.search(r":embedding\s+IS\s+NULL", code_only)
    assert bad is None, (
        "检测到裸命名参数配 IS NULL 的写法(无 CAST),PG 会无法推断 NULL 的类型。"
        "请改为 `CAST(:embedding AS text) IS NULL` 等显式类型约束写法。"
    )

    # 正向确认:CASE WHEN 分支中包含 CAST(:embedding AS text) IS NULL
    assert "CAST(:embedding AS text) IS NULL" in code_only
