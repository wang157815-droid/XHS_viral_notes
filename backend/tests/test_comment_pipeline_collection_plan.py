"""``describe_collection_plan`` 单测。

回归背景:对话层播报"已启动评论分析任务"时曾写死"Top N 条笔记/Top K 条评论"，
但 v2 全量舆情分析实际不做该截断(笔记数量由采集/缓存策略动态决定,评论全翻页拉取)，
导致播报文案与实际采集结果不匹配。``describe_collection_plan`` 是文案的唯一信源，
必须与 ``_ANALYSIS_VERSION`` 的真实采集行为保持一致。
"""
from __future__ import annotations

from backend.app.application import comment_pipeline as cp


def test_v2_plan_does_not_promise_unenforced_comment_cap():
    """v2 下文案不应出现"每条笔记取 Top K 条评论"这种不生效的硬性承诺。"""
    assert cp._ANALYSIS_VERSION == "v2", "此测试假设默认版本为 v2，若切换请同步调整断言"
    text = cp.describe_collection_plan(top_notes=20, top_comments_per_note=5)
    assert "全量评论采集" in text
    assert "不设条数上限" in text
    # 不应再出现 v1 式的精确条数承诺
    assert "Top 5 条高赞评论" not in text
    assert "20 条笔记" not in text or "目标样本规模 20 条起" in text


def test_v2_plan_unlimited_when_top_notes_zero():
    """top_notes=0(未指定/不限)时，文案应说明不限数量，而非编造一个具体数字。"""
    text = cp.describe_collection_plan(top_notes=0, top_comments_per_note=5)
    assert "不限数量" in text


def test_v1_plan_keeps_strict_topk_wording(monkeypatch):
    """切回 v1 时，v1 确实按 top_notes/top_comments_per_note 截断，文案应准确描述该截断。"""
    monkeypatch.setattr(cp, "_ANALYSIS_VERSION", "v1")
    text = cp.describe_collection_plan(top_notes=20, top_comments_per_note=5)
    assert "Top 20" in text
    assert "Top 5 条高赞评论" in text
