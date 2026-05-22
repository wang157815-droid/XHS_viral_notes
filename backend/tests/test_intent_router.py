"""intent_router 与 intent_classifier 测试套件。

新架构验收要点：
  1. IntentRouter 只对空输入、明确导出、明确画布修改返回 confidence ≥ 0.90
  2. 所有歧义输入（含 xhs_analysis 候选）confidence < 0.90，由 LLM 分类器处理
  3. 关键边界：能力询问/功能询问 MUST NOT 触发 xhs_analysis
  4. IntentClassifier._parse() 覆盖正/反例 JSON 解析逻辑
"""

from __future__ import annotations

import pytest

from backend.app.application.conversation.intent_classifier import IntentClassifier
from backend.app.application.conversation.intent_router import IntentRouter


# ─── 规则快速路径（置信度 ≥ 0.90 的明确情况） ─────────────────────────────────

class TestIntentRouterHighConfidencePaths:
    """规则层能直接确定意图的场景，confidence ≥ 0.90。"""

    def test_empty_input_returns_unknown(self):
        result = IntentRouter().classify(content="")
        assert result.intent == "unknown"
        assert result.confidence == 0.0
        assert result.clarification_needed is True

    def test_whitespace_input_returns_unknown(self):
        result = IntentRouter().classify(content="   ")
        assert result.intent == "unknown"
        assert result.confidence == 0.0

    def test_export_without_task_returns_clarification(self):
        result = IntentRouter().classify(content="导出 Excel")
        assert result.intent == "export"
        assert result.confidence >= 0.90
        assert result.clarification_needed is True

    def test_export_with_task_no_clarification(self):
        result = IntentRouter().classify(content="导出报告", active_task_id="task_abc")
        assert result.intent == "export"
        assert result.confidence >= 0.90
        assert result.clarification_needed is False

    def test_refine_canvas_with_verb_and_object(self):
        """有活跃任务 + 明确修改动词 + 画布对象词 → refine_canvas"""
        result = IntentRouter().classify(
            content="修改痛点模块，让它更有说服力",
            active_task_id="task_1",
        )
        assert result.intent == "refine_canvas"
        assert result.confidence >= 0.90

    def test_refine_canvas_optimize_seo(self):
        result = IntentRouter().classify(
            content="优化SEO关键词的排列",
            active_task_id="task_1",
        )
        assert result.intent == "refine_canvas"
        assert result.confidence >= 0.90

    def test_refine_canvas_rewrite_model_matrix(self):
        result = IntentRouter().classify(
            content="重写爆文模型矩阵，更简洁",
            active_task_id="task_1",
        )
        assert result.intent == "refine_canvas"
        assert result.confidence >= 0.90


# ─── 低置信度：交 LLM 分类器处理 ──────────────────────────────────────────────

class TestIntentRouterDeferToLLM:
    """规则层置信度不足（< 0.90），应将结果交给 LLM 分类器。"""

    def test_xhs_analysis_request_defers_to_llm(self):
        """xhs_analysis 请求不再由规则层处理，必须由 LLM 判断有无行动意图。"""
        result = IntentRouter().classify(content="帮我搜索小红书防晒爆文模型")
        assert result.confidence < 0.90

    def test_vague_keyword_mention_defers(self):
        result = IntentRouter().classify(content="我想了解巧克力相关的内容")
        assert result.confidence < 0.90

    def test_active_task_followup_defers(self):
        """有活跃任务时的追问类消息，规则层无法确定是 refine 还是 general_qa。"""
        result = IntentRouter().classify(
            content="这个爆文模型还能怎么优化",
            active_task_id="task_1",
        )
        assert result.confidence < 0.90

    def test_refine_without_active_task_defers(self):
        """无活跃任务时的修改指令不应被规则层识别为 refine_canvas。"""
        result = IntentRouter().classify(content="修改痛点模块", active_task_id=None)
        assert result.confidence < 0.90
        assert result.intent != "refine_canvas"

    def test_general_question_defers(self):
        result = IntentRouter().classify(content="你好，有什么功能")
        assert result.confidence < 0.90


# ─── 关键边界：能力/功能询问 MUST NOT 触发 xhs_analysis ────────────────────────

