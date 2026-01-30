"""
多模态分析器
支持将图片+文字联合发送给AI进行深度分析
"""
import json
import os
import base64
import requests
from typing import List, Dict, Any, Optional, Callable
from io import BytesIO
from loguru import logger
from openai import OpenAI
from dotenv import load_dotenv

from viral_agent.models.viral_note import ViralNote

# 加载环境变量
load_dotenv()


class MultimodalAnalyzer:
    """多模态分析器（图文联合分析）"""

    def __init__(
        self,
        api_key: Optional[str] = "auto",
        api_base: Optional[str] = None,
        model_name: Optional[str] = None
    ):
        """
        初始化多模态分析器

        Args:
            api_key: API密钥
            api_base: API基础URL
            model_name: 模型名称（必须是支持视觉的模型）
        """
        # 处理api_key
        if api_key == "auto":
            self.api_key = os.getenv("OPENAI_API_KEY")
        else:
            self.api_key = api_key

        # 处理API配置
        if self.api_key:
            self.api_base = api_base or os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
            self.model_name = model_name or os.getenv("MODEL_NAME", "gpt-4-vision-preview")
        else:
            self.api_base = None
            self.model_name = None

        # 配置OpenAI客户端
        if self.api_key:
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.api_base
            )
            logger.info(f"多模态分析器初始化: 模型={self.model_name}, API基址={self.api_base}")
        else:
            self.client = None
            logger.warning("多模态分析器未配置API，功能已禁用")

        # 支持的多模态模型配置
        self.MULTIMODAL_PRESETS = {
            'qwen-vl': {
                'api_base': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
                'model': 'qwen3-vl-plus',
                'description': '通义千问3代VL Plus - 视觉理解能力最强',
                'max_images': 10
            },
            'glm-4v': {
                'api_base': 'https://open.bigmodel.cn/api/paas/v4',
                'model': 'glm-4v',
                'description': '智谱GLM-4V - 完全免费',
                'max_images': 4  # GLM-4V限制较严格，最多4张
            },
            'gpt-4v': {
                'api_base': 'https://api.openai.com/v1',
                'model': 'gpt-4-vision-preview',
                'description': 'OpenAI GPT-4V - 原版多模态',
                'max_images': 10
            }
        }

        # 根据模型设置最大图片数量
        self.max_images_per_note = 4  # 默认值
        model_lower = self.model_name.lower() if self.model_name else ''
        if 'glm-4v' in model_lower or 'glm-4.5v' in model_lower:
            self.max_images_per_note = 4
        elif 'qwen' in model_lower:
            self.max_images_per_note = 10
        elif 'gpt-4' in model_lower:
            self.max_images_per_note = 10

        if self.client:
            logger.info(f"多模态模型图片限制: 每篇笔记最多 {self.max_images_per_note} 张图片")

    @classmethod
    def from_preset(cls, preset_name: str, api_key: str):
        """
        使用预设配置创建多模态分析器

        Args:
            preset_name: 预设名称（qwen-vl, glm-4v, gpt-4v）
            api_key: API密钥

        Returns:
            MultimodalAnalyzer实例
        """
        presets = {
            'qwen-vl': {
                'api_base': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
                'model': 'qwen3-vl-plus',
            },
            'glm-4v': {
                'api_base': 'https://open.bigmodel.cn/api/paas/v4',
                'model': 'glm-4v',
            },
            'gpt-4v': {
                'api_base': 'https://api.openai.com/v1',
                'model': 'gpt-4-vision-preview',
            }
        }

        if preset_name not in presets:
            raise ValueError(f"未知的预设配置: {preset_name}。可用的预设: {list(presets.keys())}")

        preset = presets[preset_name]
        logger.info(f"使用多模态预设配置: {preset_name}")

        return cls(
            api_key=api_key,
            api_base=preset['api_base'],
            model_name=preset['model']
        )

    def analyze_note_with_images(
        self,
        note: ViralNote,
        max_images: int = None
    ) -> Dict[str, Any]:
        """
        分析单篇笔记的图文联合内容

        Args:
            note: 爆款笔记对象
            max_images: 最多分析的图片数量（None则使用模型默认限制）

        Returns:
            多模态分析结果
        """
        if not self.client:
            return {
                'status': 'disabled',
                'message': '多模态分析未启用，请配置支持视觉的AI模型'
            }

        logger.info(f"开始多模态分析: {note.title[:30]}...")

        # 使用模型默认的图片数量限制
        if max_images is None:
            max_images = 9  # 获取最多9张图片

        try:
            # 1. 准备图片列表（获取所有图片）
            all_image_urls = self._get_note_images(note, max_images)
            if not all_image_urls:
                # 尝试获取视频封面
                if note.video_cover:
                    all_image_urls = [note.video_cover]
                    logger.info("视频笔记，使用封面图进行多模态分析")
                else:
                    logger.warning("笔记没有图片也没有视频封面，跳过多模态分析")
                    return {
                        'status': 'no_images',
                        'message': '笔记不包含图片'
                    }

            total_images = len(all_image_urls)
            batch_size = self.max_images_per_note  # 每批的图片数量

            # 2. 如果图片数量超过限制，分批分析
            if total_images > batch_size:
                logger.info(f"笔记有 {total_images} 张图片，将分 {(total_images + batch_size - 1) // batch_size} 批分析")
                return self._analyze_images_in_batches(note, all_image_urls, batch_size)
            else:
                # 图片数量在限制内，直接分析
                logger.info(f"准备分析 {total_images} 张图片")
                return self._analyze_single_batch(note, all_image_urls)

        except Exception as e:
            logger.error(f"多模态分析失败: {e}")
            return {
                'status': 'error',
                'message': str(e)
            }

    def analyze_notes_batch(
        self,
        notes: List[ViralNote],
        sample_count: int = 3,
        keyword: str = "",
        cancel_check: Optional[Callable[[], bool]] = None
    ) -> Dict[str, Any]:
        """
        批量分析多篇笔记的图文联合特征

        Args:
            notes: 爆款笔记列表
            sample_count: 分析的样本数量
            keyword: 搜索关键词
            cancel_check: 取消检查回调，返回True表示已取消

        Returns:
            批量分析结果
        """
        if not self.client:
            return {
                'status': 'disabled',
                'message': '多模态分析未启用'
            }

        logger.info(f"开始批量多模态分析: {sample_count} 篇笔记")

        import time
        results = []
        for i, note in enumerate(notes[:sample_count], 1):
            # 每篇笔记分析前检查取消信号
            if cancel_check and cancel_check():
                logger.info("🛑 多模态分析被取消")
                break

            logger.info(f"分析第 {i}/{sample_count} 篇笔记...")
            result = self.analyze_note_with_images(note)
            if result['status'] == 'success':
                results.append({
                    'note_title': note.title,
                    'insights': result['insights'],
                    'images_count': result['images_analyzed']
                })
            # 添加延迟避免API QPM限制（智谱API并发限制严格，需要更长延迟）
            if i < sample_count:
                # 延迟前也检查取消信号，避免不必要的等待
                if cancel_check and cancel_check():
                    logger.info("🛑 多模态分析被取消")
                    break
                time.sleep(3.0)  # 增加到3秒，避免429错误

        # 生成综合洞察
        if results:
            summary = self._generate_multimodal_summary(results, keyword)
            return {
                'status': 'success',
                'analyzed_count': len(results),
                'individual_insights': results,
                'summary': summary
            }
        else:
            return {
                'status': 'no_results',
                'message': '没有成功分析的笔记'
            }

    def _get_note_images(self, note: ViralNote, max_images: int) -> List[str]:
        """
        获取笔记的所有图片URL

        Args:
            note: 笔记对象
            max_images: 最大图片数量

        Returns:
            图片URL列表
        """
        note_dict = note.to_dict()
        image_urls = []

        # 从image_list获取图片
        if note_dict.get('image_list'):
            for img in note_dict['image_list'][:max_images]:
                if isinstance(img, dict):
                    url = img.get('url_default') or img.get('url_pre') or img.get('url', '')
                else:
                    url = img
                if url:
                    image_urls.append(url)

        return image_urls

    def _build_multimodal_messages(
        self,
        note: ViralNote,
        image_urls: List[str],
        batch_info: str = ""
    ) -> List[Dict[str, Any]]:
        """
        构建多模态分析的messages

        Args:
            note: 笔记对象
            image_urls: 图片URL列表
            batch_info: 分批信息（如"第1批，共2批"）

        Returns:
            OpenAI格式的messages
        """
        # System message
        messages = [
            {
                "role": "system",
                "content": "你是小红书图文内容分析专家，擅长分析图片与文字的配合策略、视觉设计和内容呈现技巧。"
            }
        ]

        # 构建批次说明
        batch_note = f"【注意】{batch_info}\n\n" if batch_info else ""

        # User message with text and images
        content = [
            {
                "type": "text",
                "text": f"""
请深度分析这篇爆款图文笔记的视觉策略和图文配合：

{batch_note}【笔记信息】
标题：{note.title}
正文：{note.desc[:500]}{'...' if len(note.desc) > 500 else ''}
互动数据：👍{note.liked_count} 💖{note.collected_count} 💬{note.comment_count}

【分析要求】
请从以下维度分析这{len(image_urls)}张图片与文字的配合：

1. 图片内容分析
   - 每张图片的主要内容和视觉焦点
   - 图片之间的逻辑关系和叙事顺序
   - 图片质量、构图、光线、色调

2. 图文配合策略
   - 图片与标题的呼应关系
   - 图片与正文的配合逻辑
   - 图片是否有效支撑了文字表达

3. 视觉呈现技巧
   - 封面图的吸引力要素
   - 图片排版和顺序的设计
   - 视觉冲击力和记忆点

4. 成功要素总结
   - 这组图片最成功的地方
   - 对目标用户的吸引力分析
   - 可复用的创作技巧

请以JSON格式返回分析结果。
"""
            }
        ]

        # 添加图片（根据不同API提供商使用不同格式）
        # 通义千问（qwen-vl-*）也支持 image_url，但结构略有不同
        is_qwen = 'qwen' in self.model_name.lower() if self.model_name else False

        for i, url in enumerate(image_urls, 1):
            if is_qwen:
                # 通义千问格式：image_url 但不需要 detail 字段
                content.append({
                    "type": "image_url",
                    "image_url": {"url": url}
                })
            else:
                # 智谱GLM / OpenAI 格式
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": url,
                        "detail": "auto"
                    }
                })

        messages.append({
            "role": "user",
            "content": content
        })

        return messages

    def _parse_multimodal_response(self, response: str) -> Dict[str, Any]:
        """
        解析多模态AI响应

        支持多种格式：
        1. 纯JSON
        2. ```json 代码块
        3. DeepSeek Reasoner 的 <think>...</think> + JSON 格式

        Args:
            response: AI响应文本

        Returns:
            解析后的洞察字典
        """
        import re

        try:
            # 1. 先移除 DeepSeek Reasoner 的思考过程
            clean_response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)
            clean_response = clean_response.strip()

            # 2. 尝试提取 ```json 代码块
            json_match = re.search(r'```json\s*(.*?)\s*```', clean_response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
                return json.loads(json_str.strip())

            # 3. 尝试提取 ``` 代码块（无语言标记）
            code_match = re.search(r'```\s*(.*?)\s*```', clean_response, re.DOTALL)
            if code_match:
                json_str = code_match.group(1)
                return json.loads(json_str.strip())

            # 4. 尝试提取 {...} JSON 对象
            brace_match = re.search(r'\{[\s\S]*\}', clean_response)
            if brace_match:
                return json.loads(brace_match.group())

            # 5. 直接尝试解析整个响应
            return json.loads(clean_response)

        except json.JSONDecodeError:
            # 如果不是JSON格式，返回原始文本（去除think标签后的版本）
            clean_text = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL).strip()
            return {
                'raw_insights': clean_text,
                'parsed': False
            }

    def _generate_multimodal_summary(
        self,
        results: List[Dict[str, Any]],
        keyword: str
    ) -> Dict[str, Any]:
        """
        生成多模态分析的综合摘要

        Args:
            results: 各个笔记的分析结果
            keyword: 关键词

        Returns:
            综合摘要
        """
        # 从分析结果中提取共同模式
        common_patterns = self._extract_common_patterns(results)

        # 生成最佳实践建议
        best_practices = self._generate_best_practices_from_results(results, keyword)

        return {
            'keyword': keyword,
            'total_analyzed': len(results),
            'common_patterns': common_patterns,
            'best_practices': best_practices,
            'individual_results': results
        }

    def _extract_common_patterns(self, results: List[Dict[str, Any]]) -> List[str]:
        """
        从分析结果中提取共同模式

        Args:
            results: 分析结果列表

        Returns:
            共同模式列表
        """
        patterns = []
        pattern_counter = {}

        for result in results:
            insights = result.get('insights', {})

            # 尝试从不同字段提取模式
            if isinstance(insights, dict):
                # 提取视觉风格
                visual_style = insights.get('视觉效果', insights.get('visual_style', ''))
                if visual_style:
                    pattern_counter[f"视觉风格: {str(visual_style)[:30]}"] = \
                        pattern_counter.get(f"视觉风格: {str(visual_style)[:30]}", 0) + 1

                # 提取图文配合
                text_image = insights.get('图文配合', insights.get('text_image_relation', ''))
                if text_image:
                    pattern_counter[f"图文配合: {str(text_image)[:30]}"] = \
                        pattern_counter.get(f"图文配合: {str(text_image)[:30]}", 0) + 1

                # 提取爆款要素
                viral_elements = insights.get('爆款要素', insights.get('viral_elements', ''))
                if viral_elements:
                    pattern_counter[f"爆款要素: {str(viral_elements)[:30]}"] = \
                        pattern_counter.get(f"爆款要素: {str(viral_elements)[:30]}", 0) + 1

        # 按出现次数排序，取前5个
        sorted_patterns = sorted(pattern_counter.items(), key=lambda x: x[1], reverse=True)
        patterns = [p[0] for p in sorted_patterns[:5]]

        # 如果没有提取到模式，返回通用模式
        if not patterns:
            patterns = [
                "封面大字标题突出核心卖点",
                "图片清晰度高，色彩鲜明",
                "产品与场景自然融合"
            ]

        return patterns

    def _generate_best_practices_from_results(
        self,
        results: List[Dict[str, Any]],
        keyword: str
    ) -> List[str]:
        """
        基于分析结果生成最佳实践建议

        Args:
            results: 分析结果列表
            keyword: 关键词

        Returns:
            最佳实践建议列表
        """
        best_practices = []

        # 统计各类特征
        has_text_cover = 0
        has_person = 0
        has_product = 0

        for result in results:
            insights = result.get('insights', {})
            if isinstance(insights, dict):
                # 检查封面文字
                if any(k in str(insights).lower() for k in ['文字', '标题', 'text']):
                    has_text_cover += 1
                # 检查人物
                if any(k in str(insights).lower() for k in ['人物', '真人', 'person', 'people']):
                    has_person += 1
                # 检查产品
                if any(k in str(insights).lower() for k in ['产品', '商品', 'product']):
                    has_product += 1

        total = len(results) if results else 1

        # 基于统计生成建议
        if has_text_cover / total > 0.5:
            best_practices.append(f"封面建议使用大字标题突出{keyword}核心卖点")
        if has_person / total > 0.3:
            best_practices.append("建议真人出镜提升信任感和代入感")
        if has_product / total > 0.5:
            best_practices.append("产品主体清晰展示，突出质感和细节")

        # 补充通用建议
        generic_practices = [
            "图片清晰度要高，避免模糊和过度滤镜",
            "色彩搭配协调，使用高对比度吸引注意",
            "前3张图决定用户是否继续浏览",
            "关键信息在图片中至少重复3次"
        ]

        for practice in generic_practices:
            if len(best_practices) < 5:
                best_practices.append(practice)

        return best_practices[:5]

    def _analyze_single_batch(
        self,
        note: ViralNote,
        image_urls: List[str],
        batch_info: str = "",
        max_retries: int = 3
    ) -> Dict[str, Any]:
        """
        分析单批图片（带429重试机制）

        Args:
            note: 笔记对象
            image_urls: 图片URL列表
            batch_info: 批次信息说明
            max_retries: 最大重试次数

        Returns:
            分析结果
        """
        import time

        # 构建多模态prompt
        messages = self._build_multimodal_messages(note, image_urls, batch_info)

        last_error = None
        for attempt in range(max_retries):
            try:
                # 调用多模态AI
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    temperature=0.7,
                    max_tokens=2000
                )

                # 解析响应
                ai_response = response.choices[0].message.content

                # 调试日志：记录AI原始响应
                logger.debug(f"多模态AI原始响应长度: {len(ai_response)} 字符")
                logger.debug(f"多模态AI原始响应（前800字符）: {ai_response[:800]}")

                # 检查是否有think标签
                if '<think>' in ai_response:
                    logger.info("检测到DeepSeek Reasoner think标签，将自动移除")

                insights = self._parse_multimodal_response(ai_response)

                # 调试日志：记录解析结果
                logger.debug(f"解析结果类型: {type(insights)}")
                if isinstance(insights, dict):
                    logger.debug(f"解析结果键: {list(insights.keys())}")
                    if 'raw_insights' in insights:
                        logger.warning("解析失败，回退到raw_insights模式")
                    else:
                        # 检查各键的值是否为空
                        for key, value in list(insights.items())[:3]:
                            if isinstance(value, str):
                                logger.debug(f"  键'{key}'值长度: {len(value)}")
                            elif isinstance(value, dict):
                                logger.debug(f"  键'{key}'子键: {list(value.keys())[:5]}")

                logger.success("多模态分析完成")
                return {
                    'status': 'success',
                    'insights': insights,
                    'images_analyzed': len(image_urls)
                }

            except Exception as e:
                last_error = e
                error_str = str(e)

                # 检测429错误，使用指数退避重试
                if '429' in error_str or '1302' in error_str:
                    wait_time = (2 ** attempt) * 5  # 5s, 10s, 20s
                    logger.warning(f"API限流(429)，第{attempt + 1}次重试，等待{wait_time}秒...")
                    time.sleep(wait_time)
                else:
                    # 非429错误，直接抛出
                    logger.error(f"分析失败: {e}")
                    raise

        # 所有重试都失败
        logger.error(f"分析失败，已重试{max_retries}次: {last_error}")
        raise last_error

    def _analyze_images_in_batches(
        self,
        note: ViralNote,
        all_image_urls: List[str],
        batch_size: int
    ) -> Dict[str, Any]:
        """
        分批分析图片并汇总结果

        Args:
            note: 笔记对象
            all_image_urls: 所有图片URL列表
            batch_size: 每批的图片数量

        Returns:
            汇总的分析结果
        """
        import time
        total_images = len(all_image_urls)
        batch_count = (total_images + batch_size - 1) // batch_size

        all_insights = []
        batch_summaries = []

        for i in range(batch_count):
            start_idx = i * batch_size
            end_idx = min((i + 1) * batch_size, total_images)
            batch_urls = all_image_urls[start_idx:end_idx]

            logger.info(f"正在分析第 {i+1}/{batch_count} 批（第{start_idx+1}-{end_idx}张图片）...")

            # 批次间添加延迟，避免API限流
            if i > 0:
                time.sleep(2.0)

            try:
                # 构建批次信息
                batch_info_text = f"本次分析第{i+1}批（共{batch_count}批），包含第{start_idx+1}-{end_idx}张图片"

                # 分析这一批
                batch_result = self._analyze_single_batch(note, batch_urls, batch_info_text)

                if batch_result['status'] == 'success':
                    batch_info = {
                        'batch_number': i + 1,
                        'image_range': f'{start_idx+1}-{end_idx}',
                        'insights': batch_result['insights']
                    }
                    all_insights.append(batch_info)

                    # 提取关键信息用于汇总
                    batch_summaries.append(f"第{i+1}批（第{start_idx+1}-{end_idx}张）")

            except Exception as e:
                logger.error(f"第 {i+1} 批分析失败: {e}")
                continue

        # 汇总所有批次的结果
        if all_insights:
            logger.success(f"分批分析完成，共分析 {len(all_insights)} 批，总计 {total_images} 张图片")

            # 生成综合洞察
            combined_insights = self._combine_batch_insights(all_insights, total_images)

            return {
                'status': 'success',
                'insights': combined_insights,
                'images_analyzed': total_images,
                'batch_count': len(all_insights),
                'batch_details': all_insights
            }
        else:
            return {
                'status': 'error',
                'message': '所有批次分析均失败'
            }

    def _combine_batch_insights(
        self,
        all_insights: List[Dict[str, Any]],
        total_images: int
    ) -> Dict[str, Any]:
        """
        合并多个批次的分析结果

        Args:
            all_insights: 所有批次的分析结果
            total_images: 总图片数量

        Returns:
            合并后的综合洞察
        """
        combined = {
            'summary': f'本笔记共{total_images}张图片，分{len(all_insights)}批分析',
            'batches_analyzed': len(all_insights),
            'total_images': total_images,
            'batch_insights': []
        }

        # 收集每批的关键洞察
        for batch_info in all_insights:
            batch_summary = {
                'batch': batch_info['batch_number'],
                'range': batch_info['image_range'],
                'key_findings': batch_info['insights']
            }
            combined['batch_insights'].append(batch_summary)

        # 生成整体观察
        combined['overall_observation'] = (
            f"通过分批分析，完整覆盖了笔记的全部{total_images}张图片。"
            "每批分析都提供了该部分图片的详细洞察，包括内容、配合策略和视觉技巧。"
        )

        return combined
