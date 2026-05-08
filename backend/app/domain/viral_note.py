"""
ViralNote: 爆款笔记标准数据契约(阶段 4.3pre.1)。

对齐"抗老精华爆文模型—兴长信达.xlsx"Sheet 3 "数据源总" 的 20 列字段,
外加 Sheet 4 "竞品爆文" 独有的 2 个 SEO 字段(可选)。

这是 4 个数据源 sheet(Sheet 3/4/5/6) 的统一标准字段模型,
任何 CrawlerAgent 的采集结果最终都要 normalize 到这个 dataclass。

字段分区:
- 10 基础元数据字段: 来源 / 达人 / 品牌 / 互动数 / ...
- 3 内容分类字段: 内容方向 / 封面 url / 标题
- 7 AI 产出的标注字段(6 要素 + 痛点关键词): 由 VideoAnalysisAgent 或 ImageAnalysisAgent 产出
- 3 扩展字段(竞品专用): 评论区关键词 / 笔记热搜词 Top10 / 评论热词 Top10

权威文档: docs/canvas_restructure_spec.md 第 5.1 章
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class SourceType(str, Enum):
    """爆款笔记的采集来源 — 对应模板里 4 个数据 sheet。"""

    CATEGORY_TOP = "category_top"       # Sheet 3 "数据源总" / 品类 TOP
    COMPETITOR = "competitor"           # Sheet 4 "竞品爆文"
    TOP_INTERACTION = "top_interaction" # Sheet 5 "【品类】抗老精华互动 top"
    SERP_TOP = "serp_top"               # Sheet 6 "【精华】小红书前 10 屏爆文"


@dataclass
class ViralNote:
    """爆款笔记标准字段(22 字段:10 基础 + 3 内容 + 7 标注 + 2 扩展)。"""

    # ----- 基础元数据(10 字段,由 CrawlerAgent 产出)-----
    note_id: str                         # 稳定唯一 id (小红书 note_id)
    source: SourceType                   # 采集来源(4 类枚举)
    source_label: str                    # 显示用的来源标签(如"【品类】抗老精华 TOP")
    creator_nickname: str                # 达人昵称
    creator_type: str                    # 达人类型("个护分享;口播单品推荐")
    brand: str                           # 品牌(如"IPSA茵芙莎")
    note_url: str                        # 小红书笔记链接
    likes: int = 0                       # 点赞
    collects: int = 0                    # 收藏
    comments: int = 0                    # 评论
    published_at: Optional[str] = None   # ISO 日期(Sheet 5 有)

    # ----- 内容分类(3 字段)-----
    content_direction: str = ""          # J 列: "口播单推" / "剧情" / "知识科普" / ...
    cover_url: str = ""                  # K 列封面截图 url
    title: str = ""                      # L 列笔记真实标题

    # ----- AI 产出的标注字段(7 字段,由 VideoAnalysisAgent/ImageAnalysisAgent 产出)-----
    pain_keywords: str = ""              # M 列: 如"法令纹（痛点）/仅用14天（见效快数字）"
    cover_type: str = ""                 # N 列: 如"学习前后对比图"
    cover_text_type: str = ""            # O 列: 封面压字类型"干货/经验分享"
    title_type: str = ""                 # P 列: 标题分类标签"干货/经验分享"
    opening_type: str = ""               # Q 列: 内容切入点"好皮肤的重要性（痒点）"
    product_intro_type: str = ""         # R 列: 产品引出方式"直接带出"
    product_placement_type: str = ""     # S 列: 产品植入方式"融合自己使用方法/感受讲卖点"

    # ----- 扩展字段(竞品 sheet 独有)-----
    comment_keywords: Optional[str] = None        # T 列: 评论区关键词(品类 TOP 独有,非竞品)
    seo_top10: Optional[List[str]] = None         # 竞品 sheet T 列: 笔记涵盖热搜词 Top10
    comment_hotwords_top10: Optional[List[str]] = None  # 竞品 sheet U 列: 评论热词 Top10

    # ----- 元数据(非模板字段,系统内部维护)-----
    crawled_at: Optional[str] = None     # 采集时间 ISO
    analysis_version: Optional[str] = None  # 标注字段的 AI 分析版本号

    @property
    def interaction_total(self) -> int:
        """互动总量 = 点赞 + 收藏 + 评论(对应模板 F 列公式 =G+H+I)。"""
        return self.likes + self.collects + self.comments

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict(SourceType 枚举值转字符串,便于 JSON/SSE 传输)。"""
        data = asdict(self)
        # 把 SourceType 枚举转为字符串
        if isinstance(self.source, SourceType):
            data["source"] = self.source.value
        # 补上 computed 字段
        data["interaction_total"] = self.interaction_total
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ViralNote":
        """反序列化,自动处理 SourceType 枚举和多余字段。"""
        cleaned = dict(data)
        # 去掉 computed 字段(__init__ 不接受)
        cleaned.pop("interaction_total", None)
        # 把字符串转回 SourceType
        src = cleaned.get("source")
        if isinstance(src, str):
            try:
                cleaned["source"] = SourceType(src)
            except ValueError:
                cleaned["source"] = SourceType.CATEGORY_TOP  # 兜底
        # 丢弃未知字段(保持容错)
        known = {f.name for f in cls.__dataclass_fields__.values()}
        cleaned = {k: v for k, v in cleaned.items() if k in known}
        return cls(**cleaned)

    def has_complete_annotations(self) -> bool:
        """判断 AI 标注是否完整(6 要素全部非空,不含 pain_keywords)。"""
        return all(
            getattr(self, attr).strip()
            for attr in [
                "cover_type",
                "cover_text_type",
                "title_type",
                "opening_type",
                "product_intro_type",
                "product_placement_type",
            ]
        )


__all__ = ["SourceType", "ViralNote"]
