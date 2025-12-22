# -*- coding: utf-8 -*-
"""
测试 synthesis_service 的 JSON 解析功能
"""
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from viral_agent.services.synthesis_service import SynthesisService


def test_json_parsing():
    """测试各种 JSON 解析场景"""
    service = SynthesisService()

    # 测试用例：模拟 AI 返回的各种格式问题
    test_cases = [
        {
            "name": "正常JSON",
            "input": '''```json
{
  "title_strategy": {
    "conclusions": [
      {"point": "标题长度15-20字最佳", "reasoning": "数据显示这个长度点击率最高"}
    ],
    "templates": ["模板1", "模板2"],
    "keywords_must_have": ["关键词1"]
  },
  "content_strategy": {
    "conclusions": [{"point": "内容结构化", "reasoning": "结构化内容收藏率更高"}],
    "structure_guide": "结构建议",
    "hooks": ["钩子1"]
  },
  "cover_strategy": {
    "conclusions": [{"point": "封面要有大字", "reasoning": "大字更吸引眼球"}],
    "text_guide": "文字建议",
    "visual_guide": "视觉建议"
  },
  "product_strategy": {
    "conclusions": [{"point": "中段植入最佳", "reasoning": "中段植入转化率高"}],
    "timing_guide": "时机建议",
    "scene_guide": "场景建议"
  },
  "checklist": [
    {"item": "标题包含关键词", "reason": "提升搜索匹配"},
    {"item": "封面有大字", "reason": "抓住眼球"}
  ],
  "success_pattern_summary": "这批爆款的核心成功要素是..."
}
```''',
            "expected_success": True
        },
        {
            "name": "截断的JSON（缺少闭合括号）",
            "input": '''```json
{
  "title_strategy": {
    "conclusions": [
      {"point": "标题长度15-20字最佳", "reasoning": "数据显示这个长度点击率最高"}
    ],
    "templates": ["模板1", "模板2"],
    "keywords_must_have": ["关键词1"]
  },
  "content_strategy": {
    "conclusions": [{"point": "内容结构化", "reasoning": "结构化内容收藏率更高"}
```''',
            "expected_success": True
        },
        {
            "name": "带尾部逗号的JSON",
            "input": '''```json
{
  "title_strategy": {
    "conclusions": [
      {"point": "标题要点", "reasoning": "推理"},
    ],
    "templates": ["模板1",],
  },
  "success_pattern_summary": "总结内容",
}
```''',
            "expected_success": True
        },
        {
            "name": "带换行的JSON字符串",
            "input": '''```json
{
  "title_strategy": {
    "conclusions": [
      {"point": "标题要点
有换行", "reasoning": "推理内容"}
    ]
  },
  "success_pattern_summary": "总结"
}
```''',
            "expected_success": True
        },
        {
            "name": "字段值被截断的JSON",
            "input": '''```json
{
  "title_strategy": {
    "conclusions": [
      {"point": "标题长度15-20字最佳", "reasoning": "数据显示平均标题长度X字，长度分布中Y字占比最高，说明...因为小红书用户...这里被截断了没有引号结束''',
            "expected_success": True  # 应该能提取部分内容
        },
        {
            "name": "没有代码块标记的JSON",
            "input": '''{
  "title_strategy": {
    "conclusions": [{"point": "要点", "reasoning": "理由"}]
  },
  "checklist": [{"item": "检查项", "reason": "原因"}],
  "success_pattern_summary": "成功模式"
}''',
            "expected_success": True
        }
    ]

    print("=" * 60)
    print("测试 synthesis_service JSON 解析功能")
    print("=" * 60)

    passed = 0
    failed = 0

    for i, case in enumerate(test_cases, 1):
        print(f"\n测试 {i}: {case['name']}")
        print("-" * 40)

        try:
            result = service._parse_ai_response(case['input'])

            # 检查结果
            has_content = bool(result)
            has_title_strategy = 'title_strategy' in result
            has_summary = 'success_pattern_summary' in result
            not_extracted_fallback = not result.get('extracted_from_text', False)

            print(f"  解析结果: {'成功' if has_content else '失败'}")
            print(f"  包含 title_strategy: {has_title_strategy}")
            print(f"  包含 success_pattern_summary: {has_summary}")
            print(f"  使用JSON解析(非文本提取): {not_extracted_fallback}")

            if has_content:
                # 显示部分内容
                if 'title_strategy' in result:
                    conclusions = result['title_strategy'].get('conclusions', [])
                    if conclusions:
                        print(f"  标题结论示例: {conclusions[0].get('point', '')[:30]}...")
                if 'success_pattern_summary' in result:
                    summary = result['success_pattern_summary']
                    if isinstance(summary, str):
                        print(f"  成功模式总结: {summary[:30]}...")

            if case['expected_success'] and has_content:
                print("  ✅ 测试通过")
                passed += 1
            elif not case['expected_success'] and not has_content:
                print("  ✅ 测试通过（预期失败）")
                passed += 1
            else:
                print("  ❌ 测试失败")
                failed += 1

        except Exception as e:
            print(f"  ❌ 解析异常: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"测试结果: 通过 {passed}/{passed + failed}")
    print("=" * 60)

    return failed == 0


def test_real_ai_response():
    """测试真实的 AI 响应（需要配置 API）"""
    print("\n" + "=" * 60)
    print("测试真实 AI 综合推理")
    print("=" * 60)

    service = SynthesisService()

    if not service.client:
        print("⚠️ AI API 未配置，跳过真实测试")
        return True

    # 模拟分析数据
    mock_data = {
        "keyword": "防脱洗发水",
        "total_notes": 20,
        "viral_threshold": 5000,
        "title_patterns": {
            "avg_length": 18,
            "top_keywords": [{"word": "防脱"}, {"word": "推荐"}],
            "common_patterns": ["数字+效果"],
            "length_distribution": {"15-20": 60, "20-25": 30}
        },
        "content_patterns": {
            "avg_length": 500,
            "structure_patterns": {"emoji率": 0.8},
            "top_keywords": [{"word": "效果"}, {"word": "推荐"}],
            "top_tags": [{"tag": "#防脱"}]
        },
        "cover_features": {
            "text_analysis": {
                "text_coverage_rate": 80,
                "avg_text_length": 15,
                "top_keywords": [{"word": "必买"}]
            },
            "visual_analysis": {
                "people_image_rate": 60,
                "product_image_rate": 70
            }
        },
        "product_features": {
            "product_timing": {
                "distribution": {"中段": 50, "结尾": 30},
                "optimal_strategy": "中段植入"
            },
            "marketing_scenes": {"top_scenes": ["日常分享"]},
            "approach_methods": {"distribution": {"软植入": 70}}
        },
        "interaction_features": {
            "avg_liked": 10000,
            "avg_collected": 3000,
            "avg_comment": 200,
            "avg_collection_rate": 0.3
        },
        "viral_model": {}
    }

    try:
        print("正在调用 AI 进行综合推理...")
        result = service.synthesize_final_model(mock_data)

        print(f"\n推理状态: {result.get('status')}")

        if result.get('status') == 'success':
            print("✅ AI 综合推理成功")

            # 检查关键字段
            for field in ['title_strategy', 'content_strategy', 'checklist', 'success_pattern_summary']:
                has_field = field in result
                print(f"  包含 {field}: {has_field}")

            # 显示部分内容
            if 'title_strategy' in result:
                conclusions = result['title_strategy'].get('conclusions', [])
                if conclusions:
                    print(f"\n  📌 标题策略示例:")
                    print(f"     结论: {conclusions[0].get('point', '')[:50]}...")
                    print(f"     推理: {conclusions[0].get('reasoning', '')[:50]}...")

            if 'success_pattern_summary' in result:
                print(f"\n  🎯 成功模式总结:")
                print(f"     {result['success_pattern_summary'][:100]}...")

            return True
        else:
            print(f"❌ AI 推理失败: {result.get('message', '未知错误')}")
            return False

    except Exception as e:
        print(f"❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    # 运行 JSON 解析测试
    json_test_passed = test_json_parsing()

    # 运行真实 AI 测试
    ai_test_passed = test_real_ai_response()

    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    print(f"JSON 解析测试: {'✅ 通过' if json_test_passed else '❌ 失败'}")
    print(f"AI 综合推理测试: {'✅ 通过' if ai_test_passed else '❌ 失败'}")

    sys.exit(0 if (json_test_passed and ai_test_passed) else 1)
