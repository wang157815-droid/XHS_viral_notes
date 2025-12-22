# -*- coding: utf-8 -*-
"""
独立测试JSON解析（无外部依赖）- 使用增强的括号配对方法
"""
import json
import re
from typing import Dict, Any, Optional, List


class MockLogger:
    def debug(self, msg): pass
    def info(self, msg): print(f"[INFO] {msg}")
    def warning(self, msg): print(f"[WARN] {msg}")


logger = MockLogger()


class EnhancedJSONParser:
    """增强的JSON解析器"""

    def _parse_ai_response(self, response_text: str) -> Dict[str, Any]:
        """解析AI返回的结果"""
        try:
            json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = response_text

            json_str = json_str.strip()
            if json_str.startswith('```'):
                json_str = json_str[3:]
            if json_str.endswith('```'):
                json_str = json_str[:-3]

            return json.loads(json_str)

        except json.JSONDecodeError as e:
            logger.warning(f"JSON解析失败，尝试修复: {e}")
            fixed_result = self._try_fix_json(json_str)
            if fixed_result:
                logger.info("JSON修复成功")
                return fixed_result
            return {"error": "解析失败"}

    def _try_fix_json(self, json_str: str) -> Optional[Dict[str, Any]]:
        """尝试修复JSON"""
        # 激进修复
        return self._fix_strategy_aggressive(json_str)

    def _fix_strategy_aggressive(self, json_str: str) -> Optional[Dict[str, Any]]:
        """激进修复：逐个字段提取"""
        result = {}
        fields = ['title_strategy', 'content_strategy', 'cover_strategy',
                  'product_strategy', 'checklist', 'success_pattern_summary']

        for field in fields:
            extracted = self._extract_field_aggressive(json_str, field)
            if extracted is not None:
                result[field] = extracted

        if result:
            logger.info(f"激进修复成功，提取到字段: {list(result.keys())}")
            return result
        return None

    def _extract_field_aggressive(self, json_str: str, field: str) -> Optional[Any]:
        """激进提取单个字段"""
        # 方法1：括号配对提取（最可靠）
        result = self._extract_json_by_bracket_matching(json_str, field)
        if result is not None:
            logger.debug(f"字段 {field}: 括号配对提取成功")
            return result

        # 方法2：特殊处理 success_pattern_summary
        if field == 'success_pattern_summary':
            simple_match = re.search(rf'"{field}":\s*"((?:[^"\\]|\\.)*)"', json_str)
            if simple_match:
                return simple_match.group(1)

        # 方法3：特殊处理 checklist
        if field == 'checklist':
            checklist = self._extract_checklist_from_text(json_str)
            if checklist:
                return checklist

        # 方法4：提取策略类字段
        if field.endswith('_strategy'):
            return self._extract_strategy_field(json_str, field)

        return None

    def _extract_json_by_bracket_matching(self, text: str, field: str) -> Optional[Any]:
        """通过括号配对提取JSON"""
        field_pattern = f'"{field}"\\s*:'
        match = re.search(field_pattern, text)
        if not match:
            return None

        start_pos = match.end()
        while start_pos < len(text) and text[start_pos] in ' \t\n\r':
            start_pos += 1

        if start_pos >= len(text):
            return None

        start_char = text[start_pos]
        if start_char == '{':
            end_char = '}'
        elif start_char == '[':
            end_char = ']'
        elif start_char == '"':
            str_match = re.match(r'"((?:[^"\\]|\\.)*)"', text[start_pos:])
            if str_match:
                return str_match.group(1)
            return None
        else:
            return None

        count = 0
        in_string = False
        escape_next = False

        for i, char in enumerate(text[start_pos:], start_pos):
            if escape_next:
                escape_next = False
                continue
            if char == '\\':
                escape_next = True
                continue
            if char == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == start_char:
                count += 1
            elif char == end_char:
                count -= 1
                if count == 0:
                    json_str = text[start_pos:i+1]
                    try:
                        return json.loads(json_str)
                    except json.JSONDecodeError:
                        fixed = re.sub(r',(\s*[}\]])', r'\1', json_str)
                        try:
                            return json.loads(fixed)
                        except json.JSONDecodeError:
                            return None
        return None

    def _extract_strategy_field(self, json_str: str, field: str) -> Optional[Dict]:
        """提取策略类字段"""
        conclusions = self._extract_conclusions_from_text(json_str, field)
        templates = self._extract_array_field(json_str, field, 'templates')
        keywords = self._extract_array_field(json_str, field, 'keywords_must_have')
        hooks = self._extract_array_field(json_str, field, 'hooks')
        structure_guide = self._extract_string_subfield(json_str, field, 'structure_guide')
        text_guide = self._extract_string_subfield(json_str, field, 'text_guide')
        visual_guide = self._extract_string_subfield(json_str, field, 'visual_guide')
        timing_guide = self._extract_string_subfield(json_str, field, 'timing_guide')
        scene_guide = self._extract_string_subfield(json_str, field, 'scene_guide')

        if conclusions or templates or keywords:
            result = {
                'conclusions': conclusions if conclusions else [],
                'templates': templates if templates else [],
                'keywords_must_have': keywords if keywords else []
            }
            if structure_guide: result['structure_guide'] = structure_guide
            if hooks: result['hooks'] = hooks
            if text_guide: result['text_guide'] = text_guide
            if visual_guide: result['visual_guide'] = visual_guide
            if timing_guide: result['timing_guide'] = timing_guide
            if scene_guide: result['scene_guide'] = scene_guide
            return result
        return None

    def _extract_conclusions_from_text(self, text: str, field_name: str) -> list:
        """提取conclusions"""
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
        points = re.findall(r'"point":\s*"((?:[^"\\]|\\.)*)"', field_text)
        reasonings = re.findall(r'"reasoning":\s*"((?:[^"\\]|\\.)*)"', field_text)

        for i, point in enumerate(points):
            reasoning = reasonings[i] if i < len(reasonings) else ""
            conclusions.append({"point": point, "reasoning": reasoning})
        return conclusions

    def _extract_array_field(self, json_str: str, parent_field: str, sub_field: str) -> list:
        """提取数组子字段"""
        field_start = json_str.find(f'"{parent_field}"')
        if field_start == -1:
            return []

        next_fields = ['title_strategy', 'content_strategy', 'cover_strategy',
                       'product_strategy', 'checklist', 'success_pattern_summary']
        field_end = len(json_str)
        for nf in next_fields:
            if nf != parent_field:
                pos = json_str.find(f'"{nf}"', field_start + 1)
                if pos > field_start and pos < field_end:
                    field_end = pos

        field_text = json_str[field_start:field_end]
        result = self._extract_json_by_bracket_matching(field_text, sub_field)
        if isinstance(result, list):
            return result

        pattern = rf'"{sub_field}":\s*\[(.*?)\]'
        match = re.search(pattern, field_text, re.DOTALL)
        if match:
            items = re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(1))
            return items
        return []

    def _extract_string_subfield(self, json_str: str, parent_field: str, sub_field: str) -> str:
        """提取字符串子字段"""
        field_start = json_str.find(f'"{parent_field}"')
        if field_start == -1:
            return ""

        next_fields = ['title_strategy', 'content_strategy', 'cover_strategy',
                       'product_strategy', 'checklist', 'success_pattern_summary']
        field_end = len(json_str)
        for nf in next_fields:
            if nf != parent_field:
                pos = json_str.find(f'"{nf}"', field_start + 1)
                if pos > field_start and pos < field_end:
                    field_end = pos

        field_text = json_str[field_start:field_end]
        pattern = rf'"{sub_field}":\s*"((?:[^"\\]|\\.)*)"'
        match = re.search(pattern, field_text, re.DOTALL)
        if match:
            return match.group(1)
        return ""

    def _extract_checklist_from_text(self, text: str) -> list:
        """提取checklist"""
        checklist = []
        items = re.findall(r'"item":\s*"((?:[^"\\]|\\.)*)"', text)
        reasons = re.findall(r'"reason":\s*"((?:[^"\\]|\\.)*)"', text)
        for i, item in enumerate(items[:8]):
            reason = reasons[i] if i < len(reasons) else ""
            checklist.append({"item": item, "reason": reason})
        return checklist


