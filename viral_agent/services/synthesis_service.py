# -*- coding: utf-8 -*-
"""
AI综合推理服务
基于所有分析结果进行深度思考推理，生成最终爆文模型

支持知识库注入，使用独立的提示词模块
"""
import json
import os
import re
from typing import Dict, Any, Optional
from loguru import logger

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

# 导入提示词模块
from viral_agent.prompts.viral_model_prompts import (
    build_viral_model_prompt,
    get_system_prompt as get_viral_model_system_prompt,
    parse_viral_model_response
)

# 导入知识库
from viral_agent.prompts.knowledge_base import detect_domain, get_domain_knowledge


class SynthesisService:
    """AI综合推理服务 - 整合所有分析结果生成最终爆文模型"""

    def __init__(self):
        """初始化服务"""
        self.api_base = os.getenv("OPENAI_API_BASE", "https://api.deepseek.com/v1")
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.model_name = os.getenv("MODEL_NAME", "deepseek-chat")
        self.client = None

        if OpenAI and self.api_key:
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.api_base
            )
            logger.info(f"综合推理服务初始化完成，使用模型: {self.model_name}")
        else:
            logger.warning("未配置AI API，综合推理服务不可用")

    def synthesize_final_model(self, analysis_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        基于所有分析结果，进行AI综合思考推理，生成最终爆文模型

        Args:
            analysis_data: 包含所有分析结果的数据

        Returns:
            包含结论和推理的最终爆文模型
        """
        if not self.client:
            logger.warning("AI客户端未初始化，返回空结果")
            return {"status": "disabled", "message": "AI API未配置"}

        try:
            # 1. 提取关键分析数据
            summary_data = self._extract_key_data(analysis_data)

            # 2. 构建综合推理Prompt
            prompt = self._build_synthesis_prompt(summary_data)

            # 3. 调用AI进行推理
            logger.info("开始AI综合推理...")
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {
                        "role": "system",
                        "content": self._get_system_prompt()
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.7,
                max_tokens=4000
            )

            result_text = response.choices[0].message.content
            logger.info("AI综合推理完成")

            # 调试：保存AI原始返回内容（用于排查JSON解析问题）
            try:
                import os as _os
                debug_path = _os.path.join(_os.path.dirname(__file__), '..', '..', 'logs', 'ai_response_debug.txt')
                _os.makedirs(_os.path.dirname(debug_path), exist_ok=True)
                with open(debug_path, 'w', encoding='utf-8') as f:
                    f.write(result_text)
                logger.info(f"AI原始返回已保存到: logs/ai_response_debug.txt")
            except Exception as e:
                logger.debug(f"保存调试文件失败: {e}")

            # 4. 解析AI返回的结果
            final_model = self._parse_ai_response(result_text)
            final_model["status"] = "success"
            final_model["model_used"] = self.model_name

            return final_model

        except Exception as e:
            logger.error(f"AI综合推理失败: {e}")
            return {
                "status": "error",
                "message": str(e)
            }

    def _extract_key_data(self, data: Dict) -> Dict:
        """
        提取关键分析数据用于推理

        重要：保持原始嵌套结构，因为 viral_model_prompts.py 期望的是嵌套格式
        例如：cover_features.get('text_analysis', {}).get('text_coverage_rate', 0)
        """
        # 获取原始的cover_features（保持嵌套结构）
        original_cover = data.get("cover_features", {})
        cover_text_analysis = original_cover.get("text_analysis", {})
        cover_visual_analysis = original_cover.get("visual_analysis", {})

        # 获取原始的product_features（保持嵌套结构）
        original_product = data.get("product_features", {})
        product_timing = original_product.get("product_timing", {})
        marketing_scenes = original_product.get("marketing_scenes", {})
        approach_methods = original_product.get("approach_methods", {})

        return {
            "keyword": data.get("keyword", ""),
            "total_notes": data.get("total_notes", 0),
            "viral_threshold": data.get("viral_threshold", 5000),
            # 标题分析
            "title_patterns": {
                "avg_length": data.get("title_patterns", {}).get("avg_length", 0),
                "top_keywords": data.get("title_patterns", {}).get("top_keywords", [])[:10],
                "common_patterns": data.get("title_patterns", {}).get("common_patterns", [])[:5],
                "length_distribution": data.get("title_patterns", {}).get("length_distribution", {})
            },
            # 内容分析
            "content_patterns": {
                "avg_length": data.get("content_patterns", {}).get("avg_length", 0),
                "structure_patterns": data.get("content_patterns", {}).get("structure_patterns", {}),
                "top_keywords": data.get("content_patterns", {}).get("top_keywords", [])[:10],
                "top_tags": data.get("content_patterns", {}).get("top_tags", [])[:5]
            },
            # 封面分析 - 保持原始嵌套结构！
            "cover_features": {
                "text_analysis": {
                    "text_coverage_rate": cover_text_analysis.get("text_coverage_rate", 0),
                    "avg_text_length": cover_text_analysis.get("avg_text_length", 0),
                    "top_keywords": cover_text_analysis.get("top_keywords", [])[:5]
                },
                "visual_analysis": {
                    "people_image_rate": cover_visual_analysis.get("people_image_rate", 0),
                    "product_image_rate": cover_visual_analysis.get("product_image_rate", 0)
                }
            },
            # 产品分析 - 保持原始嵌套结构！
            "product_features": {
                "timing": product_timing.get("distribution", {}),
                "optimal_strategy": product_timing.get("optimal_strategy", ""),
                "top_scenes": marketing_scenes.get("top_scenes", []),
                "approach_methods": approach_methods.get("distribution", {})
            },
            # 互动数据
            "interaction_features": {
                "avg_liked": data.get("interaction_features", {}).get("avg_liked", 0),
                "avg_collected": data.get("interaction_features", {}).get("avg_collected", 0),
                "avg_comment": data.get("interaction_features", {}).get("avg_comment", 0),
                "avg_collection_rate": data.get("interaction_features", {}).get("avg_collection_rate", 0)
            },
            # AI多模态洞察摘要
            "multimodal_summary": self._extract_multimodal_summary(data),
            # 视频AI洞察摘要
            "video_ai_summary": self._extract_video_ai_summary(data)
        }

    def _extract_multimodal_summary(self, data: Dict) -> str:
        """提取多模态分析摘要"""
        viral_model = data.get("viral_model", {})
        multimodal = viral_model.get("multimodal_insights", {})

        if multimodal.get("status") != "success":
            return "多模态分析未完成"

        insights = multimodal.get("individual_insights", [])
        if not insights:
            return "无多模态洞察数据"

        # 提取关键洞察
        summaries = []
        for insight in insights[:5]:  # 最多取5条
            if insight.get("insights"):
                insight_data = insight.get("insights", {})
                if isinstance(insight_data, dict):
                    # 提取关键字段
                    for key in ["视觉效果", "图文配合", "爆款要素"]:
                        if key in insight_data:
                            summaries.append(f"{key}: {str(insight_data[key])[:100]}")
                elif isinstance(insight_data, str):
                    summaries.append(insight_data[:200])

        return "\n".join(summaries) if summaries else "多模态洞察待提取"

    def _extract_video_ai_summary(self, data: Dict) -> str:
        """提取视频AI分析摘要"""
        viral_model = data.get("viral_model", {})
        video_ai = viral_model.get("video_ai_insights", {})

        if video_ai.get("status") != "success":
            return "视频AI分析未完成"

        summary = video_ai.get("summary", {})
        if isinstance(summary, dict):
            insights = summary.get("insights", [])
            return "\n".join(insights[:5]) if insights else "无视频洞察"
        elif isinstance(summary, str):
            return summary[:500]

        return "视频AI洞察待提取"

    def _get_system_prompt(self) -> str:
        """获取系统提示词（使用独立的提示词模块）"""
        return get_viral_model_system_prompt()

    def _build_synthesis_prompt(self, summary_data: Dict) -> str:
        """
        构建综合推理的Prompt（使用独立的提示词模块）

        支持知识库注入：根据关键词检测领域，自动注入相关知识
        """
        # 检测领域并获取知识库
        keyword = summary_data.get('keyword', '')
        domains = detect_domain(keyword)
        domain_knowledge = get_domain_knowledge(domains) if domains else ""

        # 获取领域名称
        domain_name = ""
        if domains:
            from viral_agent.prompts.knowledge_base import DOMAIN_KEYWORDS, _USE_JSON_CONFIG
            if _USE_JSON_CONFIG:
                try:
                    from viral_agent.config.knowledge_loader import get_knowledge_config
                    config = get_knowledge_config()
                    domain_info = config.get_domain_by_id(domains[0])
                    if domain_info:
                        domain_name = domain_info.get('name', '')
                except Exception:
                    pass
            if not domain_name and domains[0] in DOMAIN_KEYWORDS:
                domain_name = DOMAIN_KEYWORDS[domains[0]].get('name', '')

        logger.info(f"构建爆文模型提示词，检测到领域: {domain_name or '通用'}")

        # 使用提示词模块构建
        return build_viral_model_prompt(
            summary_data=summary_data,
            knowledge=domain_knowledge,
            domain_name=domain_name
        )

    def _parse_ai_response(self, response_text: str) -> Dict[str, Any]:
        """解析AI返回的结果"""
        try:
            # 尝试提取JSON部分
            json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # 尝试直接解析
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
            extracted = self._extract_field_aggressive(json_str, field)
            if extracted is not None:
                result[field] = extracted

        # 如果提取到了主要字段，返回结果
        if result:
            logger.info(f"激进修复成功，提取到字段: {list(result.keys())}")
            return result

        return None

    def _extract_field_aggressive(self, json_str: str, field: str) -> Optional[Any]:
        """
        激进提取单个字段，按优先级尝试多种方法

        Args:
            json_str: JSON字符串
            field: 字段名

        Returns:
            提取的值，或None
        """
        # 方法1：括号配对提取（最可靠）
        result = self._extract_json_by_bracket_matching(json_str, field)
        if result is not None:
            logger.debug(f"字段 {field}: 括号配对提取成功")
            return result

        # 方法2：正则提取完整JSON对象
        pattern = rf'"{field}":\s*(\{{[^}}]*(?:\{{[^}}]*\}}[^}}]*)*\}}|\[[^\]]*(?:\[[^\]]*\][^\]]*)*\]|"[^"]*")'
        match = re.search(pattern, json_str, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group(1))
                logger.debug(f"字段 {field}: 正则提取成功")
                return result
            except json.JSONDecodeError:
                pass

        # 方法3：特殊处理 success_pattern_summary（字符串值，支持转义字符）
        if field == 'success_pattern_summary':
            # 支持转义字符的正则
            simple_match = re.search(rf'"{field}":\s*"((?:[^"\\]|\\.)*)"', json_str)
            if simple_match:
                logger.debug(f"字段 {field}: 字符串提取成功")
                return simple_match.group(1)

        # 方法4：特殊处理 checklist
        if field == 'checklist':
            checklist = self._extract_checklist_from_text(json_str)
            if checklist:
                logger.debug(f"字段 {field}: checklist提取成功，{len(checklist)}项")
                return checklist

        # 方法5：提取策略类字段（含子字段）
        if field.endswith('_strategy'):
            strategy_result = self._extract_strategy_field(json_str, field)
            if strategy_result:
                logger.debug(f"字段 {field}: 策略字段提取成功")
                return strategy_result

        logger.debug(f"字段 {field}: 所有方法均失败")
        return None

    def _extract_strategy_field(self, json_str: str, field: str) -> Optional[Dict[str, Any]]:
        """
        提取策略类字段（包含conclusions、templates、keywords等子字段）

        Args:
            json_str: JSON字符串
            field: 字段名

        Returns:
            策略字段字典，或None
        """
        # 提取conclusions
        conclusions = self._extract_conclusions_from_text(json_str, field)

        # 提取templates（数组）
        templates = self._extract_array_field(json_str, field, 'templates')

        # 提取keywords_must_have（数组）
        keywords = self._extract_array_field(json_str, field, 'keywords_must_have')

        # 提取其他字符串字段
        structure_guide = self._extract_string_subfield(json_str, field, 'structure_guide')
        hooks = self._extract_array_field(json_str, field, 'hooks')
        text_guide = self._extract_string_subfield(json_str, field, 'text_guide')
        visual_guide = self._extract_string_subfield(json_str, field, 'visual_guide')
        timing_guide = self._extract_string_subfield(json_str, field, 'timing_guide')
        scene_guide = self._extract_string_subfield(json_str, field, 'scene_guide')

        # 只要有任何一个子字段成功提取，就返回结果
        if conclusions or templates or keywords:
            result = {
                'conclusions': conclusions if conclusions else [],
                'templates': templates if templates else [],
                'keywords_must_have': keywords if keywords else []
            }
            # 添加其他非空字段
            if structure_guide:
                result['structure_guide'] = structure_guide
            if hooks:
                result['hooks'] = hooks
            if text_guide:
                result['text_guide'] = text_guide
            if visual_guide:
                result['visual_guide'] = visual_guide
            if timing_guide:
                result['timing_guide'] = timing_guide
            if scene_guide:
                result['scene_guide'] = scene_guide
            return result

        return None

    def _extract_array_field(self, json_str: str, parent_field: str, sub_field: str) -> list:
        """提取数组类型的子字段"""
        # 先定位到父字段范围
        field_start = json_str.find(f'"{parent_field}"')
        if field_start == -1:
            return []

        # 找到父字段的大致结束位置
        next_fields = ['title_strategy', 'content_strategy', 'cover_strategy',
                       'product_strategy', 'checklist', 'success_pattern_summary']
        field_end = len(json_str)
        for nf in next_fields:
            if nf != parent_field:
                pos = json_str.find(f'"{nf}"', field_start + 1)
                if pos > field_start and pos < field_end:
                    field_end = pos

        field_text = json_str[field_start:field_end]

        # 在范围内提取子字段数组
        result = self._extract_json_by_bracket_matching(field_text, sub_field)
        if isinstance(result, list):
            return result

        # 备选：用正则提取简单字符串数组
        pattern = rf'"{sub_field}":\s*\[(.*?)\]'
        match = re.search(pattern, field_text, re.DOTALL)
        if match:
            # 提取数组中的字符串
            items = re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(1))
            return items

        return []

    def _extract_string_subfield(self, json_str: str, parent_field: str, sub_field: str) -> str:
        """提取字符串类型的子字段"""
        # 先定位到父字段范围
        field_start = json_str.find(f'"{parent_field}"')
        if field_start == -1:
            return ""

        # 找到父字段的大致结束位置
        next_fields = ['title_strategy', 'content_strategy', 'cover_strategy',
                       'product_strategy', 'checklist', 'success_pattern_summary']
        field_end = len(json_str)
        for nf in next_fields:
            if nf != parent_field:
                pos = json_str.find(f'"{nf}"', field_start + 1)
                if pos > field_start and pos < field_end:
                    field_end = pos

        field_text = json_str[field_start:field_end]

        # 在范围内提取子字段字符串（支持转义字符）
        pattern = rf'"{sub_field}":\s*"((?:[^"\\]|\\.)*)"'
        match = re.search(pattern, field_text, re.DOTALL)
        if match:
            return match.group(1)

        return ""

    def _extract_conclusions_from_text(self, text: str, field_name: str) -> list:
        """从文本中提取结论"""
        conclusions = []

        # 在字段范围内提取
        field_start = text.find(f'"{field_name}"')
        if field_start == -1:
            return conclusions

        # 找到下一个主要字段或文本结尾
        next_fields = ['title_strategy', 'content_strategy', 'cover_strategy',
                       'product_strategy', 'checklist', 'success_pattern_summary']
        field_end = len(text)
        for nf in next_fields:
            if nf != field_name:
                pos = text.find(f'"{nf}"', field_start + 1)
                if pos > field_start and pos < field_end:
                    field_end = pos

        field_text = text[field_start:field_end]

        # 提取 point 和 reasoning（使用支持转义字符的正则）
        points = re.findall(r'"point":\s*"((?:[^"\\]|\\.)*)"', field_text)
        reasonings = re.findall(r'"reasoning":\s*"((?:[^"\\]|\\.)*)"', field_text)

        for i, point in enumerate(points):
            reasoning = reasonings[i] if i < len(reasonings) else ""
            conclusions.append({
                "point": point,
                "reasoning": reasoning
            })

        return conclusions

    def _extract_checklist_from_text(self, text: str) -> list:
        """从文本中提取检查清单"""
        checklist = []
        # 使用支持转义字符的正则
        items = re.findall(r'"item":\s*"((?:[^"\\]|\\.)*)"', text)
        reasons = re.findall(r'"reason":\s*"((?:[^"\\]|\\.)*)"', text)

        for i, item in enumerate(items[:8]):
            reason = reasons[i] if i < len(reasons) else ""
            checklist.append({"item": item, "reason": reason})

        return checklist

    def _extract_json_by_bracket_matching(self, text: str, field: str) -> Optional[Any]:
        """
        通过括号配对提取JSON对象或数组（最可靠的方法）

        Args:
            text: JSON文本
            field: 字段名

        Returns:
            解析后的JSON对象/数组，或None
        """
        # 找到 "field": 的位置
        field_pattern = f'"{field}"\\s*:'
        match = re.search(field_pattern, text)
        if not match:
            return None

        start_pos = match.end()

        # 跳过空白字符
        while start_pos < len(text) and text[start_pos] in ' \t\n\r':
            start_pos += 1

        if start_pos >= len(text):
            return None

        # 确定开始字符
        start_char = text[start_pos]
        if start_char == '{':
            end_char = '}'
        elif start_char == '[':
            end_char = ']'
        elif start_char == '"':
            # 字符串值，使用正则提取
            str_match = re.match(r'"((?:[^"\\]|\\.)*)"', text[start_pos:])
            if str_match:
                return str_match.group(1)
            return None
        else:
            return None

        # 通过括号计数找到匹配的结束位置
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
                    # 找到匹配的结束位置
                    json_str = text[start_pos:i+1]
                    try:
                        return json.loads(json_str)
                    except json.JSONDecodeError:
                        # 尝试修复常见问题后再解析
                        fixed = re.sub(r',(\s*[}\]])', r'\1', json_str)
                        try:
                            return json.loads(fixed)
                        except json.JSONDecodeError:
                            logger.debug(f"括号配对提取的JSON仍无法解析: {json_str[:100]}...")
                            return None

        return None

    def _extract_from_text(self, text: str) -> Dict[str, Any]:
        """从原始文本中提取关键内容"""
        result = {
            "title_strategy": self._extract_section(text, "title_strategy", "标题"),
            "content_strategy": self._extract_section(text, "content_strategy", "内容"),
            "cover_strategy": self._extract_section(text, "cover_strategy", "封面"),
            "product_strategy": self._extract_section(text, "product_strategy", "产品"),
            "checklist": self._extract_checklist(text),
            "success_pattern_summary": self._extract_summary(text),
            "raw_response": text,
            "extracted_from_text": True
        }
        return result

    def _extract_section(self, text: str, section_name: str, cn_name: str) -> Dict[str, Any]:
        """提取指定部分的内容"""
        conclusions = []

        # 尝试提取该部分的内容
        patterns = [
            rf'"{section_name}"[:\s]*\{{(.*?)\}}',
            rf'{cn_name}[策略建议]*[:：]\s*(.+?)(?=\n\n|\n[一二三四五六七八九十]|$)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                content = match.group(1)
                # 提取结论点
                point_matches = re.findall(r'"point"[:\s]*"([^"]+)"', content)
                reasoning_matches = re.findall(r'"reasoning"[:\s]*"([^"]+)"', content)

                for i, point in enumerate(point_matches):
                    reasoning = reasoning_matches[i] if i < len(reasoning_matches) else ""
                    conclusions.append({
                        "point": point,
                        "reasoning": reasoning
                    })

        # 如果没有找到结构化内容，尝试提取普通文本
        if not conclusions:
            # 查找包含关键词的段落
            lines = text.split('\n')
            for line in lines:
                if cn_name in line and ':' in line:
                    content = line.split(':', 1)[-1].strip()
                    if content and len(content) > 10:
                        conclusions.append({
                            "point": content[:100],
                            "reasoning": "从AI原始响应中提取"
                        })
                        break

        return {
            "conclusions": conclusions if conclusions else [{"point": "解析失败，请查看原始响应", "reasoning": "JSON格式问题"}],
            "templates": [],
            "keywords_must_have": []
        }

    def _extract_checklist(self, text: str) -> list:
        """提取检查清单"""
        checklist = []

        # 尝试从JSON中提取
        match = re.search(r'"checklist"[:\s]*\[(.*?)\]', text, re.DOTALL)
        if match:
            items = re.findall(r'"item"[:\s]*"([^"]+)"', match.group(1))
            reasons = re.findall(r'"reason"[:\s]*"([^"]+)"', match.group(1))
            for i, item in enumerate(items):
                reason = reasons[i] if i < len(reasons) else ""
                checklist.append({"item": item, "reason": reason})

        return checklist if checklist else [{"item": "请查看原始AI响应", "reason": "JSON解析失败"}]

    def _extract_summary(self, text: str) -> str:
        """提取成功模式总结"""
        # 尝试从JSON中提取
        match = re.search(r'"success_pattern_summary"[:\s]*"([^"]+)"', text)
        if match:
            return match.group(1)

        # 尝试从普通文本中提取
        patterns = [
            r'成功模式[总结概括]*[:：]\s*(.+?)(?=\n\n|$)',
            r'总结[:：]\s*(.+?)(?=\n\n|$)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                return match.group(1).strip()[:500]

        return "AI响应解析异常，请查看原始数据"


    def synthesize_video_model(self, video_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        视频爆文模型综合推理

        基于视频专属分析数据，生成视频创作指导模型

        Args:
            video_data: 包含视频各维度分析结果的数据
                - cover_stats: 封面分析统计
                - title_stats: 标题分析统计
                - timeline_stats: 时间轴分析统计
                - content_analysis: 内容质量分析
                - product_analysis: 产品深度分析

        Returns:
            视频爆文模型
        """
        if not self.client:
            logger.warning("AI客户端未初始化，返回空视频模型")
            return {"status": "disabled", "message": "AI API未配置"}

        try:
            # 导入视频综合推理提示词
            from viral_agent.prompts.video_synthesis_prompts import (
                build_synthesis_prompt,
                parse_synthesis_result,
                extract_key_recommendations
            )

            # 提取关键数据
            keyword = video_data.get('keyword', '')

            # 构建综合推理提示词
            prompt = build_synthesis_prompt(
                keyword=keyword,
                cover_stats=video_data.get('cover_stats'),
                title_stats=video_data.get('title_stats'),
                timeline_stats=video_data.get('timeline_stats'),
                content_stats=video_data.get('content_analysis'),
                product_stats=video_data.get('product_analysis'),
                sample_notes=video_data.get('sample_notes', [])[:5]
            )

            # 调用AI进行视频综合推理
            logger.info("开始视频爆文模型AI综合推理...")
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {
                        "role": "system",
                        "content": "你是资深短视频运营专家，专精于小红书爆款视频的规律总结和创作指导。请基于数据分析结果，生成可执行的视频创作策略。"
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.7,
                max_tokens=4000
            )

            result_text = response.choices[0].message.content
            logger.info("视频AI综合推理完成")

            # 解析AI返回的结果
            parsed_result = parse_synthesis_result(result_text)

            if parsed_result.get('success'):
                # 提取关键建议
                key_recommendations = extract_key_recommendations(parsed_result)

                return {
                    "status": "success",
                    "model_used": self.model_name,
                    "video_viral_model": parsed_result.get('data', {}),
                    "key_recommendations": key_recommendations,
                    "raw_response": result_text
                }
            else:
                logger.warning(f"视频模型解析有问题: {parsed_result.get('error')}")
                return {
                    "status": "partial",
                    "model_used": self.model_name,
                    "error": parsed_result.get('error'),
                    "raw_response": result_text
                }

        except Exception as e:
            logger.error(f"视频AI综合推理失败: {e}")
            return {
                "status": "error",
                "message": str(e)
            }

    def _extract_video_key_data(self, data: Dict) -> Dict:
        """提取视频分析的关键数据用于推理"""
        return {
            "keyword": data.get("keyword", ""),
            "total_videos": data.get("total_videos", 0),
            # 封面分析
            "cover_analysis": {
                "text_rate": data.get("cover_stats", {}).get("text_analysis", {}).get("has_text_rate", 0),
                "person_rate": data.get("cover_stats", {}).get("layout_analysis", {}).get("has_person_rate", 0),
                "product_rate": data.get("cover_stats", {}).get("layout_analysis", {}).get("has_product_rate", 0),
                "dominant_style": data.get("cover_stats", {}).get("visual_style", {}).get("most_common_style", "")
            },
            # 内容分析
            "content_analysis": {
                "best_hook_type": data.get("content_analysis", {}).get("hook_analysis", {}).get("best_hook_type", ""),
                "avg_hook_score": data.get("content_analysis", {}).get("hook_analysis", {}).get("avg_score", 0),
                "best_structure": data.get("content_analysis", {}).get("structure_analysis", {}).get("best_structure", ""),
                "most_common_curve": data.get("content_analysis", {}).get("emotion_analysis", {}).get("most_common_curve", ""),
                "best_cta_type": data.get("content_analysis", {}).get("ending_analysis", {}).get("best_cta_type", "")
            },
            # 产品分析
            "product_analysis": {
                "avg_appear_time": data.get("product_analysis", {}).get("timing_analysis", {}).get("product_appear", {}).get("avg", 0),
                "early_appear_rate": data.get("product_analysis", {}).get("timing_analysis", {}).get("early_appear_rate", 0),
                "cta_rate": data.get("product_analysis", {}).get("cta_analysis", {}).get("cta_rate", 0),
                "most_effective_cta": data.get("product_analysis", {}).get("cta_analysis", {}).get("most_effective_cta", "")
            },
            # 时间轴分析
            "timeline_analysis": data.get("timeline_stats", {})
        }


def create_synthesis_service() -> SynthesisService:
    """创建综合推理服务实例"""
    return SynthesisService()
