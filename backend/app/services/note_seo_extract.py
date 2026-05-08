"""从笔记标题 + 正文 + 话题标签抽取「笔记涵盖热搜词」(非搜索 query)。

视频笔记：以标题/正文(口播稿、简介)为主;音视频画面语义需多模态 Agent,不在此模块重复调 VL。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_SEO_STOP = frozenset(
    {
        "一个",
        "可以",
        "没有",
        "不是",
        "什么",
        "怎么",
        "这个",
        "那个",
        "这样",
        "那种",
        "如果",
        "因为",
        "所以",
        "但是",
        "然后",
        "就是",
        "还是",
        "还有",
        "或者",
        "已经",
        "现在",
        "真的",
        "非常",
        "比较",
        "感觉",
        "大家",
        "我们",
        "你们",
        "他们",
        "自己",
        "时候",
        "一下",
        "不会",
        "不能",
        "不要",
        "需要",
        "喜欢",
        "分享",
        "推荐",
        "笔记",
        "视频",
        "图片",
        "小红书",
    }
)


def extract_seo_top10_from_content(
    *,
    title: str,
    desc: str,
    tags: Optional[List[str]] = None,
    media_type: str = "image",
    top_k: int = 10,
) -> List[str]:
    """基于文案与话题标签抽取热搜词/关键短语,最多 top_k 条。"""
    tags = tags or []
    title = (title or "").strip()
    desc = (desc or "").strip()
    tag_blob = " ".join(str(t).strip().lstrip("#") for t in tags if str(t).strip())
    corpus = "\n".join(x for x in (title, desc, tag_blob) if x).strip()
    if not corpus:
        return []

    out: List[str] = []
    seen: set[str] = set()

    try:
        import jieba.analyse

        allow = ("n", "nr", "ns", "nt", "nz", "vn", "eng", "v")
        terms = jieba.analyse.extract_tags(
            corpus,
            topK=max(top_k * 3, 20),
            withWeight=False,
            allowPOS=allow,
        )
        for kw in terms:
            s = str(kw).strip()
            if len(s) < 2 or s in _SEO_STOP:
                continue
            if s not in seen:
                seen.add(s)
                out.append(s)
            if len(out) >= top_k:
                return out
    except Exception:  # noqa: BLE001
        pass

    # 回退: 从话题标签与简单中文片段补全
    for t in tags:
        s = str(t).strip().lstrip("#").strip()
        if len(s) >= 2 and s not in seen and s not in _SEO_STOP:
            seen.add(s)
            out.append(s)
        if len(out) >= top_k:
            return out

    for m in re.findall(r"[\u4e00-\u9fff]{2,6}", title + desc):
        if m not in seen and m not in _SEO_STOP:
            seen.add(m)
            out.append(m)
        if len(out) >= top_k:
            break

    _ = media_type  # 预留与多模态分支区分日志/策略
    return out[:top_k]


def merge_seo_top10_with_multimodal_annotation(
    note: Dict[str, Any],
    ann: Optional[Dict[str, Any]],
    *,
    min_text_terms: int = 4,
    top_k: int = 10,
) -> List[str]:
    """导出/画布展示用: 文案抽取为主,视频等多模态标注的痛点/方向作补强。

    当标题+正文抽取不足 min_text_terms 条时,并入 ``pain_keywords``、``content_direction``。
    """
    base = [
        str(x).strip()
        for x in (note.get("seo_top10") or [])
        if str(x).strip()
    ]
    if len(base) >= min_text_terms:
        return base[:top_k]

    ann = ann or {}
    extra: List[str] = []
    pk = ann.get("pain_keywords") or ""
    if isinstance(pk, str) and pk.strip():
        extra.extend(
            p.strip() for p in re.split(r"[,，/|、\s]+", pk) if p.strip()
        )
    cd = ann.get("content_direction") or ""
    if isinstance(cd, str) and cd.strip():
        extra.append(cd.strip()[:30])

    seen = set(base)
    out = list(base)
    for x in extra:
        s = str(x).strip()
        if len(s) < 2 or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= top_k:
            break
    return out[:top_k]
