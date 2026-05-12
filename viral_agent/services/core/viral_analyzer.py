"""
爆文分析器
使用大模型API深度分析爆款笔记并生成爆文模型
"""
import json
import os
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime
from loguru import logger
from openai import OpenAI
from dotenv import load_dotenv
import asyncio

from viral_agent.models.viral_note import ViralNote, ViralAnalysisResult
from viral_agent.services.core.feature_extractor import ViralFeatureExtractor
from viral_agent.services.image.product_analyzer import ProductAnalyzer
from viral_agent.services.image.multimodal_analyzer import MultimodalAnalyzer
from viral_agent.services.export.synthesis_service import SynthesisService

# 视频分析服务
from viral_agent.services.video.video_content_analyzer import VideoContentAnalyzer
from viral_agent.services.video.video_product_analyzer import VideoProductAnalyzer
from viral_agent.services.video.video_ai_analyzer import VideoAIAnalyzer

# 场景方向分析服务
from viral_agent.services.image.scene_analyzer import SceneAnalyzer

# 加载环境变量
load_dotenv()

# 统一的并发限流参数，可通过环境变量配置
# - 云端CPU弱：设为2
# - 普通配置：设为3（默认）
# - 高配置：设为4-5
VIDEO_AI_MAX_CONCURRENT = int(os.getenv('VIDEO_AI_MAX_CONCURRENT', '3'))


