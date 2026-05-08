"""
ViralNote + ViralModel + ViralTaxonomyLoader 单测
(阶段 4.3pre.1 数据模型重构)。

覆盖:
- ViralNote: 字段完整性 / 序列化可逆 / 互动总量计算 / 标注完整性判断
- ViralModel: 枚举 6 要素齐全 / 序列化 / 矩阵结构
- ViralTaxonomyLoader: 默认值 / 运行时覆盖 / propose 直接并入 taxonomy / 遗留 pending 审核 / version 递增

权威文档: docs/canvas_restructure_spec.md
"""

from __future__ import annotations

import json

import pytest

from backend.app.domain.viral_model import (
    ELEMENT_ORDER,
    ElementCategory,
    ElementCode,
    ElementExample,
    UnusedDirection,
    ViralModel,
    ViralModelMatrix,
)
from backend.app.domain.viral_note import SourceType, ViralNote


# ----------------------------------------------------------------------
# ViralNote
# ----------------------------------------------------------------------


class TestViralNote:
    def test_interaction_total_sums_three_metrics(self):
        note = ViralNote(
            note_id="n1",
            source=SourceType.CATEGORY_TOP,
            source_label="【品类】抗老精华 TOP",
            creator_nickname="憨桃人儿",
            creator_type="个护分享;口播单品推荐",
            brand="IPSA",
            note_url="https://xhs.xyz/n1",
            likes=128801,
            collects=98889,
            comments=961,
        )
        assert note.interaction_total == 128801 + 98889 + 961

    def test_roundtrip_serialization(self):
        note = ViralNote(
            note_id="n_test",
            source=SourceType.COMPETITOR,
            source_label="【竞品】PMPM",
            creator_nickname="测试达人",
            creator_type="护肤博主",
            brand="PMPM",
            note_url="https://xhs.xyz/n_test",
            likes=1000,
            collects=500,
            comments=100,
            title="实测14天",
            cover_type="前后对比",
            seo_top10=["法令纹", "胶原"],
        )
        payload = note.to_dict()

        # source 字段序列化为字符串
        assert payload["source"] == "competitor"
        assert payload["interaction_total"] == 1600

        restored = ViralNote.from_dict(payload)
        assert restored.note_id == note.note_id
        assert restored.source == SourceType.COMPETITOR
        assert restored.interaction_total == note.interaction_total
        assert restored.cover_type == "前后对比"
        assert restored.seo_top10 == ["法令纹", "胶原"]

    def test_from_dict_tolerates_unknown_keys(self):
        """反序列化时丢弃未知字段,保持容错。"""
        payload = {
            "note_id": "x",
            "source": "category_top",
            "source_label": "x",
            "creator_nickname": "x",
            "creator_type": "x",
            "brand": "x",
            "note_url": "x",
            "future_field_from_v2": "should be ignored",
        }
        note = ViralNote.from_dict(payload)
        assert note.note_id == "x"

    def test_has_complete_annotations_detects_missing_fields(self):
        note = ViralNote(
            note_id="n1",
            source=SourceType.CATEGORY_TOP,
            source_label="x",
            creator_nickname="x",
            creator_type="x",
            brand="x",
            note_url="x",
            cover_type="纯产品图",
            cover_text_type="痛点",
            title_type="干货",
            opening_type="痛点切入",
            product_intro_type="直接带出",
            # 缺 product_placement_type
        )
        assert note.has_complete_annotations() is False

        note.product_placement_type = "直接讲卖点"
        assert note.has_complete_annotations() is True


# ----------------------------------------------------------------------
# ViralModel 矩阵
# ----------------------------------------------------------------------


