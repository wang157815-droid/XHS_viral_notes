"""note_seo_extract: 文案关键词 + 多模态补强。"""

from backend.app.services.note_seo_extract import (
    extract_seo_top10_from_content,
    merge_seo_top10_with_multimodal_annotation,
)


def test_extract_from_title_desc_non_empty() -> None:
    out = extract_seo_top10_from_content(
        title="山姆新品巧克力测评",
        desc="鲜牛奶巧克力口感丝滑,甜度适中适合下午茶搭配咖啡。",
        tags=["山姆会员店"],
        media_type="image",
    )
    blob = "".join(out)
    assert len(out) >= 1
    assert "山姆" in blob or "巧克力" in blob or "咖啡" in blob or "会员" in blob


def test_merge_fills_from_annotation_when_text_sparse() -> None:
    note = {"seo_top10": ["a"], "note_id": "n1"}
    ann = {"pain_keywords": "甜腻,回购,咖啡搭子", "content_direction": "零食测评"}
    merged = merge_seo_top10_with_multimodal_annotation(
        note, ann, min_text_terms=4, top_k=10
    )
    assert "甜腻" in merged or "回购" in merged
    assert "a" in merged