class TestMisfirePreventionBoundaries:
    """这些场景曾经因为关键词误匹配而错误触发 xhs_analysis，必须全部通过。"""

    def test_capability_question_not_xhs_analysis(self):
        """「你可以洞察模型吗」不能触发爆文采集任务。"""
        result = IntentRouter().classify(content="你可以洞察模型吗")
        assert result.intent != "xhs_analysis"

    def test_system_support_question_not_xhs_analysis(self):
        """「系统支持爆文分析吗」是功能询问，不是任务请求。"""
        result = IntentRouter().classify(content="系统支持爆文分析吗")
        assert result.intent != "xhs_analysis"

    def test_concept_explanation_not_xhs_analysis(self):
        """「洞察这个模块是什么意思」是概念询问。"""
        result = IntentRouter().classify(content="洞察这个模块是什么意思")
        assert result.intent != "xhs_analysis"

    def test_keyword_question_not_xhs_analysis(self):
        """只提到功能词「爆文」「模型」但没有搜索动词，不是任务请求。"""
        result = IntentRouter().classify(content="爆文模型是什么")
        assert result.intent != "xhs_analysis"

    def test_active_task_status_question_not_xhs_analysis(self):
        """询问当前任务状态，不触发新任务。"""
        result = IntentRouter().classify(
            content="这个采集任务跑完了吗",
            active_task_id="task_1",
        )
        assert result.intent != "xhs_analysis"

    def test_pure_keyword_no_verb_not_xhs_analysis(self):
        """只有品牌词，没有行动动词，不触发任务。"""
        result = IntentRouter().classify(content="格力空调")
        assert result.intent != "xhs_analysis"


# ─── IntentClassifier 解析逻辑单元测试 ─────────────────────────────────────────