class TestViralModel:
    def test_element_order_has_six_elements(self):
        assert len(ELEMENT_ORDER) == 6
        codes = [e.value for e in ELEMENT_ORDER]
        assert codes == [
            "A_cover",
            "B_cover_text",
            "C_title",
            "D_opening",
            "E_product_intro",
            "F_product_placement",
        ]

    def test_element_code_has_chinese_labels(self):
        assert ElementCode.A_COVER.label == "封面"
        assert ElementCode.B_COVER_TEXT.label == "封面压字"
        assert ElementCode.F_PRODUCT_PLACEMENT.label == "产品植入方式"

    def test_viral_model_matrix_serialization(self):
        example = ElementExample(
            note_id="n1",
            title="示例标题",
            cover_url="https://xhs.xyz/cover.jpg",
            likes=12345,
        )
        category = ElementCategory(
            type="纯产品图",
            ratio=0.28,
            count=7,
            examples=[example],
            paragraph_id="M1-A-0",
        )
        model = ViralModel(
            model_id="M1",
            name="单品推荐",
            description="纯产品图 + 讲效果",
            coverage=0.25,
            avg_interaction=15511,
            sample_note_ids=["n1", "n2"],
            elements={ElementCode.A_COVER: [category]},
        )
        matrix = ViralModelMatrix(
            models=[model],
            unused_directions=[
                UnusedDirection(
                    direction="日常vlog",
                    ratio=0.08,
                    avg_interaction=19623,
                    reason="闭环验证后链路数据差",
                )
            ],
            total_sample_count=95,
            taxonomy_version="3",
        )
        payload = matrix.to_dict()

        assert payload["total_sample_count"] == 95
        assert len(payload["models"]) == 1
        m = payload["models"][0]
        assert m["model_id"] == "M1"
        assert "A_cover" in m["elements"]
        cat = m["elements"]["A_cover"][0]
        assert cat["type"] == "纯产品图"
        assert cat["ratio"] == 0.28
        assert cat["paragraph_id"] == "M1-A-0"

        # unused_directions
        assert len(payload["unused_directions"]) == 1
        assert payload["unused_directions"][0]["direction"] == "日常vlog"

    def test_element_category_without_paragraph_id_omits_key(self):
        category = ElementCategory(type="纯产品图", ratio=0.3, count=5, examples=[])
        data = category.to_dict()
        # paragraph_id 为 None 时序列化应省略该键(JSON 清洁)
        assert "paragraph_id" not in data

    def test_element_category_sheet2_narrative_fields_roundtrip(self):
        c = ElementCategory(
            type="t",
            ratio=0.2,
            count=2,
            examples=[],
            summary="概",
            explanation="释",
            example_text="例",
            track_id="A1",
            col_span=2,
        )
        d = c.to_dict()
        assert d["summary"] == "概"
        assert d["explanation"] == "释"
        assert d["example_text"] == "例"
        assert d["track_id"] == "A1"
        assert d["col_span"] == 2


# ----------------------------------------------------------------------
# ViralTaxonomyLoader
# ----------------------------------------------------------------------


