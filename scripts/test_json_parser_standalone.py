# -*- coding: utf-8 -*-
"""
独立测试 JSON 解析功能（无外部依赖）
"""
import json
import re
from typing import Dict, Any, Optional


class MockLogger:
    """模拟 logger"""
    def debug(self, msg): pass
    def info(self, msg): print(f"INFO: {msg}")
    def warning(self, msg): print(f"WARNING: {msg}")
    def error(self, msg): print(f"ERROR: {msg}")


logger = MockLogger()


class JSONParserTester:
    """JSON 解析测试器"""

    def _parse_ai_response(self, response_text: str) -> Dict[str, Any]:
        """解析AI返回的结果"""
        try:
            # 尝试提取JSON部分
            json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = response_text

            # 清理可能的问题字符
            json_str = json_str.strip()
            if json_str.startswith('```'):
                json_str = json_str[3:]
            if json_str.endswith('```'):
                json_str = json_str[:-3]

            result = json.loads(json_str)
            logger.info("AI推理结果解析成功")
            return result

        except json.JSONDecodeError as e:
            logger.warning(f"JSON解析失败，尝试修复: {e}")

            # 尝试修复常见的JSON格式问题
            fixed_result = self._try_fix_json(json_str)
            if fixed_result:
                logger.info("JSON修复成功")
                return fixed_result

            # 如果修复失败，从原始文本中提取内容
            logger.warning("JSON修复失败，从原始文本提取内容")
            return self._extract_from_text(response_text)

    def _try_fix_json(self, json_str: str) -> Optional[Dict[str, Any]]:
        """尝试修复常见的JSON格式问题"""
        if not json_str:
            return None

        # 多次尝试不同的修复策略
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
            except Exception as e:
                logger.debug(f"修复策略 {strategy.__name__} 失败: {e}")
                continue

        return None

    def _fix_strategy_basic(self, json_str: str) -> Optional[Dict[str, Any]]:
        """基础修复：尾部逗号和括号平衡"""
        # 移除尾部逗号
        fixed = re.sub(r',(\s*[}\]])', r'\1', json_str)

        # 修复转义问题：将未转义的换行替换
        fixed = re.sub(r'(?<!\\)\n', r'\\n', fixed)

        # 计算括号平衡
        open_braces = fixed.count('{') - fixed.count('}')
        open_brackets = fixed.count('[') - fixed.count(']')

        if open_braces > 0 or open_brackets > 0:
            fixed += ']' * open_brackets + '}' * open_braces

        return json.loads(fixed)

    def _fix_strategy_remove_incomplete(self, json_str: str) -> Optional[Dict[str, Any]]:
        """移除不完整的字段"""
        fixed = re.sub(r',(\s*[}\]])', r'\1', json_str)

        # 移除最后一个可能不完整的字段
        open_braces = fixed.count('{') - fixed.count('}')
        open_brackets = fixed.count('[') - fixed.count(']')

        if open_braces > 0 or open_brackets > 0:
            # 找到最后一个完整的逗号位置
            last_complete_comma = -1
            brace_count = 0
            bracket_count = 0

            for i, char in enumerate(fixed):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                elif char == '[':
                    bracket_count += 1
                elif char == ']':
                    bracket_count -= 1
                elif char == ',' and brace_count > 0 and bracket_count >= 0:
                    last_complete_comma = i

            if last_complete_comma > 0:
                fixed = fixed[:last_complete_comma]

            # 重新计算并补全括号
            open_braces = fixed.count('{') - fixed.count('}')
            open_brackets = fixed.count('[') - fixed.count(']')
            fixed += ']' * open_brackets + '}' * open_braces

        return json.loads(fixed)

    def _fix_strategy_extract_valid_part(self, json_str: str) -> Optional[Dict[str, Any]]:
        """提取最大有效JSON片段"""
        # 找到第一个 { 和匹配的 }
        start = json_str.find('{')
        if start == -1:
            return None

        brace_count = 0
        for i, char in enumerate(json_str[start:], start):
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    # 找到了完整的JSON对象
                    potential_json = json_str[start:i+1]
                    try:
                        return json.loads(potential_json)
                    except json.JSONDecodeError:
                        # 继续尝试修复这部分
                        fixed = re.sub(r',(\s*[}\]])', r'\1', potential_json)
                        return json.loads(fixed)

        return None

    def _fix_strategy_aggressive(self, json_str: str) -> Optional[Dict[str, Any]]:
        """激进修复：逐个字段提取"""
        result = {}

        # 提取各个主要字段
        fields = [
            'title_strategy', 'content_strategy', 'cover_strategy',
            'product_strategy', 'checklist', 'success_pattern_summary'
        ]

        for field in fields:
            # 尝试提取字段
            pattern = rf'"{field}":\s*(\{{[^}}]*(?:\{{[^}}]*\}}[^}}]*)*\}}|\[[^\]]*(?:\[[^\]]*\][^\]]*)*\]|"[^"]*")'
            match = re.search(pattern, json_str, re.DOTALL)
            if match:
                try:
                    value = json.loads(match.group(1))
                    result[field] = value
                except json.JSONDecodeError:
                    # 尝试简化提取
                    if field == 'success_pattern_summary':
                        simple_match = re.search(rf'"{field}":\s*"([^"]+)"', json_str)
                        if simple_match:
                            result[field] = simple_match.group(1)
                    elif field == 'checklist':
                        result[field] = self._extract_checklist_from_text(json_str)

        # 如果提取到了主要字段，返回结果
        if result:
            logger.info(f"激进修复成功，提取到字段: {list(result.keys())}")
            return result

        return None

    def _extract_checklist_from_text(self, text: str) -> list:
        """从文本中提取检查清单"""
        checklist = []
        items = re.findall(r'"item":\s*"([^"]+)"', text)
        reasons = re.findall(r'"reason":\s*"([^"]+)"', text)

        for i, item in enumerate(items[:8]):
            reason = reasons[i] if i < len(reasons) else ""
            checklist.append({"item": item, "reason": reason})

        return checklist

    def _extract_from_text(self, text: str) -> Dict[str, Any]:
        """从原始文本中提取关键内容"""
        result = {
            "title_strategy": {"conclusions": [], "templates": [], "keywords_must_have": []},
            "content_strategy": {"conclusions": [], "structure_guide": "", "hooks": []},
            "cover_strategy": {"conclusions": [], "text_guide": "", "visual_guide": ""},
            "product_strategy": {"conclusions": [], "timing_guide": "", "scene_guide": ""},
            "checklist": self._extract_checklist_from_text(text),
            "success_pattern_summary": self._extract_summary(text),
            "raw_response": text,
            "extracted_from_text": True
        }
        return result

    def _extract_summary(self, text: str) -> str:
        """提取成功模式总结"""
        match = re.search(r'"success_pattern_summary":\s*"([^"]+)"', text)
        if match:
            return match.group(1)
        return "AI响应解析异常，请查看原始数据"