# 真实的AI返回内容
REAL_AI_RESPONSE = '''```json
{
  "title_strategy": {
    "conclusions": [
      {
        "point": "标题长度追求"精悍"，以10-20字为主流，且10字以内同样具有竞争力。",
        "reasoning": "数据显示平均标题长度为10.3字，长度分布中"<10字"和"10-20字"区间各占5篇，合计占比超过90%。"
      },
      {
        "point": "标题必须包含核心话题词"柯南"，并搭配角色名或事件关键词。",
        "reasoning": "高频关键词TOP10中，"柯南"作为绝对核心词必然出现。"
      }
    ],
    "templates": [
      "情绪+角色+事件！| 感叹柯南里XXX的XXX瞬间！",
      "干货/盘点+核心词 | 柯南里那些细思极恐的侦探排名"
    ],
    "keywords_must_have": ["柯南", "侦探"]
  },
  "content_strategy": {
    "conclusions": [
      {
        "point": "内容以简短、直接的"分享体"或"观后感体"为主。",
        "reasoning": "平均内容长度仅126.5字，且内容结构特征数据显示，所有结构化元素占比均为0%。"
      }
    ],
    "structure_guide": "采用"钩子（情绪/事件）+ 细节描述 + 互动引导"的简单三段式。",
    "hooks": ["看到柯南里这一幕，我直接破防了！", "永远会被灰原哀的细节打动！"]
  },
  "cover_strategy": {
    "conclusions": [
      {
        "point": "封面以真人出镜（90.9%）为主导策略。",
        "reasoning": "真人出镜比例高达90.9%，远超普通笔记水平。"
      }
    ],
    "text_guide": "叠加1-4个情绪化或悬念式的文字",
    "visual_guide": "首选真人出镜（尤其是表情生动的半身或特写）"
  },
  "product_strategy": {
    "conclusions": [
      {
        "point": "产品植入需高度场景化，作为内容体验的一部分自然呈现。",
        "reasoning": "产品植入分析数据显示所有"提及率"均为0%。"
      }
    ],
    "timing_guide": "避免在标题或文案开头生硬提及产品",
    "scene_guide": "聚焦于"二次元生活化"场景"
  },
  "checklist": [
    {
      "item": "标题是否在20字以内，并包含"柯南"及至少一个角色关键词？",
      "reason": "数据证明短标题和精准关键词是吸引目标粉丝点击的第一步。"
    },
    {
      "item": "封面是否有真人出镜或极具张力的视觉画面？",
      "reason": "真人出镜率超90%，是建立信任和引发共鸣的关键。"
    }
  ],
  "success_pattern_summary": "这批"柯南"爆款笔记的成功核心在于"真人化IP共鸣"与"圈层化轻量分享"的结合。"
}
```'''