class TestViralTaxonomyLoader:
    @pytest.mark.asyncio
    async def test_empty_file_returns_defaults(self, tmp_path):
        from backend.app.services.viral_taxonomy_loader import ViralTaxonomyLoader

        loader = ViralTaxonomyLoader(store_file=str(tmp_path / "tax.json"))
        data = await loader.get()

        assert data["version"] == 1
        assert "A_cover" in data["taxonomy"]
        # 默认 A 封面应含 "纯产品图"
        assert "纯产品图" in data["taxonomy"]["A_cover"]
        # pending 应为空
        assert data["pending_additions"] == {}

    @pytest.mark.asyncio
    async def test_propose_new_type_merges_into_taxonomy(self, tmp_path):
        from backend.app.services.viral_taxonomy_loader import ViralTaxonomyLoader

        loader = ViralTaxonomyLoader(store_file=str(tmp_path / "tax.json"))
        ok = await loader.propose_new_type(
            ElementCode.A_COVER, "AR滤镜封面"
        )
        assert ok is True

        # 第二次提同一个应该去重
        again = await loader.propose_new_type(ElementCode.A_COVER, "AR滤镜封面")
        assert again is False

        # 默认已存在的类型也应拒绝
        dup_default = await loader.propose_new_type(
            ElementCode.A_COVER, "纯产品图"
        )
        assert dup_default is False

        data = await loader.get()
        assert "AR滤镜封面" in data["taxonomy"]["A_cover"]
        assert not data["pending_additions"].get("A_cover")
        assert data["version"] == 2  # 加了一次,从 1 → 2

    @pytest.mark.asyncio
    async def test_approve_moves_legacy_pending_to_main(self, tmp_path):
        from backend.app.services.viral_taxonomy_loader import ViralTaxonomyLoader

        path = tmp_path / "tax.json"
        path.write_text(
            json.dumps(
                {
                    "version": 3,
                    "taxonomy": {},
                    "pending_additions": {"B_cover_text": ["反转钩"]},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        loader = ViralTaxonomyLoader(store_file=str(path))
        approved = await loader.approve_pending(
            ElementCode.B_COVER_TEXT, "反转钩"
        )
        assert approved is True

        data = await loader.get()
        assert "反转钩" in data["taxonomy"]["B_cover_text"]
        assert "反转钩" not in data["pending_additions"].get("B_cover_text", [])

    @pytest.mark.asyncio
    async def test_get_element_types_includes_inferred_types(self, tmp_path):
        from backend.app.services.viral_taxonomy_loader import ViralTaxonomyLoader

        loader = ViralTaxonomyLoader(store_file=str(tmp_path / "tax.json"))
        await loader.propose_new_type(ElementCode.A_COVER, "AR滤镜封面")

        types = await loader.get_element_types(ElementCode.A_COVER)
        assert "纯产品图" in types       # 来自 DEFAULT
        assert "AR滤镜封面" in types     # 已并入 taxonomy

    @pytest.mark.asyncio
    async def test_replace_taxonomy_bumps_version_and_persists(self, tmp_path):
        from backend.app.services.viral_taxonomy_loader import ViralTaxonomyLoader

        loader = ViralTaxonomyLoader(store_file=str(tmp_path / "tax.json"))
        v_before = await loader.current_version()

        await loader.replace_taxonomy(
            {
                ElementCode.A_COVER.value: ["自定义1", "自定义2"],
                ElementCode.B_COVER_TEXT.value: ["自定义3"],
            }
        )

        v_after = await loader.current_version()
        assert v_after > v_before

        data = await loader.get()
        assert data["taxonomy"]["A_cover"] == ["自定义1", "自定义2"]
        assert data["taxonomy"]["B_cover_text"] == ["自定义3"]
        # 未传的 element 仍用 DEFAULT(而不是空)
        assert "干货切入" in data["taxonomy"]["D_opening"]

    @pytest.mark.asyncio
    async def test_corrupted_json_falls_back_to_defaults(self, tmp_path):
        from backend.app.services.viral_taxonomy_loader import ViralTaxonomyLoader

        path = tmp_path / "tax.json"
        path.write_text("{ not valid json", encoding="utf-8")

        loader = ViralTaxonomyLoader(store_file=str(path))
        data = await loader.get()
        assert data["version"] == 1
        assert "纯产品图" in data["taxonomy"]["A_cover"]

    @pytest.mark.asyncio
    async def test_propose_rejects_empty_or_too_long(self, tmp_path):
        from backend.app.services.viral_taxonomy_loader import ViralTaxonomyLoader

        loader = ViralTaxonomyLoader(store_file=str(tmp_path / "tax.json"))
        assert await loader.propose_new_type(ElementCode.A_COVER, "") is False
        assert await loader.propose_new_type(ElementCode.A_COVER, "  ") is False
        assert await loader.propose_new_type(ElementCode.A_COVER, "x" * 100) is False