class ViralAnalyzer:
    """爆文分析器"""

    def __init__(self, api_key: Optional[str] = "auto", api_base: Optional[str] = None, model_name: Optional[str] = None):
        """
        初始化分析器

        Args:
            api_key: API密钥（支持OpenAI及兼容API）
                    - "auto"（默认）: 自动从环境变量读取
                    - None: 明确禁用AI分析
                    - 具体值: 使用指定的API密钥
            api_base: API基础URL（用于兼容其他API）
            model_name: 模型名称（如gpt-4, deepseek-chat, qwen-max等）
        """
        # 处理api_key
        if api_key == "auto":
            self.api_key = os.getenv("OPENAI_API_KEY")
        else:
            self.api_key = api_key

        # 只有启用AI时才处理其他配置
        if self.api_key:
            self.api_base = api_base or os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
            self.model_name = model_name or os.getenv("MODEL_NAME", "gpt-4")
        else:
            self.api_base = None
            self.model_name = None

        # 配置API（新版本OpenAI SDK，兼容各种大模型）
        if self.api_key:
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.api_base if self.api_base else None
            )
            logger.info(f"AI分析器初始化: 模型={self.model_name}, API基址={self.api_base}")
        else:
            self.client = None
            logger.info("AI分析器初始化: AI功能已禁用")

        # 初始化特征提取器和产品分析器
        self.feature_extractor = ViralFeatureExtractor()
        # 传递AI客户端给产品分析器，启用AI语义分析模式
        # 不显式传入model_name，让ProductAnalyzer优先使用PRODUCT_MODEL_NAME环境变量
        # 产品分析需要稳定的JSON输出，deepseek-reasoner有时返回空响应，推荐使用deepseek-chat
        self.product_analyzer = ProductAnalyzer(
            ai_client=self.client,
            model_name=None  # 让ProductAnalyzer自行读取PRODUCT_MODEL_NAME配置
        )

        # 初始化多模态分析器（检测是否配置了多模态模型）
        multimodal_key = os.getenv("MULTIMODAL_API_KEY") or self.api_key
        multimodal_base = os.getenv("MULTIMODAL_API_BASE")
        multimodal_model = os.getenv("MULTIMODAL_MODEL_NAME")

        if multimodal_key and multimodal_model:
            # 用户显式配置了多模态模型
            self.multimodal_analyzer = MultimodalAnalyzer(
                api_key=multimodal_key,
                api_base=multimodal_base,
                model_name=multimodal_model
            )
            logger.info(f"✓ 多模态分析已启用: {multimodal_model}")
        else:
            self.multimodal_analyzer = None
            logger.info("多模态分析未配置（需要支持视觉的模型，如qwen3-vl-plus、glm-4v-plus）")

        # 初始化综合推理服务
        self.synthesis_service = SynthesisService()
        logger.info("✓ AI综合推理服务已初始化")

        # 初始化视频深度分析器（阶段6工作流优化）
        # 使用 VideoAIAnalyzer（有 analyze_video 方法），而非 MultimodalAnalyzer
        self.video_ai_analyzer = None
        if self.multimodal_analyzer:
            # 当多模态分析器可用时，创建视频AI分析器
            self.video_ai_analyzer = VideoAIAnalyzer()

        self.video_content_analyzer = VideoContentAnalyzer(
            ai_analyzer=self.video_ai_analyzer
        ) if self.video_ai_analyzer else None
        self.video_product_analyzer = VideoProductAnalyzer(
            ai_analyzer=self.video_ai_analyzer
        ) if self.video_ai_analyzer else None

        if self.video_content_analyzer:
            logger.info("✓ 视频内容质量分析器已初始化（4维分析）")
        if self.video_product_analyzer:
            logger.info("✓ 视频产品深度分析器已初始化（12数据点）")

        # 初始化场景方向分析器
        self.scene_analyzer = SceneAnalyzer(
            multimodal_analyzer=self.multimodal_analyzer,
            video_ai_analyzer=self.video_ai_analyzer
        )
        logger.info("✓ 场景方向分析器已初始化")

        # 预设的大模型配置（方便快速切换）
        self.MODEL_PRESETS = {
            'deepseek': {
                'api_base': 'https://api.deepseek.com/v1',
                'model': 'deepseek-chat',
                'description': 'DeepSeek V3 - 高性价比，性能对标GPT-4'
            },
            'qwen': {
                'api_base': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
                'model': 'qwen-max',
                'description': '通义千问 - 阿里云，中文理解最强'
            },
            'glm': {
                'api_base': 'https://open.bigmodel.cn/api/paas/v4',
                'model': 'glm-4-flash',
                'description': '智谱GLM - 完全免费，适合测试'
            }
        }

        logger.info(f"AI分析器初始化: 模型={self.model_name}, API基址={self.api_base}")

    @classmethod
    def from_preset(cls, preset_name: str, api_key: str):
        """
        使用预设配置创建分析器

        Args:
            preset_name: 预设名称（deepseek, qwen, glm）
            api_key: API密钥

        Returns:
            ViralAnalyzer实例
        """
        presets = {
            'deepseek': {
                'api_base': 'https://api.deepseek.com/v1',
                'model': 'deepseek-chat',
            },
            'qwen': {
                'api_base': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
                'model': 'qwen-max',
            },
            'glm': {
                'api_base': 'https://open.bigmodel.cn/api/paas/v4',
                'model': 'glm-4-flash',
            }
        }

        if preset_name not in presets:
            raise ValueError(f"未知的预设配置: {preset_name}。可用的预设: {list(presets.keys())}")

        preset = presets[preset_name]
        logger.info(f"使用预设配置: {preset_name}")

        return cls(
            api_key=api_key,
            api_base=preset['api_base'],
            model_name=preset['model']
        )

    def analyze_viral_notes(
        self,
        notes: List[ViralNote],
        keyword: str,
        threshold: int = 5000,
        analysis_type: str = "all",
        video_source_mode: Optional[str] = None,
        progress_callback: Optional[Callable[[str, Optional[int]], None]] = None
    ) -> ViralAnalysisResult:
        """
        分析爆款笔记并生成爆文模型

        Args:
            notes: 爆款笔记列表
            keyword: 搜索关键词
            threshold: 互动阈值
            analysis_type: 分析类型 ("image"=仅图文, "video"=仅视频, "all"=全部)
            video_source_mode: 视频源模式 ("url"=URL直传, "proxy"=本地下载)，None使用环境变量
            progress_callback: 进度回调函数，接收 (message: str, progress: int) 参数

        Returns:
            分析结果
        """
        def report_progress(message: str, progress: int = None):
            """内部进度报告函数"""
            logger.info(message)
            if progress_callback:
                progress_callback(message, progress)
        # 保存视频源模式供后续使用
        self._video_source_mode = video_source_mode
        report_progress(f"开始分析 {len(notes)} 篇爆款笔记，分析类型: {analysis_type}", 50)

        # ========== 新增：保存原始笔记用于类型分离特征计算 ==========
        # 无论analysis_type是什么，都保存全量数据用于后续填充image/video分离字段
        all_notes_for_type_separation = notes.copy()

        # 根据分析类型筛选笔记
        original_count = len(notes)
        if analysis_type == "image":
            notes = [n for n in notes if n.note_type != '视频']
            logger.info(f"图文模式：筛选出 {len(notes)}/{original_count} 篇图文笔记")
        elif analysis_type == "video":
            notes = [n for n in notes if n.note_type == '视频']
            logger.info(f"视频模式：筛选出 {len(notes)}/{original_count} 篇视频笔记")
        else:
            logger.info(f"全部模式：分析全部 {len(notes)} 篇笔记")

        # 检查筛选后是否有数据
        if not notes:
            logger.warning(f"分析类型 {analysis_type} 没有匹配的笔记")
            return ViralAnalysisResult(
                keyword=keyword,
                analysis_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                total_notes=0,
                viral_threshold=threshold,
                title_patterns={},
                content_patterns={},
                user_patterns={},
                time_patterns={},
                interaction_features={},
                cover_features={},
                all_images_ocr={},
                product_features={},
                video_analysis={},
                viral_model={'status': 'no_notes', 'message': f'没有匹配的{analysis_type}类型笔记'}
            )

        # 1. 提取特征
        report_progress("📊 提取标题/内容/互动特征...", 52)
        features = self.feature_extractor.extract_all_features(notes)

        # 2. 场景方向分析（新增：在标题分析之前）
        report_progress("🎬 开始场景方向分析...", 55)
        try:
            scene_features = self.scene_analyzer.analyze_scenes(notes, keyword)
            features['scene_features'] = scene_features
            report_progress("✅ 场景方向分析完成", 58)
        except Exception as e:
            logger.error(f"场景方向分析失败: {e}")
            features['scene_features'] = {'status': 'error', 'message': str(e)}

        # 3. 分析产品引出特征
        report_progress("📦 分析产品引出特征...", 60)
        product_features = self.product_analyzer.analyze_product_mentions(notes)
        features['product_features'] = product_features

        # 4. 生成爆文模型
        report_progress("🔧 生成爆文模型框架...", 63)
        viral_model = self._generate_viral_model(features, notes)

        # 添加场景策略到爆文模型
        if features.get('scene_features', {}).get('status') == 'success':
            viral_model['scene_strategy'] = features['scene_features'].get('scene_strategy', {})

        # 5. 生成产品策略
        report_progress("💡 生成产品策略...", 65)
        product_strategy = self.product_analyzer.generate_product_strategy(product_features)
        viral_model['product_strategy'] = product_strategy

        # 6. 使用AI深度分析（如果配置了API）
        if self.api_key:
            report_progress("🤖 调用AI深度推理（耗时较长）...", 68)
            ai_insights = self._analyze_with_ai(notes, features, keyword)
            viral_model['ai_insights'] = ai_insights
            report_progress("✅ AI深度推理完成", 72)

        # 7. 使用多模态AI分析图文联合特征（如果配置了多模态模型）
        if self.multimodal_analyzer:
            report_progress("🖼️ 开始多模态分析（图文联合理解）...", 75)
            multimodal_insights = self.multimodal_analyzer.analyze_notes_batch(
                notes=notes,
                sample_count=len(notes),  # 分析全部笔记
                keyword=keyword
            )
            viral_model['multimodal_insights'] = multimodal_insights
        else:
            viral_model['multimodal_insights'] = {
                'status': 'disabled',
                'message': '多模态分析未配置，请在.env中设置MULTIMODAL_MODEL_NAME'
            }

        # 8. 视频AI深度分析（根据分析类型决定是否执行）
        # 图文模式跳过视频分析，视频/全部模式执行视频分析
        if analysis_type == 'image':
            report_progress("📷 图文分析模式：跳过视频AI深度分析", 80)
            viral_model['video_ai_insights'] = {
                'status': 'skipped',
                'message': '图文分析模式，跳过视频分析'
            }
        else:
            video_notes = [n for n in notes if n.note_type == '视频']
            report_progress(f"🎬 检测到 {len(video_notes)} 个视频笔记", 78)

            if video_notes:
                report_progress(f"🎬 开始视频AI深度分析（{len(video_notes)} 个视频）...", 80)
                try:
                    video_ai_insights = self._analyze_videos_with_ai(
                        video_notes,
                        video_source_mode=self._video_source_mode
                    )
                    viral_model['video_ai_insights'] = video_ai_insights
                    report_progress("✅ 视频AI深度分析完成", 85)
                except Exception as e:
                    logger.error(f"视频AI深度分析失败: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    viral_model['video_ai_insights'] = {
                        'status': 'error',
                        'message': f'分析失败: {str(e)}'
                    }
            else:
                report_progress("⚠️ 没有识别到视频笔记", 80)
                viral_model['video_ai_insights'] = {
                    'status': 'no_videos',
                    'message': '没有视频笔记'
                }

        # 在爆文模型中保存分析类型（供Excel导出使用）
        viral_model['analysis_type'] = analysis_type

        # 9. AI综合推理 - 基于所有分析结果生成最终爆文模型
        report_progress("🧠 开始AI综合推理（生成最终爆文模型，可能需要30秒）...", 85)
        try:
            # 构建完整的分析数据供综合推理使用
            full_analysis_data = {
                'keyword': keyword,
                'total_notes': len(notes),
                'viral_threshold': threshold,
                'analysis_type': analysis_type,  # 传递分析类型给综合推理
                'title_patterns': features['title_features'],
                'content_patterns': features['content_features'],
                'user_patterns': features['user_features'],
                'time_patterns': features['time_features'],
                'interaction_features': features['interaction_features'],
                'cover_features': features.get('cover_features', {}),
                'all_images_ocr': features.get('all_images_ocr', {}),
                'product_features': features.get('product_features', {}),
                'viral_model': viral_model
            }

            # 调用综合推理服务
            final_delivery = self.synthesis_service.synthesize_final_model(full_analysis_data)
            viral_model['final_delivery'] = final_delivery

            if final_delivery.get('status') == 'success':
                report_progress("✅ AI综合推理完成，最终爆文模型已生成", 87)
            else:
                report_progress(f"⚠️ AI综合推理状态: {final_delivery.get('status')}", 87)

        except Exception as e:
            logger.error(f"AI综合推理失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            report_progress(f"⚠️ AI综合推理失败: {str(e)[:50]}", 87)
            viral_model['final_delivery'] = {
                'status': 'error',
                'message': str(e)
            }

        # 10. 计算图文/视频分离特征（无论analysis_type是什么，都填充这些字段）
        logger.info("📊 计算图文/视频分离特征...")
        image_notes_all = [n for n in all_notes_for_type_separation if n.note_type != '视频']
        video_notes_all = [n for n in all_notes_for_type_separation if n.note_type == '视频']
        logger.info(f"类型分布: 图文 {len(image_notes_all)} 篇, 视频 {len(video_notes_all)} 篇")

        image_note_features = self._extract_type_features(image_notes_all, '图文')
        video_note_features = self._extract_type_features(video_notes_all, '视频')
        type_summary = self._generate_type_summary(image_notes_all, video_notes_all)

        # 11. 创建分析结果
        result = ViralAnalysisResult(
            keyword=keyword,
            analysis_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            total_notes=len(notes),
            viral_threshold=threshold,
            title_patterns=features['title_features'],
            content_patterns=features['content_features'],
            user_patterns=features['user_features'],
            time_patterns=features['time_features'],
            interaction_features=features['interaction_features'],
            cover_features=features.get('cover_features', {}),
            all_images_ocr=features.get('all_images_ocr', {}),
            product_features=features.get('product_features', {}),
            video_analysis=features.get('video_features', {}),
            scene_features=features.get('scene_features', {}),
            viral_model=viral_model,
            # 新增：图文/视频分离特征
            image_note_features=image_note_features,
            video_note_features=video_note_features,
            type_summary=type_summary
        )

        logger.success(f"爆文分析完成 | 类型摘要: {type_summary.get('recommendation', '')}")
        return result

    def _generate_viral_model(
        self,
        features: Dict[str, Any],
        notes: List[ViralNote]
    ) -> Dict[str, Any]:
        """
        生成爆文模型

        Returns:
            爆文模型字典
        """
        # 提取标题模板
        title_templates = self._extract_title_templates(features['title_features'])

        # 提取内容框架
        content_framework = self._extract_content_framework(features['content_features'])

        # 生成最佳实践建议
        best_practices = self._generate_best_practices(features)

        # 构建爆文模型
        viral_model = {
            'title_strategy': {
                'optimal_length': features['title_features']['avg_length'],
                'must_have_elements': self._get_must_have_elements(features['title_features']),
                'recommended_templates': title_templates,
                'top_keywords': features['title_features']['top_keywords'][:10]
            },
            'content_strategy': {
                'optimal_length': features['content_features']['avg_length'],
                'recommended_structure': content_framework,
                'must_include_elements': self._get_content_must_haves(features['content_features']),
                'top_keywords': features['content_features']['top_keywords'][:15]
            },
            'engagement_strategy': {
                'avg_collection_rate': features['interaction_features']['avg_collection_rate'],
                'interaction_benchmarks': {
                    'minimum': features['interaction_features']['min_interaction'],
                    'average': features['interaction_features']['avg_liked'],
                    'target': features['interaction_features']['max_interaction'] // 2
                }
            },
            'best_practices': best_practices,
            'success_examples': self._get_success_examples(notes)
        }

        # 添加封面策略（如果有封面分析数据）
        if 'cover_features' in features and features['cover_features'].get('enabled', True):
            cover_data = features['cover_features']
            viral_model['cover_strategy'] = self._generate_cover_strategy(cover_data)

        # 添加视频策略
        if 'video_features' in features and features['video_features'].get('enabled', False):
            video_data = features['video_features']
            viral_model['video_strategy'] = self._generate_video_strategy(video_data)
        else:
            viral_model['video_strategy'] = {
                'message': '视频分析功能未启用或无视频数据'
            }

        return viral_model

    def _generate_cover_strategy(self, cover_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        生成封面策略建议

        Args:
            cover_data: 封面分析数据

        Returns:
            封面策略字典
        """
        strategy = {
            'visual_recommendations': [],
            'text_recommendations': [],
            'color_recommendations': [],
            'layout_recommendations': []
        }

        # 文字建议
        if 'text_analysis' in cover_data:
            text_data = cover_data['text_analysis']
            if text_data.get('text_coverage_rate', 0) > 50:
                strategy['text_recommendations'].append(
                    f"建议在封面添加文字，{text_data['text_coverage_rate']}%的爆款封面包含文字"
                )

            if text_data.get('top_keywords'):
                top_words = [kw['word'] for kw in text_data['top_keywords'][:5]]
                strategy['text_recommendations'].append(
                    f"热门封面文字关键词：{', '.join(top_words)}"
                )

        # 颜色建议
        if 'color_analysis' in cover_data:
            color_data = cover_data['color_analysis']
            if color_data.get('color_style_distribution'):
                dominant_style = max(color_data['color_style_distribution'].items(),
                                   key=lambda x: x[1])[0]
                strategy['color_recommendations'].append(
                    f"主流色调风格：{dominant_style}（明亮/暗色）"
                )

            if color_data.get('popular_colors'):
                top_colors = color_data['popular_colors'][:3]
                strategy['color_recommendations'].append(
                    f"流行配色：{', '.join([c['color'] for c in top_colors])}"
                )

        # 布局建议
        if 'layout_analysis' in cover_data:
            layout_data = cover_data['layout_analysis']
            if layout_data.get('orientation_distribution'):
                main_orientation = max(layout_data['orientation_distribution'].items(),
                                     key=lambda x: x[1])[0]
                strategy['layout_recommendations'].append(
                    f"推荐图片方向：{main_orientation}（横版/竖版/方形）"
                )

            if layout_data.get('collage_rate', 0) > 30:
                strategy['layout_recommendations'].append(
                    f"拼图形式较受欢迎（{layout_data['collage_rate']}%使用拼图）"
                )

        # 视觉元素建议
        if 'visual_analysis' in cover_data:
            visual_data = cover_data['visual_analysis']
            if visual_data.get('product_image_rate', 0) > 40:
                strategy['visual_recommendations'].append(
                    f"产品图占比高（{visual_data['product_image_rate']}%），建议突出产品展示"
                )

            if visual_data.get('people_image_rate', 0) > 30:
                strategy['visual_recommendations'].append(
                    f"真人出镜有助提升信任度（{visual_data['people_image_rate']}%包含人物）"
                )

        # 综合建议
        strategy['key_principles'] = [
            "封面是吸引用户点击的第一要素",
            "文字压图能有效传达核心信息",
            "保持视觉风格统一性",
            "使用高质量、清晰的图片",
            "突出对比和视觉焦点"
        ]

        return strategy

    def _generate_video_strategy(self, video_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        生成视频策略建议

        Args:
            video_data: 视频分析数据

        Returns:
            视频策略字典
        """
        strategy = {
            'duration_recommendations': [],
            'structure_recommendations': [],
            'opening_recommendations': [],
            'engagement_tips': []
        }

        # 时长建议
        if 'duration_analysis' in video_data and video_data['duration_analysis'].get('available'):
            duration = video_data['duration_analysis']
            strategy['duration_recommendations'].append(
                duration.get('recommendation', '根据内容调整时长')
            )
            strategy['optimal_duration'] = duration.get('optimal_range', '1-3min')

        # 结构建议
        if 'content_structure' in video_data:
            structure = video_data['content_structure']
            if structure.get('tips'):
                strategy['structure_recommendations'].extend(structure['tips'])

        # 开头策略
        if 'opening_analysis' in video_data:
            opening = video_data['opening_analysis']
            if opening.get('strategies'):
                strategy['opening_recommendations'].extend(opening['strategies'])
            strategy['top_opening_types'] = opening.get('top_types', [])

        # 封面建议
        if 'cover_analysis' in video_data and video_data['cover_analysis'].get('available'):
            cover = video_data['cover_analysis']
            if cover.get('recommendations'):
                strategy['cover_tips'] = cover['recommendations']

        # 互动建议
        if 'engagement_patterns' in video_data and video_data['engagement_patterns'].get('available'):
            engagement = video_data['engagement_patterns']
            if engagement.get('insights'):
                strategy['engagement_tips'].extend(engagement['insights'])

        # 视频制作核心要点
        strategy['key_principles'] = [
            "前3秒决定完播率，必须抓住注意力",
            "字幕提升理解度，照顾静音观看场景",
            "适度的BGM增强情绪感染力",
            "真人出镜提升信任度和亲和力",
            "节奏把控是关键，避免拖沓"
        ]

        return strategy

    def _analyze_with_ai(
        self,
        notes: List[ViralNote],
        features: Dict[str, Any],
        keyword: str
    ) -> Dict[str, Any]:
        """
        使用AI深度分析笔记（集成RAG知识库）

        Returns:
            AI分析洞察
        """
        try:
            # 1. 检索相关知识（RAG）
            knowledge = self._retrieve_knowledge(keyword, notes)

            # 准备分析数据
            sample_notes = notes[:10]  # 取前10篇作为样本
            prompt = self._build_ai_prompt(sample_notes, features, keyword, knowledge)

            # 调用AI分析（新版本API）
            if not self.client:
                raise ValueError("未配置API客户端")

            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": "你是小红书爆款内容分析专家，精通用户心理学、内容营销和数据分析。你能从完整的笔记文本中洞察深层的创作规律，提供可落地的创作建议。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=3000  # 增加token限制，以容纳更详细的深度分析结果
            )

            # 解析响应
            ai_response = response.choices[0].message.content
            insights = self._parse_ai_response(ai_response)

            logger.success("AI分析完成")
            return insights

        except Exception as e:
            logger.error(f"AI分析失败: {e}")
            return {
                'status': 'error',
                'message': str(e),
                'insights': None
            }

    def _retrieve_knowledge(self, keyword: str, notes: List[ViralNote]) -> Dict[str, Any]:
        """
        检索相关知识（RAG）

        Args:
            keyword: 关键词
            notes: 笔记列表

        Returns:
            知识检索结果
        """
        try:
            from viral_agent.services.knowledge.knowledge_retriever import UnifiedKnowledgeRetriever

            retriever = UnifiedKnowledgeRetriever(enable_rag=True)

            # 使用搜索关键词 + 首篇笔记的标题和描述作为检索上下文
            note_title = notes[0].title if notes else ""
            title = f"{keyword} {note_title}"  # 关键词优先
            description = notes[0].desc[:200] if notes and notes[0].desc else ""

            knowledge = retriever.retrieve_knowledge(
                title=title,
                description=description,
                query=f"{keyword}的产品引出和植入技巧，爆款创作规律"
            )

            logger.info(f"知识检索完成: RAG={knowledge['has_rag']}")
            return knowledge

        except Exception as e:
            logger.error(f"知识检索失败: {e}")
            return {
                'structured_knowledge': '',
                'document_knowledge': [],
                'has_json': False,
                'has_rag': False
            }

    def _build_ai_prompt(
        self,
        sample_notes: List[ViralNote],
        features: Dict[str, Any],
        keyword: str,
        knowledge: Dict[str, Any] = None
    ) -> str:
        """
        构建AI分析提示词（集成知识库增强）

        Returns:
            提示词字符串
        """
        # 准备样本数据 - 减少样本数量但提供完整内容
        samples = []
        for note in sample_notes[:3]:  # 从5个减少到3个，为完整内容留出token空间
            # 智能处理超长内容：保留完整性但控制在合理范围
            content = note.desc
            if len(content) > 1000:
                # 超长内容：保留开头500字+结尾200字
                content = content[:500] + '\n\n...(中间省略)...\n\n' + content[-200:]

            samples.append({
                'title': note.title,
                'content': content,  # 完整内容（或智能截断后的内容）
                'interaction': note.interaction_score,
                'liked': note.liked_count,
                'collected': note.collected_count,
                'comment': note.comment_count,
                'tags': note.tags[:5],
                'user': note.nickname
            })

        # 构建知识库部分
        knowledge_section = ""
        if knowledge:
            if knowledge.get('structured_knowledge'):
                knowledge_section += "【结构化知识库（行业规则与模板）】\n"
                knowledge_section += knowledge['structured_knowledge']
                knowledge_section += "\n\n"

            if knowledge.get('document_knowledge'):
                knowledge_section += "【历史成功案例（RAG智能检索）】\n"
                knowledge_section += "\n\n".join(knowledge['document_knowledge'])
                knowledge_section += "\n\n"

        # 使用模块化提示词构建器
        from viral_agent.prompts import build_viral_analysis_prompt
        prompt = build_viral_analysis_prompt(
            keyword=keyword,
            samples=samples,
            features=features,
            knowledge_section=knowledge_section
        )
        return prompt

    def _parse_ai_response(self, response: str) -> Dict[str, Any]:
        """
        解析AI响应

        Returns:
            解析后的洞察字典
        """
        from viral_agent.prompts import parse_viral_analysis_response
        return parse_viral_analysis_response(response)

    def _extract_title_templates(self, title_features: Dict[str, Any]) -> List[str]:
        """
        提取标题模板

        Returns:
            标题模板列表
        """
        templates = []

        # 基于常见模式生成模板
        patterns = title_features.get('common_patterns', [])

        for pattern in patterns[:5]:
            if pattern['pattern'] == '数字开头':
                templates.append("{数字}个{产品/方法}，第{数字}个真的绝了！")
            elif pattern['pattern'] == '测评类':
                templates.append("{品牌}VS{品牌}真实测评，结果让人意外")
            elif pattern['pattern'] == '教程类':
                templates.append("新手必看！{技能}保姆级教程")
            elif pattern['pattern'] == '避坑类':
                templates.append("血泪教训！{产品/行为}的{数字}个大坑")
            elif pattern['pattern'] == '推荐类':
                templates.append("闭眼入！{类别}天花板推荐")

        # 添加通用模板
        templates.extend([
            "终于找到了！{痛点}的解决方法",
            "姐妹们！{发现/体验}真的太{形容词}了",
            "{时间}亲测{数字}款{产品}，只推荐这{数字}个",
            "为什么没人告诉我{秘密/方法}",
            "{人群}必看！{主题}全攻略"
        ])

        return templates[:8]

    def _extract_content_framework(self, content_features: Dict[str, Any]) -> Dict[str, Any]:
        """
        提取内容框架

        Returns:
            内容框架字典
        """
        structure = content_features.get('structure_patterns', {})

        framework = {
            'opening_strategies': [
                "痛点共鸣：直接戳中用户痛点",
                "悬念开场：提出引人好奇的问题",
                "亲身经历：分享真实故事"
            ],
            'body_structure': [],
            'closing_strategies': [
                "总结要点：快速回顾核心内容",
                "行动号召：引导收藏、关注",
                "互动引导：提出问题引发评论"
            ]
        }

        # 根据结构特征添加建议
        if structure.get('has_list', 0) > 30:
            framework['body_structure'].append("列表式：用数字序号组织内容")
        if structure.get('has_steps', 0) > 20:
            framework['body_structure'].append("步骤式：清晰的操作步骤")
        if structure.get('has_tips', 0) > 25:
            framework['body_structure'].append("贴士式：实用小技巧分享")
        if structure.get('has_warning', 0) > 20:
            framework['body_structure'].append("避坑式：注意事项和警示")

        return framework

    def _get_must_have_elements(self, title_features: Dict[str, Any]) -> List[str]:
        """
        获取标题必备要素

        Returns:
            必备要素列表
        """
        elements = []

        # 基于统计数据提取必备要素
        if title_features.get('emoji_usage', {}).get('usage_rate', 0) > 30:
            elements.append("适量使用emoji增加视觉吸引力")

        patterns = title_features.get('common_patterns', [])
        for pattern in patterns:
            if pattern['percentage'] > 25:
                if pattern['pattern'] == '数字开头':
                    elements.append("使用具体数字增加可信度")
                elif pattern['pattern'] == '感叹号结尾':
                    elements.append("感叹号增强情感表达")
                elif pattern['pattern'] == '疑问句式':
                    elements.append("疑问句引发好奇心")

        # 添加基础建议
        elements.extend([
            f"标题长度控制在{int(title_features['avg_length']-5)}-{int(title_features['avg_length']+5)}字",
            "包含1-2个热门关键词"
        ])

        return elements[:5]

    def _get_content_must_haves(self, content_features: Dict[str, Any]) -> List[str]:
        """
        获取内容必备要素

        Returns:
            必备要素列表
        """
        must_haves = []

        structure = content_features.get('structure_patterns', {})

        # 基于结构分析添加建议
        if structure.get('has_cta', 0) > 40:
            must_haves.append("包含明确的行动号召（关注/收藏/评论）")
        if structure.get('has_list', 0) > 30:
            must_haves.append("使用列表或数字序号增强可读性")
        if structure.get('has_tips', 0) > 25:
            must_haves.append("提供实用价值和具体建议")

        # 添加基础要求
        must_haves.extend([
            f"内容长度{int(content_features['avg_length']-100)}-{int(content_features['avg_length']+100)}字为宜",
            "开头30字内抓住注意力",
            "使用换行和emoji提升可读性"
        ])

        return must_haves[:5]

    def _generate_best_practices(self, features: Dict[str, Any]) -> List[str]:
        """
        生成最佳实践建议

        Returns:
            最佳实践列表
        """
        practices = []

        # 基于互动数据的建议
        interaction = features.get('interaction_features', {})
        if interaction.get('avg_collection_rate', 0) > 0.3:
            practices.append("创造高收藏价值的实用内容")

        # 基于时间特征的建议
        time_features = features.get('time_features', {})
        if time_features.get('upload_distribution', {}).get('1-3天', 0) > 30:
            practices.append("保持内容时效性，紧跟热点")

        # 基于用户特征的建议
        user_features = features.get('user_features', {})
        if user_features.get('avg_notes_per_user', 1) > 1.5:
            practices.append("持续产出同类优质内容，培养粉丝粘性")

        # 通用最佳实践
        practices.extend([
            "真实分享，避免过度营销",
            "图片质量要高，首图尤其重要",
            "积极回复评论，提升互动率",
            "选择合适的发布时间（晚上7-9点）",
            "使用3-5个精准标签提升曝光"
        ])

        return practices[:8]

    def _get_success_examples(self, notes: List[ViralNote]) -> List[Dict[str, Any]]:
        """
        获取成功案例

        Returns:
            成功案例列表
        """
        # 按互动分数排序，取前5
        sorted_notes = sorted(notes, key=lambda x: x.interaction_score, reverse=True)

        examples = []
        for note in sorted_notes[:5]:
            examples.append({
                'title': note.title,
                'interaction_score': note.interaction_score,
                'highlights': self._analyze_success_factors(note)
            })

        return examples

    def _analyze_success_factors(self, note: ViralNote) -> List[str]:
        """
        分析单篇笔记的成功因素

        Returns:
            成功因素列表
        """
        factors = []

        # 分析标题
        if len(note.title) > 15:
            factors.append("标题信息量充足")
        if any(char in note.title for char in ['！', '!', '？', '?']):
            factors.append("使用感叹/疑问增强情感")

        # 分析互动 - 防止除零错误
        if note.liked_count > 0:
            if note.collected_count / note.liked_count > 0.3:
                factors.append("高收藏率，内容实用性强")
            if note.comment_count / note.liked_count > 0.05:
                factors.append("高评论率，话题性强")
        else:
            # 如果点赞数为0，但收藏或评论不为0，也算特殊成功因素
            if note.collected_count > 0:
                factors.append("虽无点赞但有收藏，内容有价值")
            if note.comment_count > 0:
                factors.append("虽无点赞但有评论，引发讨论")

        return factors[:3]

    def _analyze_videos_with_ai(
        self,
        video_notes: List[ViralNote],
        video_source_mode: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        使用AI深度分析视频笔记（集成VideoEnhancedAnalyzer，包含封面、标题、时间轴分析）

        Args:
            video_notes: 视频笔记列表
            video_source_mode: 视频源模式 ("url"=URL直传, "proxy"=本地下载)

        Returns:
            视频AI分析结果（包含细粒度分析）
        """
        from viral_agent.services.video.video_enhanced_analyzer import VideoEnhancedAnalyzer
        import asyncio

        try:
            # 使用增强型视频分析器（集成封面、标题、时间轴分析）
            analyzer = VideoEnhancedAnalyzer(
                enable_ai=True,
                video_source_mode=video_source_mode
            )

            # 检查是否支持视频分析
            if not analyzer.ai_analyzer or not analyzer.ai_analyzer._supports_video():
                logger.warning(f"当前配置不支持完整视频分析")
                return {
                    'status': 'unsupported',
                    'message': f'模型不支持视频分析，请在.env中配置支持视频的模型（如 qwen3-vl-plus 或 glm-4v-plus）'
                }

            video_model = analyzer.ai_analyzer.video_model
            logger.info(f"✨ 使用增强型视频分析器（{video_model}）分析 {len(video_notes)} 个视频...")
            logger.info(f"   - 封面分析：分类封面类型")
            logger.info(f"   - 标题分析：识别标题策略")
            logger.info(f"   - 时间轴分析：提取7个关键数据点")

            # 准备笔记数据
            notes_data = []
            for note in video_notes:
                note_data = {
                    'note_id': note.note_id,
                    'video_addr': note.video_addr or '',
                    'title': note.title,
                    'desc': note.desc,
                    'video_cover': note.video_cover if hasattr(note, 'video_cover') and note.video_cover else '',
                    'video_urls': note.video_urls if hasattr(note, 'video_urls') else [],
                    'note_type': note.note_type,
                    'note_url': note.note_url,
                    'interaction_score': note.interaction_score,
                    # 传递互动详细数据供时间轴统计使用
                    'liked_count': note.liked_count,
                    'collected_count': note.collected_count,
                }
                notes_data.append(note_data)

            if not notes_data:
                logger.warning("所有视频笔记都没有有效数据")
                return {
                    'status': 'no_valid_videos',
                    'message': '所有视频笔记都没有有效的视频地址'
                }

            # 使用批量分析功能（支持并发）
            logger.info(f"开始批量分析...")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                # 调用 VideoEnhancedAnalyzer 的批量分析
                batch_result = loop.run_until_complete(
                    analyzer.analyze_batch_videos(
                        notes=notes_data,
                        batch_id=f"viral_batch_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                        max_concurrent=3  # 控制并发数，避免API限流
                    )
                )
            finally:
                loop.close()

            logger.success(f"批量分析完成: 成功 {batch_result.success_count}, 失败 {batch_result.failed_count}")

            # 整理分析结果（包含细粒度数据）
            insights = []
            for result in batch_result.results:
                insight = {
                    'note_id': result.note_id,
                    'note_title': result.title,
                    'video_url': result.video_url,
                    'analysis_status': result.analysis_status,
                    'analysis_time': result.analysis_time
                }

                # 添加封面分析结果
                if result.cover_analysis:
                    insight['cover_analysis'] = {
                        'main_category': result.cover_analysis.main_category,
                        'sub_category': result.cover_analysis.sub_category,
                        'image_type': result.cover_analysis.image_type
                    }

                # 添加标题分析结果
                if result.title_analysis:
                    insight['title_analysis'] = {
                        'main_category': result.title_analysis.main_category,
                        'sub_category': result.title_analysis.sub_category,
                        'keywords': result.title_analysis.keywords
                    }

                # 添加时间轴分析结果（7个关键数据点）
                if result.timeline_analysis:
                    insight['timeline_analysis'] = {
                        'product_appear_time': result.timeline_analysis.product_appear_time,
                        'product_use_time': result.timeline_analysis.product_use_time,
                        'content_start_time': result.timeline_analysis.content_start_time,
                        'content_type': result.timeline_analysis.content_type,
                        'entry_point': result.timeline_analysis.entry_point,
                        'product_intro_way': result.timeline_analysis.product_intro_way,
                        'product_embed_way': result.timeline_analysis.product_embed_way
                    }

                # 添加音画同步分析结果（ASR语音识别）
                if result.av_sync_result:
                    av = result.av_sync_result
                    # 兼容 dataclass 和 dict 两种情况
                    if hasattr(av, 'to_dict'):
                        insight['av_sync_result'] = av.to_dict()
                    elif isinstance(av, dict):
                        insight['av_sync_result'] = av
                    else:
                        from dataclasses import asdict, is_dataclass
                        if is_dataclass(av):
                            insight['av_sync_result'] = asdict(av)

                # 添加帧+ASR联合分析结果
                if result.frame_asr_analysis:
                    fa = result.frame_asr_analysis
                    if hasattr(fa, 'to_dict'):
                        insight['frame_asr_analysis'] = fa.to_dict()
                    elif isinstance(fa, dict):
                        insight['frame_asr_analysis'] = fa
                    else:
                        from dataclasses import asdict, is_dataclass
                        if is_dataclass(fa):
                            insight['frame_asr_analysis'] = asdict(fa)

                if result.error_message:
                    insight['error'] = result.error_message

                # 添加AI原始分析结果（用于导出显示）
                if result.ai_raw_response:
                    ai_analysis = result.ai_raw_response.get('analysis', '')
                    if ai_analysis:
                        insight['ai_analysis'] = ai_analysis

                # 查找对应的互动分数
                for note in notes_data:
                    if note.get('note_id') == result.note_id:
                        insight['interaction_score'] = note.get('interaction_score', 0)
                        break

                insights.append(insight)

            # 生成综合洞察
            summary = self._summarize_video_enhanced_insights(insights)

            # 将批量统计数据添加到summary中（用于Excel展示PRD对齐数据）
            if isinstance(summary, dict):
                # 添加各维度统计数据
                summary['timeline_stats'] = batch_result.timeline_stats
                summary['cover_stats'] = batch_result.cover_stats
                summary['title_stats'] = batch_result.title_stats

            # ========== 阶段6新增：深度视频分析 ==========
            content_analysis = None
            product_analysis = None
            video_viral_model = None

            # 并行执行内容分析和产品分析（性能优化：从串行改为并行）
            async def _run_parallel_video_analysis():
                """并行执行视频内容分析和产品分析"""
                tasks = []
                task_names = []

                if self.video_content_analyzer:
                    tasks.append(
                        self.video_content_analyzer.analyze_batch(
                            notes=notes_data,
                            max_concurrent=VIDEO_AI_MAX_CONCURRENT
                        )
                    )
                    task_names.append('content')

                if self.video_product_analyzer:
                    tasks.append(
                        self.video_product_analyzer.analyze_batch(
                            notes=notes_data,
                            max_concurrent=VIDEO_AI_MAX_CONCURRENT
                        )
                    )
                    task_names.append('product')

                if not tasks:
                    return None, None

                # 并行执行，捕获异常不影响其他任务
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # 解析结果
                content_result = None
                product_result = None

                for i, (name, result) in enumerate(zip(task_names, results)):
                    if isinstance(result, Exception):
                        logger.warning(f"{name}分析失败: {result}")
                    elif name == 'content':
                        content_result = result
                        logger.success("✅ 内容质量分析完成")
                    elif name == 'product':
                        product_result = result
                        logger.success("✅ 产品深度分析完成")

                return content_result, product_result

            # 执行并行视频分析
            if self.video_content_analyzer or self.video_product_analyzer:
                try:
                    logger.info(f"执行视频深度分析（并行模式，并发数={VIDEO_AI_MAX_CONCURRENT}）...")
                    analysis_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(analysis_loop)
                    try:
                        content_analysis, product_analysis = analysis_loop.run_until_complete(
                            _run_parallel_video_analysis()
                        )
                    finally:
                        analysis_loop.close()
                except Exception as e:
                    logger.warning(f"视频深度分析失败: {e}")

            # 视频爆文模型综合推理
            if self.synthesis_service:
                try:
                    logger.info("执行视频爆文模型综合推理...")
                    video_viral_model = self.synthesis_service.synthesize_video_model({
                        'keyword': notes_data[0].get('title', '')[:10] if notes_data else '',
                        'cover_stats': batch_result.cover_stats,
                        'title_stats': batch_result.title_stats,
                        'timeline_stats': batch_result.timeline_stats,
                        'content_analysis': content_analysis,
                        'product_analysis': product_analysis,
                        'sample_notes': notes_data[:5]
                    })
                    logger.success(f"✅ 视频爆文模型生成完成")
                except Exception as e:
                    logger.warning(f"视频爆文模型生成失败: {e}")
            # ========== 阶段6新增结束 ==========

            return {
                'status': 'success',
                'analyzed_count': batch_result.success_count,
                'failed_count': batch_result.failed_count,
                'model': video_model,
                'analysis_mode': 'enhanced',  # 标记为增强模式
                'individual_insights': insights,
                'summary': summary,
                # 阶段6新增：深度分析结果
                'content_analysis': content_analysis,
                'product_analysis': product_analysis,
                'video_viral_model': video_viral_model
            }

        except Exception as e:
            logger.error(f"视频AI分析失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {
                'status': 'error',
                'message': str(e)
            }

    def _summarize_video_enhanced_insights(self, insights: List[Dict]) -> Dict[str, Any]:
        """
        汇总增强型视频分析洞察（包含封面、标题、时间轴等细粒度数据）

        Args:
            insights: 单个视频的增强分析结果列表

        Returns:
            综合洞察字典
        """
        try:
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

            # 统计产品引出方式
            intro_ways = {}
            for insight in insights:
                if insight.get('timeline_analysis'):
                    way = insight['timeline_analysis']['product_intro_way']
                    if way and way != '/':
                        intro_ways[way] = intro_ways.get(way, 0) + 1

            # 统计产品植入方式
            embed_ways = {}
            for insight in insights:
                if insight.get('timeline_analysis'):
                    way = insight['timeline_analysis']['product_embed_way']
                    if way and way != '/':
                        embed_ways[way] = embed_ways.get(way, 0) + 1

            # 构建综合洞察
            summary = {
                'total_analyzed': len(insights),
                'cover_type_distribution': cover_categories,
                'title_strategy_distribution': title_categories,
                'content_type_distribution': content_types,
                'product_intro_ways': intro_ways,
                'product_embed_ways': embed_ways,
                'insights': []
            }

            # 添加核心发现
            if cover_categories:
                top_cover = max(cover_categories.items(), key=lambda x: x[1])
                summary['insights'].append(f"封面策略：{top_cover[0]} 最常见（{top_cover[1]}个视频）")

            if title_categories:
                top_title = max(title_categories.items(), key=lambda x: x[1])
                summary['insights'].append(f"标题策略：{top_title[0]} 最有效（{top_title[1]}个视频）")

            if content_types:
                top_content = max(content_types.items(), key=lambda x: x[1])
                summary['insights'].append(f"内容类型：{top_content[0]} 占主流（{top_content[1]}个视频）")

            if intro_ways:
                top_intro = max(intro_ways.items(), key=lambda x: x[1])
                summary['insights'].append(f"产品引出：{top_intro[0]} 最自然（{top_intro[1]}个视频）")

            if embed_ways:
                top_embed = max(embed_ways.items(), key=lambda x: x[1])
                summary['insights'].append(f"产品植入：{top_embed[0]} 最有效（{top_embed[1]}个视频）")

            logger.success(f"✅ 生成综合洞察: {len(summary['insights'])} 条核心发现")
            return summary

        except Exception as e:
            logger.error(f"生成综合洞察失败: {e}")
            return {
                'error': str(e),
                'insights': ['无法生成综合洞察']
            }

    def _build_video_analysis_prompt(self) -> str:
        """构建视频分析提示词"""
        return """
请深度分析这个小红书视频笔记，重点关注：

1. **开场策略**：前3秒如何吸引注意力？使用了什么hook（钩子）？
2. **内容节奏**：镜头切换频率、信息密度、叙事节奏如何？
3. **视觉设计**：色彩、构图、字幕样式、特效使用有什么特点？
4. **价值呈现**：如何展示产品/内容价值？是否有对比、前后效果展示？
5. **情感共鸣点**：触发了哪些情绪？如何引发共鸣？
6. **行动引导**：如何引导用户点赞、收藏、关注？

请用简洁的语言总结关键成功要素（3-5条），每条控制在20字以内。
"""

    def _summarize_video_ai_insights(self, insights: List[Dict]) -> str:
        """
        汇总视频AI分析洞察

        Args:
            insights: 单个视频的分析结果列表

        Returns:
            综合洞察文本
        """
        if not self.api_key:
            return "未配置AI API，无法生成综合洞察"

        try:
            # 提取所有分析结果
            all_analyses = "\n\n---\n\n".join([
                f"视频{i+1}《{item['note_title']}》(互动:{item['interaction_score']})分析：\n{item['ai_analysis']}"
                for i, item in enumerate(insights)
            ])

            # 调用AI总结
            from openai import OpenAI
            client = OpenAI(api_key=self.api_key, base_url=self.api_base)

            response = client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {
                        "role": "system",
                        "content": "你是小红书视频内容策略专家，擅长总结爆款视频的共性规律。"
                    },
                    {
                        "role": "user",
                        "content": f"""
以下是多个爆款视频的AI深度分析结果：

{all_analyses}

请总结这些爆款视频的**共性成功要素**，提炼出3-5条可执行的创作建议。
每条建议需要包含：
1. 具体策略
2. 实施要点
3. 预期效果

请用简洁、有条理的格式输出。
"""
                    }
                ],
                temperature=0.3,
                max_tokens=1000
            )

            return response.choices[0].message.content

        except Exception as e:
            logger.error(f"视频AI洞察汇总失败: {e}")
            return f"洞察汇总失败: {str(e)}"

    # ========== 图文/视频分离特征提取方法 ==========

    def _extract_type_features(
        self,
        notes: List[ViralNote],
        type_name: str
    ) -> Dict[str, Any]:
        """
        提取特定类型笔记的专属特征

        Args:
            notes: 特定类型的笔记列表
            type_name: 类型名称（'图文' 或 '视频'）

        Returns:
            该类型的特征字典
        """
        if not notes:
            return {
                'count': 0,
                'status': 'no_data',
                'message': f'没有{type_name}类型笔记'
            }

        # 基础统计
        count = len(notes)
        interactions = [n.interaction_score for n in notes]
        avg_interaction = sum(interactions) / count if count > 0 else 0
        max_interaction = max(interactions) if interactions else 0
        min_interaction = min(interactions) if interactions else 0

        # 标题特征
        titles = [n.title for n in notes if n.title]
        avg_title_length = sum(len(t) for t in titles) / len(titles) if titles else 0

        # 提取高频词（简单实现）
        all_title_text = ' '.join(titles)
        # 使用jieba分词提取关键词
        try:
            import jieba.analyse
            top_keywords = jieba.analyse.extract_tags(all_title_text, topK=10)
        except Exception:
            top_keywords = []

        # 发布时间分布
        hour_distribution = {}
        for note in notes:
            if note.upload_time:
                try:
                    hour = int(note.upload_time.split(' ')[1].split(':')[0])
                    hour_distribution[hour] = hour_distribution.get(hour, 0) + 1
                except Exception:
                    pass

        # 找出最佳发布时间段
        best_hour = max(hour_distribution, key=hour_distribution.get) if hour_distribution else None
        best_publish_time = f"{best_hour}:00-{best_hour + 2}:00" if best_hour is not None else "未知"

        # TOP笔记（取互动量前5）
        sorted_notes = sorted(notes, key=lambda x: x.interaction_score, reverse=True)[:5]
        top_notes = [
            {
                'note_id': n.note_id,
                'title': n.title,
                'interaction_score': n.interaction_score,
                'liked_count': n.liked_count,
                'collected_count': n.collected_count,
                'comment_count': n.comment_count
            }
            for n in sorted_notes
        ]

        return {
            'count': count,
            'status': 'success',
            'interaction_stats': {
                'avg': round(avg_interaction, 1),
                'max': max_interaction,
                'min': min_interaction,
                'total': sum(interactions)
            },
            'title_features': {
                'avg_length': round(avg_title_length, 1),
                'top_keywords': top_keywords
            },
            'time_features': {
                'hour_distribution': hour_distribution,
                'best_publish_time': best_publish_time
            },
            'top_notes': top_notes
        }

    def _generate_type_summary(
        self,
        image_notes: List[ViralNote],
        video_notes: List[ViralNote]
    ) -> Dict[str, Any]:
        """
        生成图文/视频类型对比统计摘要

        Args:
            image_notes: 图文笔记列表
            video_notes: 视频笔记列表

        Returns:
            类型统计摘要，包含数量、占比、对比结论
        """
        total_count = len(image_notes) + len(video_notes)
        image_count = len(image_notes)
        video_count = len(video_notes)

        # 计算占比
        image_percentage = round(image_count / total_count * 100, 1) if total_count > 0 else 0
        video_percentage = round(video_count / total_count * 100, 1) if total_count > 0 else 0

        # 计算各类型平均互动
        image_interactions = [n.interaction_score for n in image_notes]
        video_interactions = [n.interaction_score for n in video_notes]

        image_avg = sum(image_interactions) / len(image_interactions) if image_interactions else 0
        video_avg = sum(video_interactions) / len(video_interactions) if video_interactions else 0

        image_max = max(image_interactions) if image_interactions else 0
        video_max = max(video_interactions) if video_interactions else 0

        # 对比分析
        if image_avg > 0 and video_avg > 0:
            if video_avg > image_avg:
                higher_interaction = 'video'
                interaction_diff_percent = round((video_avg - image_avg) / image_avg * 100, 1)
            else:
                higher_interaction = 'image'
                interaction_diff_percent = round((image_avg - video_avg) / video_avg * 100, 1)
        else:
            higher_interaction = 'unknown'
            interaction_diff_percent = 0

        # 生成建议
        if total_count == 0:
            recommendation = '暂无数据，无法生成建议'
        elif video_count == 0:
            recommendation = '当前无视频笔记，建议尝试视频形式扩大覆盖'
        elif image_count == 0:
            recommendation = '当前无图文笔记，建议结合图文形式补充内容深度'
        elif higher_interaction == 'video' and interaction_diff_percent > 20:
            recommendation = f'视频互动率高出{interaction_diff_percent}%，建议以视频为主、图文为辅'
        elif higher_interaction == 'image' and interaction_diff_percent > 20:
            recommendation = f'图文互动率高出{interaction_diff_percent}%，建议以图文为主、视频为辅'
        else:
            recommendation = '图文与视频表现相当，建议均衡发展，根据内容特点选择形式'

        return {
            'total_count': total_count,
            'image_count': image_count,
            'video_count': video_count,
            'image_percentage': image_percentage,
            'video_percentage': video_percentage,
            'image_stats': {
                'avg_interaction': round(image_avg, 1),
                'max_interaction': image_max
            },
            'video_stats': {
                'avg_interaction': round(video_avg, 1),
                'max_interaction': video_max
            },
            'comparison': {
                'higher_interaction': higher_interaction,
                'interaction_diff_percent': interaction_diff_percent,
                'recommendation': recommendation
            }
        }