def test_real_case():
    """测试真实案例"""
    print("=" * 60)
    print("测试增强的JSON解析器")
    print("=" * 60)

    parser = EnhancedJSONParser()
    result = parser._parse_ai_response(REAL_AI_RESPONSE)

    expected_fields = ['title_strategy', 'content_strategy', 'cover_strategy',
                       'product_strategy', 'checklist', 'success_pattern_summary']

    print("\n字段提取结果:")
    all_success = True
    for field in expected_fields:
        has_field = field in result
        status = "✅" if has_field else "❌"
        print(f"  {status} {field}")
        if not has_field:
            all_success = False

    print("\n子字段检查:")
    if 'title_strategy' in result:
        ts = result['title_strategy']
        print(f"  title_strategy.conclusions: {len(ts.get('conclusions', []))}个")
        print(f"  title_strategy.templates: {len(ts.get('templates', []))}个")
        print(f"  title_strategy.keywords_must_have: {ts.get('keywords_must_have', [])}")

    if 'content_strategy' in result:
        cs = result['content_strategy']
        print(f"  content_strategy.conclusions: {len(cs.get('conclusions', []))}个")
        print(f"  content_strategy.hooks: {cs.get('hooks', [])}")
        print(f"  content_strategy.structure_guide: {'有' if cs.get('structure_guide') else '无'}")

    if 'checklist' in result:
        print(f"  checklist: {len(result['checklist'])}项")

    if 'success_pattern_summary' in result:
        summary = result['success_pattern_summary']
        print(f"  success_pattern_summary: {len(summary)}字")

    print("\n" + "=" * 60)
    if all_success:
        print("🎉 所有字段提取成功！")
    else:
        print("⚠️ 部分字段提取失败")
    print("=" * 60)

    return all_success


if __name__ == "__main__":
    import sys
    success = test_real_case()
    sys.exit(0 if success else 1)