class TestIntentClassifierParsing:
    """直接测试 _parse 静态方法，覆盖 LLM 各种输出格式。"""

    def test_parse_xhs_analysis_with_keywords(self):
        raw = (
            '{"intent":"xhs_analysis","confidence":0.95,'
            '"slots":{"keywords":["格力空调"],"competitor_keywords":[],"skip_competitor":false},'
            '"missing_fields":[],"clarification_question":null,"reason":"明确搜索请求"}'
        )
        result = IntentClassifier._parse(raw, "帮我搜索格力空调")
        assert result.intent == "xhs_analysis"
        assert result.confidence == pytest.approx(0.95)
        assert result.slots["keywords"] == ["格力空调"]
        assert not result.clarification_needed

    def test_parse_general_qa_for_capability_question(self):
        raw = (
            '{"intent":"general_qa","confidence":0.92,'
            '"slots":{},"missing_fields":[],"clarification_question":null,'
            '"reason":"用户在询问系统是否具备爆文分析功能"}'
        )
        result = IntentClassifier._parse(raw, "你可以做爆文分析吗")
        assert result.intent == "general_qa"
        assert result.confidence == pytest.approx(0.92)
        assert not result.clarification_needed

    def test_parse_xhs_analysis_missing_keywords_triggers_clarification(self):
        """xhs_analysis 但 keywords 为空 → 自动标记 clarification_needed。"""
        raw = (
            '{"intent":"xhs_analysis","confidence":0.72,'
            '"slots":{"keywords":[]},'
            '"missing_fields":["keywords"],'
            '"clarification_question":"请告诉我你想搜索的产品或品牌名称？",'
            '"reason":"有执行动词但缺少搜索目标"}'
        )
        result = IntentClassifier._parse(raw, "帮我搜索一下")
        assert result.intent == "xhs_analysis"
        assert result.clarification_needed is True
        assert "keywords" in result.missing_fields
        assert result.clarification_question is not None

    def test_parse_auto_injects_clarification_when_keywords_empty(self):
        """即使 LLM 未设 missing_fields，_parse 也应自动补充 keywords 追问。"""
        raw = (
            '{"intent":"xhs_analysis","confidence":0.80,'
            '"slots":{"keywords":[]},'
            '"missing_fields":[],'
            '"clarification_question":null,'
            '"reason":"test"}'
        )
        result = IntentClassifier._parse(raw, "帮我分析一下")
        assert result.clarification_needed is True
        assert "keywords" in result.missing_fields

    def test_parse_with_competitor_keywords(self):
        raw = (
            '{"intent":"xhs_analysis","confidence":0.95,'
            '"slots":{"keywords":["花西子口红"],"competitor_keywords":["完美日记"],"skip_competitor":false},'
            '"missing_fields":[],"clarification_question":null,"reason":"有竞品"}'
        )
        result = IntentClassifier._parse(raw, "搜索花西子口红，竞品是完美日记")
        assert result.slots["keywords"] == ["花西子口红"]
        assert result.slots["competitor_keywords"] == ["完美日记"]

    def test_parse_skip_competitor_flag(self):
        raw = (
            '{"intent":"xhs_analysis","confidence":0.90,'
            '"slots":{"keywords":["戴森吹风机"],"competitor_keywords":[],"skip_competitor":true},'
            '"missing_fields":[],"clarification_question":null,"reason":"明确跳过竞品"}'
        )
        result = IntentClassifier._parse(raw, "搜索戴森吹风机，不要对比竞品")
        assert result.slots["skip_competitor"] is True

    def test_parse_invalid_intent_falls_back_to_general_qa(self):
        """LLM 返回了非法 intent 名称，应降级为 general_qa。"""
        raw = '{"intent":"task_trigger","confidence":0.85,"slots":{},"missing_fields":[],"clarification_question":null,"reason":"..."}'
        result = IntentClassifier._parse(raw, "随便问一下")
        assert result.intent == "general_qa"

    def test_parse_malformed_json_falls_back(self):
        """完全无效的 JSON，不应抛异常，降级为 general_qa。"""
        result = IntentClassifier._parse("I cannot determine the intent", "随便")
        assert result.intent == "general_qa"
        assert result.confidence > 0  # 有一个默认置信度

    def test_parse_markdown_wrapped_json(self):
        """LLM 可能把 JSON 包在 markdown 代码块里。"""
        raw = '```json\n{"intent":"export","confidence":0.90,"slots":{},"missing_fields":[],"clarification_question":null,"reason":"导出"}\n```'
        result = IntentClassifier._parse(raw, "导出 Excel")
        assert result.intent == "export"

    def test_parse_refine_canvas_with_instruction(self):
        raw = (
            '{"intent":"refine_canvas","confidence":0.91,'
            '"slots":{"module_id":"mod-pain-points","instruction":"重写，更有说服力"},'
            '"missing_fields":[],"clarification_question":null,"reason":"有活跃任务"}'
        )
        result = IntentClassifier._parse(raw, "把痛点模块重新写一遍")
        assert result.intent == "refine_canvas"
        assert result.slots["module_id"] == "mod-pain-points"

    def test_parse_unknown_with_clarification(self):
        raw = (
            '{"intent":"unknown","confidence":0.45,'
            '"slots":{},"missing_fields":[],'
            '"clarification_question":"你是想搜索某个产品，还是想了解系统功能？",'
            '"reason":"输入太模糊"}'
        )
        result = IntentClassifier._parse(raw, "我想做点什么")
        assert result.intent == "unknown"

    def test_parse_confidence_clamped_to_range(self):
        """置信度值超出 [0,1] 范围应被截断。"""
        raw = '{"intent":"general_qa","confidence":1.5,"slots":{},"missing_fields":[],"clarification_question":null,"reason":"test"}'
        result = IntentClassifier._parse(raw, "test")
        assert result.confidence == pytest.approx(1.0)

        raw2 = '{"intent":"general_qa","confidence":-0.1,"slots":{},"missing_fields":[],"clarification_question":null,"reason":"test"}'
        result2 = IntentClassifier._parse(raw2, "test")
        assert result2.confidence == pytest.approx(0.0)


# ─── 工具方法测试（保留） ───────────────────────────────────────────────────────

class TestRouterUtilMethods:
    """extract_keywords / extract_competitor_keywords / extract_target_modules 的正确性。"""

    def test_extract_keywords_from_quotes(self):
        kw = IntentRouter.extract_keywords("帮我搜索「防晒」并生成爆文模型")
        assert kw == ["防晒"]

    def test_extract_keywords_from_verb_pattern(self):
        kw = IntentRouter.extract_keywords("搜索香水爆文模型")
        assert "香水" in kw

    def test_extract_competitor_keywords(self):
        comp = IntentRouter.extract_competitor_keywords("竞品为香奈儿香水和宝格丽")
        assert "香奈儿香水" in comp or "宝格丽" in comp

    def test_extract_target_modules_pain_points(self):
        modules = IntentRouter.extract_target_modules("优化痛点洞察")
        assert "mod-pain-points" in modules

    def test_extract_target_modules_seo(self):
        modules = IntentRouter.extract_target_modules("调整SEO关键词")
        assert "mod-seo-insights" in modules

    def test_export_without_xlsx_still_works(self):
        """只有「导出」词也应触发 export（不需要一定出现文件格式词）。"""
        result = IntentRouter().classify(content="导出当前任务结果", active_task_id="t1")
        assert result.intent == "export"
        assert result.confidence >= 0.90
