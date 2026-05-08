"""JSON 提取工具契约测试：覆盖主流 LLM 输出格式。"""

from __future__ import annotations

from backend.app.application.agents._json_parsing import extract_json_object


def test_plain_json_object():
    assert extract_json_object('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}


def test_code_fence_json():
    text = '这是回答:\n```json\n{"points": ["a", "b"], "n": 2}\n```\n'
    assert extract_json_object(text) == {"points": ["a", "b"], "n": 2}


def test_code_fence_any_language():
    text = "前言\n```\n{\"x\": true}\n```"
    assert extract_json_object(text) == {"x": True}


def test_think_block_stripped():
    text = "<think>在思考要返回啥</think>\n{\"answer\": \"final\"}"
    assert extract_json_object(text) == {"answer": "final"}


def test_think_block_case_insensitive():
    text = "<Think>\nreasoning\n</Think>{\"k\": 3}"
    assert extract_json_object(text) == {"k": 3}


def test_json_mixed_with_prose():
    text = "好的，这是分析结果：\n{\"conclusions\": [\"结论1\"], \"highlight\": \"X\"}\n希望有帮助。"
    out = extract_json_object(text)
    assert out == {"conclusions": ["结论1"], "highlight": "X"}


def test_nested_object_via_greedy():
    text = '前缀\n{"a": {"b": [1, 2, 3]}, "c": "end"}\n后缀'
    out = extract_json_object(text)
    assert out == {"a": {"b": [1, 2, 3]}, "c": "end"}


def test_list_at_root_returns_none():
    """根级不是 dict 时返回 None（该工具只接受 dict）。"""
    assert extract_json_object("[1, 2, 3]") is None


def test_empty_and_none_inputs():
    assert extract_json_object("") is None
    assert extract_json_object("   \n\t") is None
    assert extract_json_object(None) is None  # type: ignore[arg-type]
    assert extract_json_object(42) is None  # type: ignore[arg-type]


def test_malformed_json_returns_none():
    assert extract_json_object("not json at all") is None
    assert extract_json_object("{oops") is None
    assert extract_json_object("```json\n{broken: value}\n```") is None


def test_strict_skips_narrow_fallback():
    text = "抱歉,无法生成 {valid: json},这是示例 {x: 1}"
    assert extract_json_object(text, strict=True) is None


def test_non_strict_narrow_fallback_finds_valid_subset():
    """窄正则兜底：当文本里只有一段正确的 {...} 夹在文字里时应能提到。"""
    text = "prefix {\"ok\": true} suffix"
    assert extract_json_object(text, strict=False) == {"ok": True}


def test_chinese_values_preserved():
    text = '```json\n{"points": ["手持口播", "单推手"], "highlight": "开箱测评"}\n```'
    out = extract_json_object(text)
    assert out is not None
    assert out["highlight"] == "开箱测评"
    assert "手持口播" in out["points"]


def test_multiple_code_fences_picks_first():
    """两个代码块时取第一个（LLM 一般第一个就是想要的输出）。"""
    text = '```json\n{"first": 1}\n```\n然后:\n```json\n{"second": 2}\n```'
    assert extract_json_object(text) == {"first": 1}
