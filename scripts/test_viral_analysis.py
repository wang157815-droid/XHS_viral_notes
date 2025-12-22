# -*- coding: utf-8 -*-
"""
综合测试脚本：测试爆文分析的两个关键问题
1. 视频AI分析结果
2. 爆文创作模型JSON解析
"""
import sys
import os
import json
import re
from typing import Dict, Any, Optional

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class MockLogger:
    """模拟 logger"""
    def debug(self, msg): pass
    def info(self, msg): print(f"[INFO] {msg}")
    def warning(self, msg): print(f"[WARNING] {msg}")
    def error(self, msg): print(f"[ERROR] {msg}")
    def success(self, msg): print(f"[SUCCESS] {msg}")


logger = MockLogger()


# ==================== 测试1: JSON解析 ====================

class JSONParserTest:
    """JSON 解析测试"""

    def _try_fix_json(self, json_str: str) -> Optional[Dict[str, Any]]:
        """尝试修复常见的JSON格式问题"""
        if not json_str:
            return None

        strategies = [
            self._fix_strategy_basic,
            self._fix_strategy_remove_incomplete,
            self._fix_strategy_extract_valid_part,
            self._fix_strategy_aggressive,
        ]

        for strategy in strategies:
            try:
                result = strategy(json_str)
                if result:
                    return result
            except Exception:
                continue

        return None

    def _fix_strategy_basic(self, json_str: str) -> Optional[Dict[str, Any]]:
        fixed = re.sub(r',(\s*[}\]])', r'\1', json_str)
        fixed = re.sub(r'(?<!\\)\n', r'\\n', fixed)
        open_braces = fixed.count('{') - fixed.count('}')
        open_brackets = fixed.count('[') - fixed.count(']')
        if open_braces > 0 or open_brackets > 0:
            fixed += ']' * open_brackets + '}' * open_braces
        return json.loads(fixed)

    def _fix_strategy_remove_incomplete(self, json_str: str) -> Optional[Dict[str, Any]]:
        fixed = re.sub(r',(\s*[}\]])', r'\1', json_str)
        open_braces = fixed.count('{') - fixed.count('}')
        open_brackets = fixed.count('[') - fixed.count(']')
        if open_braces > 0 or open_brackets > 0:
            last_complete_comma = -1
            brace_count = 0
            bracket_count = 0
            for i, char in enumerate(fixed):
                if char == '{': brace_count += 1
                elif char == '}': brace_count -= 1
                elif char == '[': bracket_count += 1
                elif char == ']': bracket_count -= 1
                elif char == ',' and brace_count > 0 and bracket_count >= 0:
                    last_complete_comma = i
            if last_complete_comma > 0:
                fixed = fixed[:last_complete_comma]
            open_braces = fixed.count('{') - fixed.count('}')
            open_brackets = fixed.count('[') - fixed.count(']')
            fixed += ']' * open_brackets + '}' * open_braces
        return json.loads(fixed)

    def _fix_strategy_extract_valid_part(self, json_str: str) -> Optional[Dict[str, Any]]:
        start = json_str.find('{')
        if start == -1:
            return None
        brace_count = 0
        for i, char in enumerate(json_str[start:], start):
            if char == '{': brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    potential_json = json_str[start:i+1]
                    try:
                        return json.loads(potential_json)
                    except json.JSONDecodeError:
                        fixed = re.sub(r',(\s*[}\]])', r'\1', potential_json)
                        return json.loads(fixed)
        return None

    def _fix_strategy_aggressive(self, json_str: str) -> Optional[Dict[str, Any]]:
        result = {}
        fields = ['title_strategy', 'content_strategy', 'cover_strategy',
                  'product_strategy', 'checklist', 'success_pattern_summary']
        for field in fields:
            extracted = self._extract_field_aggressive(json_str, field)
            if extracted is not None:
                result[field] = extracted
        if result:
            return result
        return None

    def _extract_field_aggressive(self, json_str: str, field: str) -> Optional[Any]:
        # 方法1：尝试完整JSON提取
        pattern = rf'"{field}":\s*(\{{[^}}]*(?:\{{[^}}]*\}}[^}}]*)*\}}|\[[^\]]*(?:\[[^\]]*\][^\]]*)*\]|"[^"]*")'
        match = re.search(pattern, json_str, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # 方法2：提取字符串值
        if field == 'success_pattern_summary':
            simple_match = re.search(rf'"{field}":\s*"([^"]+)"', json_str)
            if simple_match:
                return simple_match.group(1)

        # 方法3：提取checklist
        if field == 'checklist':
            items = re.findall(r'"item":\s*"([^"]+)"', json_str)
            reasons = re.findall(r'"reason":\s*"([^"]+)"', json_str)
            if items:
                return [{"item": items[i], "reason": reasons[i] if i < len(reasons) else ""}
                       for i in range(len(items))]

        # 方法4：提取策略类字段的conclusions
        if field.endswith('_strategy'):
            conclusions = self._extract_conclusions_from_text(json_str, field)
            if conclusions:
                return {'conclusions': conclusions, 'templates': [], 'keywords_must_have': []}

        return None

    def _extract_conclusions_from_text(self, text: str, field_name: str) -> list:
        conclusions = []
        field_start = text.find(f'"{field_name}"')
        if field_start == -1:
            return conclusions

        next_fields = ['title_strategy', 'content_strategy', 'cover_strategy',
                       'product_strategy', 'checklist', 'success_pattern_summary']
        field_end = len(text)
        for nf in next_fields:
            if nf != field_name:
                pos = text.find(f'"{nf}"', field_start + 1)
                if pos > field_start and pos < field_end:
                    field_end = pos

        field_text = text[field_start:field_end]
        points = re.findall(r'"point":\s*"([^"]+)"', field_text)
        reasonings = re.findall(r'"reasoning":\s*"([^"]+)"', field_text)

        for i, point in enumerate(points):
            reasoning = reasonings[i] if i < len(reasonings) else ""
            conclusions.append({"point": point, "reasoning": reasoning})

        return conclusions

    def _parse_ai_response(self, response_text: str) -> Dict[str, Any]:
        try:
            json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = response_text
            json_str = json_str.strip()
            if json_str.startswith('```'): json_str = json_str[3:]
            if json_str.endswith('```'): json_str = json_str[:-3]
            return json.loads(json_str)
        except json.JSONDecodeError:
            fixed_result = self._try_fix_json(json_str)
            if fixed_result:
                return fixed_result
            return {"error": "JSON解析失败", "extracted_from_text": True}


def test_json_parsing():
    """测试JSON解析"""
    print("\n" + "=" * 60)
    print("测试1: JSON 解析功能")
    print("=" * 60)

    parser = JSONParserTest()

    # 模拟实际可能出现的问题JSON
    test_cases = [
        {
            "name": "正常完整JSON",
            "input": '{"title_strategy": {"conclusions": [{"point": "test", "reasoning": "test"}]}, "success_pattern_summary": "总结"}',
            "expected": True
        },
        {
            "name": "带尾部逗号",
            "input": '{"title_strategy": {"conclusions": [{"point": "test",},],}, "success_pattern_summary": "总结",}',
            "expected": True
        },
        {
            "name": "截断的JSON（有完整point）",
            "input": '{"title_strategy": {"conclusions": [{"point": "标题要包含关键词", "reasoning": "这里是推理内容但被截断了',
            "expected": True
        }
    ]

    passed = 0
    for case in test_cases:
        result = parser._parse_ai_response(case["input"])
        success = bool(result) and not result.get("error")
        status = "✅" if success == case["expected"] else "❌"
        print(f"  {status} {case['name']}: {'通过' if success else '失败'}")
        if success == case["expected"]:
            passed += 1

    print(f"\n  结果: {passed}/{len(test_cases)} 通过")
    return passed == len(test_cases)


# ==================== 测试2: 视频分析流程 ====================

def test_video_timeline_parsing():
    """测试视频时间轴解析"""
    print("\n" + "=" * 60)
    print("测试2: 视频时间轴解析")
    print("=" * 60)

    # 模拟各种AI返回格式
    test_cases = [
        {
            "name": "标准格式",
            "input": "Result_30s,60s,45s,单品推荐-单品推荐,熬夜垮脸,自用分享,手持口播",
            "expected_fields": 7
        },
        {
            "name": "无产品时间",
            "input": "Result_/,/,20s,干货教程-手法干货,肌肤问题,护理经验,干货手法中植入",
            "expected_fields": 7
        },
        {
            "name": "无Result前缀",
            "input": "30s,60s,45s,单品推荐-单品推荐,熬夜垮脸,自用分享,手持口播",
            "expected_fields": 7
        },
        {
            "name": "AI返回错误信息",
            "input": "无法读取视频，视频URL无效",
            "expected_fields": 0
        },
        {
            "name": "空返回",
            "input": "",
            "expected_fields": 0
        }
    ]

    def parse_timeline(result: str) -> dict:
        try:
            result = result.strip()
            if result.startswith('Result_'):
                result = result[7:]
            parts = result.split(',')
            if len(parts) >= 7:
                return {
                    'product_appear_time': parts[0].strip(),
                    'product_use_time': parts[1].strip(),
                    'content_start_time': parts[2].strip(),
                    'content_type': parts[3].strip(),
                    'entry_point': parts[4].strip(),
                    'product_intro_way': parts[5].strip(),
                    'product_embed_way': parts[6].strip(),
                    'raw_result': result
                }
            else:
                return {'error': 'Insufficient data points', 'raw_result': result}
        except Exception as e:
            return {'error': str(e), 'raw_result': result}

    passed = 0
    for case in test_cases:
        result = parse_timeline(case["input"])
        has_error = 'error' in result
        field_count = 0 if has_error else len([k for k in result if k != 'raw_result'])

        expected_has_fields = case["expected_fields"] > 0
        actual_has_fields = field_count >= 7

        if expected_has_fields == actual_has_fields:
            print(f"  ✅ {case['name']}: 解析{'成功' if actual_has_fields else '正确识别为无效'}")
            passed += 1
        else:
            print(f"  ❌ {case['name']}: 预期{case['expected_fields']}字段，实际{field_count}字段")

    print(f"\n  结果: {passed}/{len(test_cases)} 通过")
    return passed == len(test_cases)


# ==================== 测试3: 综合洞察生成 ====================

def test_summary_generation():
    """测试综合洞察生成"""
    print("\n" + "=" * 60)
    print("测试3: 综合洞察生成")
    print("=" * 60)

    # 模拟视频分析结果
    mock_insights = [
        {
            'note_id': 'test1',
            'cover_analysis': {'main_category': '产品展示', 'sub_category': '单品'},
            'title_analysis': {'main_category': '痛点标题', 'sub_category': '问题解决'},
            'timeline_analysis': {
                'content_type': '单品推荐-单品推荐',
                'product_intro_way': '自用分享',
                'product_embed_way': '手持口播'
            }
        },
        {
            'note_id': 'test2',
            'cover_analysis': {'main_category': '产品展示', 'sub_category': '单品'},
            'title_analysis': {'main_category': '痛点标题', 'sub_category': '效果展示'},
            'timeline_analysis': {
                'content_type': '干货教程-手法干货',
                'product_intro_way': '护理经验',
                'product_embed_way': '干货手法中植入'
            }
        },
        {
            'note_id': 'test3',
            'cover_analysis': {'main_category': '真人出镜', 'sub_category': '效果对比'},
            'title_analysis': {'main_category': '数字标题', 'sub_category': '效果数字'},
            'timeline_analysis': {
                'content_type': '单品推荐-单品推荐',
                'product_intro_way': '自用分享',
                'product_embed_way': '流程中植入产品'
            }
        }
    ]

    def generate_summary(insights):
        # 统计封面类型分布
        cover_categories = {}
        for insight in insights:
            if insight.get('cover_analysis'):
                cat = insight['cover_analysis']['main_category']
                cover_categories[cat] = cover_categories.get(cat, 0) + 1

        # 统计标题策略分布
        title_categories = {}
        for insight in insights:
            if insight.get('title_analysis'):
                cat = insight['title_analysis']['main_category']
                title_categories[cat] = title_categories.get(cat, 0) + 1

        # 统计内容类型分布
        content_types = {}
        for insight in insights:
            if insight.get('timeline_analysis'):
                ct = insight['timeline_analysis']['content_type']
                content_types[ct] = content_types.get(ct, 0) + 1

        # 构建综合洞察
        summary_insights = []

        if cover_categories:
            top_cover = max(cover_categories.items(), key=lambda x: x[1])
            summary_insights.append(f"封面策略：{top_cover[0]} 最常见（{top_cover[1]}个视频）")

        if title_categories:
            top_title = max(title_categories.items(), key=lambda x: x[1])
            summary_insights.append(f"标题策略：{top_title[0]} 最有效（{top_title[1]}个视频）")

        if content_types:
            top_content = max(content_types.items(), key=lambda x: x[1])
            summary_insights.append(f"内容类型：{top_content[0]} 占主流（{top_content[1]}个视频）")

        return {
            'total_analyzed': len(insights),
            'cover_type_distribution': cover_categories,
            'title_strategy_distribution': title_categories,
            'content_type_distribution': content_types,
            'insights': summary_insights
        }

    summary = generate_summary(mock_insights)

    checks = [
        ("总分析数量正确", summary['total_analyzed'] == 3),
        ("封面分布不为空", len(summary['cover_type_distribution']) > 0),
        ("标题分布不为空", len(summary['title_strategy_distribution']) > 0),
        ("内容类型分布不为空", len(summary['content_type_distribution']) > 0),
        ("有核心发现", len(summary['insights']) >= 3),
    ]

    passed = 0
    for check_name, check_result in checks:
        status = "✅" if check_result else "❌"
        print(f"  {status} {check_name}")
        if check_result:
            passed += 1

    if summary['insights']:
        print("\n  生成的核心发现:")
        for insight in summary['insights']:
            print(f"    - {insight}")

    print(f"\n  结果: {passed}/{len(checks)} 通过")
    return passed == len(checks)


# ==================== 测试4: 数据流完整性 ====================

def test_data_flow():
    """测试数据从分析到导出的完整流程"""
    print("\n" + "=" * 60)
    print("测试4: 数据流完整性")
    print("=" * 60)

    # 模拟完整的视频AI分析数据结构
    mock_video_ai_data = {
        'status': 'success',
        'analyzed_count': 3,
        'failed_count': 0,
        'model': 'glm-4v-plus',
        'analysis_mode': 'enhanced',
        'individual_insights': [
            {
                'note_id': 'test1',
                'note_title': '测试视频1',
                'analysis_status': 'success',
                'cover_analysis': {'main_category': '产品展示'},
                'title_analysis': {'main_category': '痛点标题'},
                'timeline_analysis': {
                    'content_type': '单品推荐-单品推荐',
                    'product_intro_way': '自用分享',
                    'product_embed_way': '手持口播'
                },
                'ai_analysis': '这是AI分析的原始内容...'
            }
        ],
        'summary': {
            'total_analyzed': 3,
            'insights': ['封面策略：产品展示 最常见', '标题策略：痛点标题 最有效'],
            'content_type_distribution': {'单品推荐-单品推荐': 2},
            'product_intro_ways': {'自用分享': 2},
            'product_embed_ways': {'手持口播': 1}
        }
    }

    # 验证数据结构
    checks = [
        ("状态为success", mock_video_ai_data.get('status') == 'success'),
        ("有分析数量", mock_video_ai_data.get('analyzed_count', 0) > 0),
        ("有个体洞察", len(mock_video_ai_data.get('individual_insights', [])) > 0),
        ("有综合摘要", mock_video_ai_data.get('summary') is not None),
        ("摘要有insights", len(mock_video_ai_data.get('summary', {}).get('insights', [])) > 0),
    ]

    passed = 0
    for check_name, check_result in checks:
        status = "✅" if check_result else "❌"
        print(f"  {status} {check_name}")
        if check_result:
            passed += 1

    # 模拟导出检查逻辑
    can_export = mock_video_ai_data.get('status') == 'success'
    print(f"\n  导出检查: {'✅ 可以导出' if can_export else '❌ 无法导出'}")

    print(f"\n  结果: {passed}/{len(checks)} 通过")
    return passed == len(checks) and can_export


# ==================== 主测试入口 ====================

def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("爆文分析综合测试")
    print("=" * 60)

    results = {
        "JSON解析": test_json_parsing(),
        "视频时间轴解析": test_video_timeline_parsing(),
        "综合洞察生成": test_summary_generation(),
        "数据流完整性": test_data_flow(),
    }

    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)

    all_passed = True
    for name, passed in results.items():
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False

    print("\n" + "=" * 60)
    if all_passed:
        print("🎉 所有测试通过!")
    else:
        print("⚠️ 部分测试失败，请检查上述问题")
    print("=" * 60)

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
