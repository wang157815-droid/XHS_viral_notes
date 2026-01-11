"""
视频AI分析器
封装AI调用逻辑，支持多种AI服务分析视频内容
"""
import os
import json
import asyncio
import sys
import base64
import tempfile
from typing import Dict, List, Any, Optional, Tuple
from loguru import logger
import aiohttp
from dotenv import load_dotenv

# 添加项目根目录到路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# 加载环境变量
load_dotenv()

# 导入URL验证工具
try:
    from xhs_utils.url_validator import validate_video_url, get_best_video_url
except ImportError:
    logger.warning("无法导入URL验证工具，将使用降级方案")
    validate_video_url = lambda url, timeout=3: True  # 降级为始终返回True
    get_best_video_url = lambda urls, max_attempts=3: urls[0] if urls else None

# 导入模块化提示词
from viral_agent.prompts import build_video_metadata_prompt

class VideoAIAnalyzer:
    """视频AI分析器"""

    def __init__(
        self,
        video_source_mode: Optional[str] = None,
        download_manager: Optional['VideoDownloadManager'] = None
    ):
        """
        初始化AI分析器

        Args:
            video_source_mode: 视频源模式 ("url"=URL直传, "proxy"=本地下载)
                              None表示使用环境变量 VIDEO_SOURCE_MODE 配置
            download_manager: 视频下载管理器（可选，用于共享下载避免重复）
        """
        self.api_base = os.getenv('OPENAI_API_BASE', 'https://api.openai.com/v1')
        self.api_key = os.getenv('OPENAI_API_KEY')
        self.model_name = os.getenv('MODEL_NAME', 'gpt-4-vision-preview')

        # 多模态配置
        self.multimodal_api_base = os.getenv('MULTIMODAL_API_BASE', self.api_base)
        self.multimodal_api_key = os.getenv('MULTIMODAL_API_KEY', self.api_key)
        self.multimodal_model = os.getenv('MULTIMODAL_MODEL_NAME', 'glm-4v')

        # 视频分析配置
        self.video_model = os.getenv('VIDEO_MODEL_NAME', self.multimodal_model)
        self.video_analysis_mode = os.getenv('VIDEO_ANALYSIS_MODE', 'metadata')

        # 视频源处理配置（P0-1）
        # 优先使用运行时传入的参数，否则使用环境变量配置
        if video_source_mode is not None:
            self.video_source_mode = video_source_mode
        else:
            self.video_source_mode = os.getenv('VIDEO_SOURCE_MODE', 'url')  # url | proxy
        self.video_download_timeout = int(os.getenv('VIDEO_DOWNLOAD_TIMEOUT', '60'))
        # P2-fix-4: 考虑 base64 膨胀（约1.37倍），实际限制为配置值的 75%
        configured_max_mb = int(os.getenv('VIDEO_MAX_SIZE_MB', '50'))
        self.video_max_size_mb = int(configured_max_mb * 0.75)  # 50MB 配置 → 37MB 实际限制

        # 视频下载管理器（共享下载，避免重复）
        self.download_manager = download_manager

        # 全局并发限制（P0-3）
        max_concurrent = int(os.getenv('VIDEO_MAX_CONCURRENT', '2'))
        self.video_semaphore = asyncio.Semaphore(max_concurrent)

        # 检测配置的AI服务类型
        self.service_type = self._detect_service_type()
        self.vision_model = self._get_vision_model()

        # 缓存
        self.cache = {}

        # 知识检索器（RAG + JSON配置）
        self.knowledge_retriever = None
        self._init_knowledge_retriever()

        if not self.api_key and not self.multimodal_api_key:
            logger.warning("AI API密钥未配置")

        # 输出配置信息
        mode_source = "运行时参数" if video_source_mode is not None else "环境变量"
        logger.info(f"视频分析模式: {self.video_analysis_mode}")
        logger.info(f"视频模型: {self.video_model}")
        logger.info(f"视频源处理: {self.video_source_mode} ({mode_source}), 并发限制: {max_concurrent}")

    def _init_knowledge_retriever(self):
        """初始化知识检索器"""
        try:
            from viral_agent.services.knowledge.knowledge_retriever import UnifiedKnowledgeRetriever
            self.knowledge_retriever = UnifiedKnowledgeRetriever(enable_rag=True)
            logger.info("✅ 视频分析知识检索器初始化成功")
        except Exception as e:
            logger.warning(f"知识检索器初始化失败，将使用纯提示词模式: {e}")
            self.knowledge_retriever = None

    def retrieve_video_knowledge(
        self,
        title: str = "",
        description: str = "",
        query: str = ""
    ) -> Dict[str, Any]:
        """
        检索视频分析相关知识（RAG + JSON配置）

        Args:
            title: 视频标题
            description: 视频描述
            query: 自定义检索查询（可选）

        Returns:
            知识检索结果
        """
        if not self.knowledge_retriever:
            return {
                'structured_knowledge': '',
                'document_knowledge': [],
                'has_json': False,
                'has_rag': False
            }

        try:
            # 构建检索查询
            search_query = query or f"{title} 视频创作技巧 产品植入方式"

            knowledge = self.knowledge_retriever.retrieve_knowledge(
                title=title,
                description=description,
                query=search_query
            )

            logger.info(f"视频知识检索完成: JSON={knowledge.get('has_json', False)}, RAG={knowledge.get('has_rag', False)}")
            return knowledge

        except Exception as e:
            logger.error(f"视频知识检索失败: {e}")
            return {
                'structured_knowledge': '',
                'document_knowledge': [],
                'has_json': False,
                'has_rag': False
            }

    def enhance_prompt_with_knowledge(
        self,
        base_prompt: str,
        title: str = "",
        description: str = "",
        query: str = ""
    ) -> str:
        """
        使用知识库增强提示词

        Args:
            base_prompt: 基础提示词
            title: 视频标题
            description: 视频描述
            query: 自定义检索查询

        Returns:
            增强后的提示词
        """
        knowledge = self.retrieve_video_knowledge(title, description, query)

        # 如果没有检索到知识，返回原始提示词
        if not knowledge.get('has_json') and not knowledge.get('has_rag'):
            return base_prompt

        # 构建知识上下文
        knowledge_context = []

        # 添加结构化知识（JSON配置）
        if knowledge.get('structured_knowledge'):
            knowledge_context.append("【领域专业知识】")
            knowledge_context.append(knowledge['structured_knowledge'])

        # 添加文档知识（RAG检索）
        if knowledge.get('document_knowledge'):
            knowledge_context.append("\n【相关参考文档】")
            for i, doc in enumerate(knowledge['document_knowledge'][:3], 1):
                if isinstance(doc, dict):
                    content = doc.get('content', str(doc))[:500]
                else:
                    content = str(doc)[:500]
                knowledge_context.append(f"{i}. {content}")

        if knowledge_context:
            enhanced_prompt = base_prompt + "\n\n" + "\n".join(knowledge_context)
            logger.debug(f"提示词已增强，添加了 {len(knowledge_context)} 段知识")
            return enhanced_prompt

        return base_prompt

    def _detect_service_type(self) -> str:
        """
        检测配置的AI服务类型

        Returns:
            服务类型
        """
        # 优先检测多模态API
        api_to_check = self.multimodal_api_base if self.multimodal_api_base else self.api_base

        if 'deepseek' in api_to_check:
            return 'deepseek'
        elif 'bigmodel' in api_to_check:
            return 'glm'
        elif 'dashscope' in api_to_check:
            return 'qwen'
        elif 'openai' in api_to_check:
            return 'openai'
        else:
            return 'unknown'

    def _get_vision_model(self) -> str:
        """
        获取对应的视觉模型名称

        Returns:
            视觉模型名称
        """
        # 优先使用配置的多模态模型
        if self.multimodal_model:
            return self.multimodal_model

        # 否则根据服务类型选择默认模型
        vision_models = {
            'deepseek': 'deepseek-vision',
            'glm': 'glm-4v-plus',  # 默认使用plus版本
            'qwen': 'qwen3-vl-plus',  # 默认使用Qwen3-VL（视觉理解更强）
            'openai': 'gpt-4-vision-preview'
        }
        return vision_models.get(self.service_type, self.model_name)

    async def analyze_image(
        self,
        image_url: str,
        prompt: str,
        title: str = None,
        max_tokens: int = 500
    ) -> str:
        """
        分析图片内容（封面图片）

        Args:
            image_url: 图片URL
            prompt: 分析提示词
            title: 相关标题（可选）
            max_tokens: 最大输出token数

        Returns:
            AI分析结果
        """
        if not self.api_key:
            logger.error("API密钥未配置")
            return "API密钥未配置"

        try:
            # 检查缓存
            cache_key = f"image_{image_url}_{hash(prompt)}"
            if cache_key in self.cache:
                return self.cache[cache_key]

            # 构建请求
            messages = self._build_image_messages(image_url, prompt, title)

            # 调用AI API
            result = await self._call_ai_api(
                messages=messages,
                model=self.vision_model,
                max_tokens=max_tokens
            )

            # 缓存结果
            self.cache[cache_key] = result

            return result

        except Exception as e:
            logger.error(f"图片分析失败: {e}")
            return f"分析失败: {str(e)}"

    async def analyze_text(
        self,
        text: str,
        prompt: str,
        max_tokens: int = 300,
        model: Optional[str] = None
    ) -> str:
        """
        分析文本内容（标题、描述等）

        Args:
            text: 待分析文本
            prompt: 分析提示词
            max_tokens: 最大输出token数
            model: 指定模型（可选，默认使用self.model_name）

        Returns:
            AI分析结果
        """
        if not self.api_key:
            logger.error("API密钥未配置")
            return "API密钥未配置"

        # 使用指定模型或默认模型
        use_model = model or self.model_name

        try:
            # 检查缓存
            cache_key = f"text_{hash(text)}_{hash(prompt)}_{use_model}"
            if cache_key in self.cache:
                return self.cache[cache_key]

            # 构建消息
            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": text}
            ]

            # 调用AI API
            result = await self._call_ai_api(
                messages=messages,
                model=use_model,
                max_tokens=max_tokens
            )

            # 缓存结果
            self.cache[cache_key] = result

            return result

        except Exception as e:
            logger.error(f"文本分析失败: {e}")
            return f"分析失败: {str(e)}"

    async def analyze_video(
        self,
        video_url: str,
        prompt: str,
        title: str = None,
        description: str = None,
        max_tokens: int = 1000,
        video_urls: List[Dict[str, Any]] = None,
        note_id: str = None
    ) -> str:
        """
        分析视频内容（通过视频URL，支持多源重试）

        Args:
            video_url: 主视频URL
            prompt: 分析提示词
            title: 视频标题（可选）
            description: 视频描述（可选）
            max_tokens: 最大输出token数
            video_urls: 备选视频URL列表（可选）
            note_id: 笔记ID（用于下载共享和生成稳定缓存键）

        Returns:
            AI分析结果
        """
        if not self.api_key and not self.multimodal_api_key:
            logger.error("API密钥未配置")
            return "API密钥未配置"

        # URL验证
        if not video_url or not video_url.startswith('http'):
            logger.warning(f"无效的视频URL: {video_url}")
            return f"视频URL无效，基于元数据分析：{title or '无标题'}"

        try:
            # 检查缓存
            cache_key = f"video_{video_url}_{hash(prompt)}"
            if cache_key in self.cache:
                return self.cache[cache_key]

            # 根据配置的分析模式选择分析方式
            if self.video_analysis_mode == 'full' and self._supports_video():
                # 完整视频分析（需要支持视频的模型）
                logger.info(f"使用完整视频分析模式（{self.video_model}）")

                # **新增：多源URL重试逻辑 + 下载共享支持**
                result = await self._try_analyze_video_with_retry(
                    video_url, prompt, title, description, max_tokens,
                    video_urls, note_id
                )

            else:
                # 元数据分析模式（基于标题和描述）
                logger.info("使用元数据分析模式（基于标题和描述）")
                if self.video_analysis_mode == 'full':
                    logger.warning(f"当前模型 {self.video_model} 不支持完整视频分析，回退到元数据模式")
                result = await self._analyze_video_metadata(
                    video_url, prompt, title, description, max_tokens
                )

            # 缓存结果
            self.cache[cache_key] = result

            return result

        except Exception as e:
            logger.error(f"视频分析失败: {e}")
            # 最后的兜底：返回基于元数据的简要说明
            return f"分析失败，基于标题推断：{title or '无标题'}。{description[:50] if description else ''}"

    async def _try_analyze_video_with_retry(
        self,
        video_url: str,
        prompt: str,
        title: str,
        description: str,
        max_tokens: int,
        video_urls: List[Dict[str, Any]] = None,
        note_id: str = None
    ) -> str:
        """
        尝试分析视频，支持多层URL验证和智能回退

        采用4层验证策略：
        1. HTTP预验证层 - 使用url_validator快速过滤不可访问的URL（0.3秒/URL，免费）
        2. URL准备层 - 根据预验证结果优化URL优先级
        3. AI兼容性验证层 - 逐个调用AI，检测1210等格式错误（5秒/URL，消耗tokens）
        4. 回退机制层 - 所有URL失败后使用元数据分析兜底

        Args:
            video_url: 主视频URL
            prompt: 分析提示词
            title: 视频标题
            description: 视频描述
            max_tokens: 最大token数
            video_urls: 备选视频URL列表（可选），多个URL时会触发预验证
            note_id: 笔记ID（用于生成稳定缓存键）

        Returns:
            分析结果（AI分析或元数据分析的结果）
        """
        # 🔍 第1层：HTTP预验证（快速过滤不可访问的URL）
        if video_urls and len(video_urls) > 1:
            logger.info(f"🔍 预验证 {len(video_urls)} 个视频URL的可访问性...")

            # 使用url_validator预先筛选出可访问的URL
            best_url = get_best_video_url(video_urls, max_attempts=min(3, len(video_urls)))

            if best_url:
                logger.success(f"✅ HTTP预验证通过，优先使用: {best_url[:80]}...")
                video_url = best_url  # 使用预验证通过的URL

                # 重新排序：把验证通过的URL放最前面
                video_urls_reordered = [{'url': best_url}]
                for url_info in video_urls:
                    url = url_info.get('url') if isinstance(url_info, dict) else url_info
                    if url and url != best_url:
                        video_urls_reordered.append({'url': url} if isinstance(url, str) else url_info)
                video_urls = video_urls_reordered
            else:
                logger.warning("⚠️ 所有URL的HTTP预验证都未通过，仍尝试AI调用（可能失败）")

        # 🎯 第2层：构建URL尝试列表（用于AI调用）
        urls_to_try = []

        # 1. 主URL（优先级最高，可能已被预验证优化）
        urls_to_try.append(video_url)

        # 2. 添加备选URL（如果有）
        if video_urls:
            for url_info in video_urls[1:]:  # 跳过第一个（已经是主URL）
                url = url_info.get('url') if isinstance(url_info, dict) else url_info
                if url and url != video_url:  # 避免重复
                    urls_to_try.append(url)

        logger.info(f"🚀 准备尝试 {len(urls_to_try)} 个视频URL（AI调用层）")

        # 🤖 第3层：AI兼容性验证（逐个尝试，检测1210等AI特定错误）
        for i, url in enumerate(urls_to_try[:3]):  # 最多尝试3个URL
            try:
                logger.debug(f"🤖 AI分析尝试 {i+1}/{len(urls_to_try)}: {url[:80]}...")

                # 尝试分析（传入 note_id 和备选URL用于下载共享）
                result = await self._analyze_video_directly(
                    url, prompt, title, description, max_tokens,
                    note_id=note_id, backup_urls=video_urls
                )

                # 检查是否成功（没有AI特定错误、API异常、或AI明确表示无法读取）
                error_keywords = [
                    "1210", "视频输入格式", "解析错误",  # AI格式错误
                    "API调用异常", "API调用失败",  # API调用错误
                    "无法读取视频", "无法观看视频", "无法访问视频",  # AI明确表示无法读取
                    "视频无法加载", "视频加载失败", "无法获取视频"  # 其他无法读取的表述
                ]
                if not any(kw in result for kw in error_keywords):
                    if i > 0:
                        logger.success(f"✅ 备选URL {i+1} AI分析成功！")
                    else:
                        logger.success(f"✅ 主URL AI分析成功！")
                    return result
                else:
                    # 区分错误类型以便调试
                    if any(kw in result for kw in ["无法读取", "无法观看", "无法访问", "无法加载", "无法获取"]):
                        logger.warning(f"❌ URL {i+1} AI无法读取视频内容，尝试下一个URL...")
                    else:
                        logger.warning(f"❌ URL {i+1} AI返回格式错误: {result[:100]}")
                    continue  # 尝试下一个URL

            except Exception as e:
                logger.warning(f"❌ URL {i+1} AI分析异常: {e}")
                continue  # 尝试下一个URL

        # 🔄 第4层：回退机制（所有URL都失败，使用元数据分析兜底）
        logger.warning(f"⚠️ 所有 {len(urls_to_try)} 个视频URL的AI分析都失败，回退到元数据分析")
        return await self._analyze_video_metadata(
            video_url, prompt, title, description, max_tokens
        )

    def _supports_video(self) -> bool:
        """
        检查当前模型是否支持视频分析

        Returns:
            是否支持视频
        """
        # 支持完整视频分析的模型
        video_capable_models = [
            'glm-4v-plus',      # GLM-4V Plus版本支持视频
            'glm-4.5v',         # GLM-4.5V 新版本支持视频
            'qwen-vl-max',      # 通义千问视觉大模型
            'qwen-vl-plus',     # 通义千问Plus版本
            'qwen3-vl-plus',    # 通义千问3代视觉模型
            'qwen3-vl-flash',   # 通义千问3代Flash版（经济实惠）
            'qwen-vl-max-latest',  # 通义千问最新版本
            'gpt-4-vision-preview'  # OpenAI GPT-4V（部分支持）
        ]

        # 检查当前配置的视频模型
        is_capable = self.video_model in video_capable_models

        if not is_capable and self.video_model == 'glm-4v':
            logger.info("GLM-4V基础版视频支持有限，建议升级到GLM-4V-Plus")

        return is_capable

    async def _download_video_with_referer(
        self,
        video_url: str
    ) -> Tuple[Optional[bytes], Optional[str]]:
        """
        使用Referer头下载小红书视频（P0-1 proxy模式核心）

        小红书CDN需要 Referer: https://www.xiaohongshu.com/ 才能访问
        AI模型API无法携带此header，所以需要本地下载后传递

        Args:
            video_url: 视频URL

        Returns:
            (视频二进制数据, 错误信息) - 成功时错误信息为None
        """
        headers = {
            'Referer': 'https://www.xiaohongshu.com/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        try:
            timeout = aiohttp.ClientTimeout(total=self.video_download_timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(video_url, headers=headers) as response:
                    if response.status != 200:
                        return None, f"下载失败: HTTP {response.status}"

                    # 检查文件大小
                    content_length = response.headers.get('Content-Length')
                    if content_length:
                        size_mb = int(content_length) / (1024 * 1024)
                        if size_mb > self.video_max_size_mb:
                            return None, f"视频过大: {size_mb:.1f}MB > {self.video_max_size_mb}MB"

                    # 读取视频数据
                    video_data = await response.read()

                    # 二次检查实际大小
                    actual_size_mb = len(video_data) / (1024 * 1024)
                    if actual_size_mb > self.video_max_size_mb:
                        return None, f"视频过大: {actual_size_mb:.1f}MB > {self.video_max_size_mb}MB"

                    logger.info(f"✅ 视频下载成功: {actual_size_mb:.1f}MB")
                    return video_data, None

        except asyncio.TimeoutError:
            return None, f"下载超时: {self.video_download_timeout}s"
        except aiohttp.ClientError as e:
            return None, f"网络错误: {type(e).__name__}"
        except Exception as e:
            return None, f"下载异常: {type(e).__name__}: {str(e)}"

    def _video_to_base64(self, video_data: bytes) -> str:
        """将视频数据转为base64编码"""
        return base64.b64encode(video_data).decode('utf-8')

    def _build_video_messages_with_base64(
        self,
        video_base64: str,
        prompt: str,
        title: str,
        description: str
    ) -> List[Dict[str, Any]]:
        """
        构建包含base64视频的消息（proxy模式专用）

        Args:
            video_base64: base64编码的视频数据
            prompt: 提示词
            title: 标题
            description: 描述

        Returns:
            消息列表
        """
        context = prompt
        if title:
            context += f"\n\n视频标题：{title}"
        if description:
            context += f"\n视频描述：{description}"

        # P0-fix: 根据模型选择正确的 base64 格式
        # 智谱GLM：直接传纯 base64 字符串（无前缀）
        # 通义千问：官方推荐用帧列表，直接 base64 视频不稳定，此处尝试 data URI
        model_lower = self.video_model.lower()

        if 'glm' in model_lower or 'bigmodel' in self.multimodal_api_base.lower():
            # 智谱GLM 格式：纯 base64 字符串，无 data URI 前缀
            user_content = [
                {"type": "text", "text": context},
                {
                    "type": "video_url",
                    "video_url": {"url": video_base64}  # 直接传 base64，无前缀
                }
            ]
        elif 'qwen' in model_lower:
            # 通义千问：尝试 data URI 格式（可能不稳定，有降级机制）
            user_content = [
                {
                    "type": "video_url",
                    "video_url": {"url": f"data:video/mp4;base64,{video_base64}"}
                },
                {"type": "text", "text": context}
            ]
        else:
            # 其他模型：尝试 data URI 格式
            user_content = [
                {"type": "text", "text": context},
                {
                    "type": "video_url",
                    "video_url": {"url": f"data:video/mp4;base64,{video_base64}"}
                }
            ]

        return [
            {"role": "system", "content": "你是一个专业的视频内容分析师。"},
            {"role": "user", "content": user_content}
        ]

    async def _analyze_video_directly(
        self,
        video_url: str,
        prompt: str,
        title: str,
        description: str,
        max_tokens: int,
        note_id: str = None,
        backup_urls: List[Dict[str, Any]] = None
    ) -> str:
        """
        直接分析视频内容（带并发控制和proxy模式支持）

        P0-1: 支持 url/proxy 两种模式
        P0-3: 使用 video_semaphore 控制并发
        P1-download: 支持共享下载管理器（避免重复下载）

        Args:
            video_url: 视频URL
            prompt: 分析提示词
            title: 视频标题
            description: 视频描述
            max_tokens: 最大输出token数
            note_id: 笔记ID（用于生成稳定缓存键）
            backup_urls: 备选视频URL列表

        Returns:
            分析结果
        """
        # P0-3: 使用 semaphore 限制视频分析并发
        async with self.video_semaphore:
            logger.debug(f"🔒 获取视频分析锁，当前模式: {self.video_source_mode}")

            # P0-1: 根据 VIDEO_SOURCE_MODE 选择处理方式
            if self.video_source_mode == 'proxy':
                # proxy 模式：本地下载（带Referer）→ base64传给模型
                logger.info(f"📥 proxy模式：下载视频并转base64...")

                # P1-download: 优先使用共享下载管理器（支持与AVSync共享下载）
                if self.download_manager:
                    video_data, error = await self._download_via_manager(
                        video_url, backup_urls, note_id
                    )
                else:
                    # 回退到独立下载方法（向后兼容）
                    video_data, error = await self._download_video_with_referer(video_url)

                if error:
                    logger.warning(f"⚠️ proxy下载失败: {error}，回退到URL模式")
                    # 回退到URL模式
                    messages = self._build_video_messages(video_url, prompt, title, description)
                else:
                    # 转base64并构建消息
                    video_base64 = self._video_to_base64(video_data)
                    logger.info(f"✅ base64编码完成，长度: {len(video_base64)//1024}KB")
                    messages = self._build_video_messages_with_base64(
                        video_base64, prompt, title, description
                    )
                    # 立即释放视频数据内存
                    del video_data
                    del video_base64

            else:
                # url 模式：直接使用原始URL（默认）
                messages = self._build_video_messages(video_url, prompt, title, description)

            # 调用AI API
            return await self._call_ai_api(
                messages=messages,
                model=self.video_model,
                max_tokens=max_tokens
            )

    async def _download_via_manager(
        self,
        video_url: str,
        backup_urls: List[Dict[str, Any]] = None,
        note_id: str = None
    ) -> Tuple[Optional[bytes], Optional[str]]:
        """
        通过共享下载管理器获取视频数据（P1-download）

        使用 VideoDownloadManager 实现下载共享，避免同一视频被重复下载。
        当 AVSyncAnalyzer 也在分析同一视频时，两者共用同一份下载文件。

        Args:
            video_url: 视频URL
            backup_urls: 备选URL列表
            note_id: 笔记ID（用于生成稳定缓存键）

        Returns:
            (视频二进制数据, 错误信息) - 成功时错误信息为None
        """
        try:
            # 通过管理器获取视频（require_bytes=True 表示需要 bytes 数据）
            handle = await self.download_manager.acquire(
                url=video_url,
                backup_urls=backup_urls,
                require_file=False,   # AI分析不需要本地文件路径
                require_bytes=True,   # 需要 bytes 用于 base64 编码
                note_id=note_id
            )

            if handle.bytes_data:
                logger.info(f"✅ 通过共享管理器获取视频: {handle.size_bytes/1024/1024:.1f}MB, 缓存={handle.is_cached}")
                # 立即释放引用（bytes 已拷贝到 handle 中）
                self.download_manager.release(video_url, note_id)
                return handle.bytes_data, None
            else:
                self.download_manager.release(video_url, note_id)
                return None, "下载管理器返回空数据"

        except Exception as e:
            error_msg = f"下载管理器异常: {type(e).__name__}: {str(e)}"
            logger.warning(error_msg)
            # 确保释放引用
            try:
                self.download_manager.release(video_url, note_id)
            except Exception:
                pass
            return None, error_msg

    async def _analyze_video_metadata(
        self,
        video_url: str,
        prompt: str,
        title: str,
        description: str,
        max_tokens: int
    ) -> str:
        """
        通过元数据分析视频

        Args:
            video_url: 视频URL
            prompt: 分析提示词
            title: 视频标题
            description: 视频描述
            max_tokens: 最大输出token数

        Returns:
            分析结果
        """
        # 使用模块化提示词构建器
        metadata_prompt = build_video_metadata_prompt(
            base_prompt=prompt,
            video_url=video_url,
            title=title,
            description=description
        )

        messages = [
            {"role": "system", "content": "你是一个视频内容分析专家。"},
            {"role": "user", "content": metadata_prompt}
        ]

        return await self._call_ai_api(
            messages=messages,
            model=self.model_name,
            max_tokens=max_tokens
        )

    def _build_image_messages(
        self,
        image_url: str,
        prompt: str,
        title: str
    ) -> List[Dict[str, Any]]:
        """
        构建图片分析消息（根据不同API提供商使用不同格式）

        Args:
            image_url: 图片URL
            prompt: 提示词
            title: 标题

        Returns:
            消息列表
        """
        # 构建文本提示
        text_content = f"{prompt}\n\n相关标题：{title}" if title else prompt

        # 根据模型选择正确的图片消息格式
        # 通义千问也使用 image_url 格式，但不需要 detail 字段
        if 'qwen' in self.vision_model.lower():
            # 通义千问格式
            user_content = [
                {"type": "image_url", "image_url": {"url": image_url}},
                {"type": "text", "text": text_content}
            ]
        else:
            # 智谱GLM / OpenAI 格式
            user_content = [
                {"type": "text", "text": text_content},
                {"type": "image_url", "image_url": {"url": image_url}}
            ]

        return [
            {"role": "system", "content": "你是一个专业的视觉内容分析师。"},
            {"role": "user", "content": user_content}
        ]

    def _build_video_messages(
        self,
        video_url: str,
        prompt: str,
        title: str,
        description: str
    ) -> List[Dict[str, Any]]:
        """
        构建视频分析消息（根据不同API提供商使用不同格式）

        Args:
            video_url: 视频URL
            prompt: 提示词
            title: 标题
            description: 描述

        Returns:
            消息列表
        """
        # 构建上下文信息
        context = prompt
        if title:
            context += f"\n\n视频标题：{title}"
        if description:
            context += f"\n视频描述：{description}"

        # 根据模型选择正确的视频消息格式
        # 注意：通义千问和智谱/OpenAI 都使用 video_url 格式（阿里云2025年已统一）
        if 'qwen' in self.video_model.lower():
            # 通义千问格式（qwen3-vl / qwen-vl 统一使用 video_url）
            # 参考文档: https://help.aliyun.com/zh/model-studio/qwenvl-video-understanding
            user_content = [
                {"type": "video_url", "video_url": {"url": video_url}},
                {"type": "text", "text": context}
            ]
        else:
            # 智谱GLM / OpenAI 格式
            user_content = [
                {"type": "text", "text": context},
                {"type": "video_url", "video_url": {"url": video_url}}
            ]

        return [
            {"role": "system", "content": "你是一个专业的视频内容分析师。"},
            {"role": "user", "content": user_content}
        ]

    async def _call_ai_api(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        max_tokens: int = 500,
        temperature: float = 0.3,
        max_retries: int = 3
    ) -> str:
        """
        调用AI API（带429重试机制）

        Args:
            messages: 消息列表
            model: 模型名称
            max_tokens: 最大token数
            temperature: 温度参数
            max_retries: 最大重试次数

        Returns:
            AI响应内容
        """
        # 判断使用哪个API配置
        multimodal_models = [
            'glm-4v', 'glm-4v-plus', 'glm-4.5v',
            'qwen-vl-max', 'qwen-vl-plus', 'qwen3-vl-plus', 'qwen3-vl-flash', 'qwen-vl-max-latest'
        ]
        if model in multimodal_models:
            # 使用多模态API配置
            api_key = self.multimodal_api_key
            api_base = self.multimodal_api_base
        else:
            # 使用默认API配置
            api_key = self.api_key
            api_base = self.api_base

        if not api_key:
            return "API密钥未配置"

        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }

        data = {
            'model': model,
            'messages': messages,
            'max_tokens': max_tokens,
            'temperature': temperature
        }

        last_error = None
        for attempt in range(max_retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f'{api_base}/chat/completions',
                        headers=headers,
                        json=data,
                        timeout=aiohttp.ClientTimeout(total=60)
                    ) as response:
                        if response.status == 200:
                            result = await response.json()
                            return result['choices'][0]['message']['content']
                        elif response.status == 429:
                            # 429限流，使用指数退避重试
                            error_text = await response.text()
                            wait_time = (2 ** attempt) * 5  # 5s, 10s, 20s
                            logger.warning(f"API限流(429)，第{attempt + 1}次重试，等待{wait_time}秒...")
                            await asyncio.sleep(wait_time)
                            last_error = f"API调用失败: 429 - {error_text}"
                        else:
                            error_text = await response.text()
                            logger.error(f"API调用失败: {response.status} - {error_text}")
                            return f"API调用失败: {response.status}"
            except asyncio.TimeoutError as e:
                # P0-2: 超时异常单独处理，可重试
                timeout_val = 60  # 与 ClientTimeout(total=60) 一致
                last_error = f"timeout_{timeout_val}s"
                logger.error(f"API调用超时: {timeout_val}s | {type(e).__name__}")
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) * 3
                    logger.warning(f"超时重试，第{attempt + 1}次，等待{wait_time}秒...")
                    await asyncio.sleep(wait_time)
                continue
            except aiohttp.ClientError as e:
                # P0-2: 网络异常单独处理，可重试
                last_error = f"network_{type(e).__name__}"
                logger.error(f"API网络异常: {type(e).__name__}: {repr(e)}")
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) * 3
                    logger.warning(f"网络异常重试，第{attempt + 1}次，等待{wait_time}秒...")
                    await asyncio.sleep(wait_time)
                continue
            except Exception as e:
                # P0-2: 其他异常，记录详细信息
                last_error = f"{type(e).__name__}: {repr(e)}"
                if '429' in str(e) or '1302' in str(e):
                    wait_time = (2 ** attempt) * 5
                    logger.warning(f"API限流，第{attempt + 1}次重试，等待{wait_time}秒...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"API调用异常: {type(e).__name__}: {repr(e)}")
                    return f"API调用异常: {type(e).__name__}"

        # 所有重试都失败
        logger.error(f"API调用失败，已重试{max_retries}次: {last_error}")
        return f"API调用失败: {last_error}"

    async def batch_analyze(
        self,
        items: List[Dict[str, Any]],
        analyze_type: str = 'image',
        batch_size: int = 2  # 每批处理数量，降低并发避免智谱API 429错误
    ) -> List[str]:
        """
        批量分析（分批并发，避免API限流）

        Args:
            items: 待分析项列表
            analyze_type: 分析类型（image/text/video）
            batch_size: 每批并发数量（智谱API限制严格，建议不超过2）

        Returns:
            分析结果列表
        """
        all_results = []

        # 分批处理，避免并发过高导致429错误
        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            tasks = []

            for item in batch:
                if analyze_type == 'image':
                    task = self.analyze_image(
                        item.get('url'),
                        item.get('prompt'),
                        item.get('title')
                    )
                elif analyze_type == 'text':
                    task = self.analyze_text(
                        item.get('text'),
                        item.get('prompt')
                    )
                elif analyze_type == 'video':
                    task = self.analyze_video(
                        item.get('url'),
                        item.get('prompt'),
                        item.get('title'),
                        item.get('description'),
                        video_urls=item.get('video_urls', []),  # 传递备选URL列表
                        note_id=item.get('note_id')  # 传递笔记ID用于下载共享
                    )
                else:
                    continue

                tasks.append(task)

            # 批次并发执行
            logger.info(f"正在处理第 {i//batch_size + 1} 批（{len(tasks)} 个任务）...")
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            # 处理结果（包括异常）
            for result in batch_results:
                if isinstance(result, Exception):
                    logger.error(f"分析失败: {result}")
                    all_results.append(f"分析失败: {str(result)}")
                else:
                    all_results.append(result)

            # 批次间暂停，避免触发限流
            if i + batch_size < len(items):
                await asyncio.sleep(3)  # 批次间暂停3秒，避免智谱API 429错误
                logger.info(f"已完成 {len(all_results)}/{len(items)}，暂停3秒...")

        return all_results