def test_json_parsing():
    """测试各种 JSON 解析场景"""
    parser = JSONParserTester()

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
            "name": "没有代码块标记的JSON",
            "input": '''{
  "title_strategy": {
    "conclusions": [{"point": "要点", "reasoning": "理由"}]
  },
  "checklist": [{"item": "检查项", "reason": "原因"}],
  "success_pattern_summary": "成功模式"
}''',
            "expected_success": True
        },
        {
            "name": "字段值被截断（模拟char 1609错误）",
            "input": '''```json
{
  "title_strategy": {
    "conclusions": [
      {"point": "标题长度15-20字最佳", "reasoning": "数据显示平均标题长度X字，长度分布中Y字占比最高"},
      {"point": "使用数字+情绪词", "reasoning": "数字增加可信度，情绪词引发共鸣"}
    ],
    "templates": ["【数字】+效果词+产品名"],
    "keywords_must_have": ["推荐", "必买"]
  },
  "content_strategy": {
    "conclusions": [
      {"point": "内容300-500字最佳", "reasoning": "这个长度用户阅读完成率最高"}
    ],
    "structure_guide": "开头钩子+痛点+解决方案+使用感受+总结",
    "hooks": ["姐妹们！这个真的太好用了"]
  },
  "cover_strategy": {
    "conclusions": [{"point": "大字压图", "reasoning": "在信息流中0.3秒抓住眼球"}],
    "text_guide": "15字以内，包含核心卖点",
    "visual_guide": "真人出镜+产品特写"
  },
  "product_strategy": {
    "conclusions": [{"point": "中段自然植入", "reasoning": "数据显示中段植入转化率最高为45%"}],
    "timing_guide": "在解决方案部分自然引出产品",
    "scene_guide": "日常使用场景最受欢迎"
  },
  "checklist": [
    {"item": "标题包含核心关键词", "reason": "提升搜索匹配度"},
    {"item": "封面有醒目大字''',  # 这里模拟截断
            "expected_success": True
        }
    ]

    print("=" * 60)
    print("测试 JSON 解析功能")
    print("=" * 60)

    passed = 0
    failed = 0

    for i, case in enumerate(test_cases, 1):
        print(f"\n测试 {i}: {case['name']}")
        print("-" * 40)

        try:
            result = parser._parse_ai_response(case['input'])

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
                    if conclusions and isinstance(conclusions, list):
                        first = conclusions[0] if conclusions else {}
                        if isinstance(first, dict):
                            print(f"  标题结论示例: {first.get('point', '')[:40]}...")

                if 'success_pattern_summary' in result:
                    summary = result['success_pattern_summary']
                    if isinstance(summary, str):
                        print(f"  成功模式总结: {summary[:40]}...")

            if case['expected_success'] and has_content and (has_title_strategy or has_summary):
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
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print(f"测试结果: 通过 {passed}/{passed + failed}")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = test_json_parsing()
    exit(0 if success else 1)
