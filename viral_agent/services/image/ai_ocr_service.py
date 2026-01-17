"""
AI 视觉模型 OCR 服务
使用多模态大模型（如 qwen-vl、glm-4v）进行封面文字识别

优势：
- 无需 PyTorch、OpenCV 等重量级本地依赖
- 识别准确度高，能处理艺术字、手写体
- 复用现有多模态 API 配置，无需新增 API Key
- 成本可控：约 0.01 元/张图
"""
import os
import base64
import asyncio
from io import BytesIO
from typing import List, Optional, Dict, TYPE_CHECKING
from loguru import logger

if TYPE_CHECKING:
    from PIL import Image


class AIOCRService:
    """基于 AI 视觉模型的 OCR 服务"""

    # OCR 提示词
    OCR_PROMPT = (
        "请提取这张图片中的所有可见文字。"
        "要求：\n"
        "1. 只返回文字内容本身，每行一个文字块\n"
        "2. 不要添加任何解释、编号或格式\n"
        "3. 如果图片中没有文字，只返回一个字：无\n"
        "4. 保持文字的原始顺序（从上到下、从左到右）"
    )

    def __init__(self):
        """初始化 AI OCR 服务"""
        # 优先使用多模态配置，否则回退到主 API 配置
        self.api_base = os.getenv('MULTIMODAL_API_BASE') or os.getenv('OPENAI_API_BASE', '')
        self.api_key = os.getenv('MULTIMODAL_API_KEY') or os.getenv('OPENAI_API_KEY', '')
        # 优先使用多模态模型，否则尝试视频模型
        self.model = os.getenv('MULTIMODAL_MODEL_NAME') or os.getenv('VIDEO_MODEL_NAME', '')

        # 检查配置完整性
        self.enabled = bool(self.api_base and self.api_key and self.model)

        if not self.enabled:
            logger.warning("AI OCR 服务未配置（缺少多模态 API 配置）")
            logger.info("配置建议：在 .env 中设置 MULTIMODAL_API_BASE、MULTIMODAL_API_KEY、MULTIMODAL_MODEL_NAME")
        else:
            logger.info(f"AI OCR 服务已启用，使用模型: {self.model}")

        self._client = None

    def _get_client(self):
        """懒加载 OpenAI 客户端"""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(
                    api_key=self.api_key,
                    base_url=self.api_base
                )
            except ImportError:
                logger.error("未安装 openai 库，AI OCR 功能不可用")
                self.enabled = False
        return self._client

    def _parse_ocr_result(self, result: str) -> List[str]:
        """解析 OCR 结果，提取文字列表"""
        if not result:
            return []

        result = result.strip()

        # 处理空结果
        if result in ['无', '空', 'null', 'none', '没有文字', '图片中没有文字']:
            return []

        # 按行分割，过滤空行和无效内容
        texts = []
        for line in result.split('\n'):
            line = line.strip()
            if not line or line in ['无', '空', 'null', 'none', '没有文字', '图片中没有文字']:
                continue
            # 移除可能的序号前缀（如 "1. "、"- " 等）
            if len(line) > 2 and line[0].isdigit() and line[1] in '.、':
                line = line[2:].strip()
            elif len(line) > 1 and line[0] in '-•':
                line = line[1:].strip()
            if line:
                texts.append(line)

        return texts

    # base64 大小限制（5MB，留余量给请求体其他部分）
    MAX_BASE64_SIZE = 5 * 1024 * 1024
    # 最大图片边长（超过则缩小）
    MAX_IMAGE_DIMENSION = 2048

    def _prepare_image_for_ocr(self, image: "Image.Image") -> str:
        """
        准备图片用于 OCR（降采样 + 压缩，确保 base64 不超限）

        Args:
            image: PIL Image 对象

        Returns:
            data:image/jpeg;base64,xxx 格式的 URL
        """
        # 确保是 RGB 模式
        if image.mode != 'RGB':
            image = image.convert('RGB')

        # 1. 如果图片尺寸过大，先缩小
        width, height = image.size
        if max(width, height) > self.MAX_IMAGE_DIMENSION:
            ratio = self.MAX_IMAGE_DIMENSION / max(width, height)
            new_size = (int(width * ratio), int(height * ratio))
            image = image.resize(new_size, Image.Resampling.LANCZOS)
            logger.debug(f"图片缩小: {width}x{height} -> {new_size[0]}x{new_size[1]}")

        # 2. 逐步降低质量直到 base64 大小在限制内
        for quality in [85, 70, 50, 30]:
            buffer = BytesIO()
            image.save(buffer, format='JPEG', quality=quality)
            img_bytes = buffer.getvalue()

            # base64 会膨胀约 1.33 倍
            if len(img_bytes) * 1.4 <= self.MAX_BASE64_SIZE:
                img_base64 = base64.b64encode(img_bytes).decode('utf-8')
                if quality < 85:
                    logger.debug(f"图片压缩: quality={quality}, size={len(img_bytes)/1024:.1f}KB")
                return f"data:image/jpeg;base64,{img_base64}"

        # 3. 如果还是太大，进一步缩小图片
        width, height = image.size
        image = image.resize((width // 2, height // 2), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        image.save(buffer, format='JPEG', quality=50)
        img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
        logger.warning(f"图片大幅压缩: {width}x{height} -> {width//2}x{height//2}")
        return f"data:image/jpeg;base64,{img_base64}"

    async def extract_text_from_image(self, image: "Image.Image") -> List[str]:
        """
        从 PIL Image 对象提取文字（推荐方式，避免防盗链问题）

        Args:
            image: PIL Image 对象

        Returns:
            识别出的文字列表
        """
        if not self.enabled:
            return []

        client = self._get_client()
        if not client:
            return []

        try:
            # 准备图片（降采样 + 压缩，防止超过 API 大小限制）
            data_url = self._prepare_image_for_ocr(image)

            response = await client.chat.completions.create(
                model=self.model,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url}
                        },
                        {
                            "type": "text",
                            "text": self.OCR_PROMPT
                        }
                    ]
                }],
                max_tokens=500,
                temperature=0.1
            )

            result = response.choices[0].message.content
            return self._parse_ocr_result(result)

        except Exception as e:
            logger.error(f"AI OCR 识别失败: {e}")
            return []

    async def extract_text(self, image_url: str) -> List[str]:
        """
        从图片 URL 提取文字（备用方式，可能受防盗链影响）

        Args:
            image_url: 图片 URL

        Returns:
            识别出的文字列表
        """
        if not self.enabled:
            return []

        client = self._get_client()
        if not client:
            return []

        try:
            response = await client.chat.completions.create(
                model=self.model,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url}
                        },
                        {
                            "type": "text",
                            "text": self.OCR_PROMPT
                        }
                    ]
                }],
                max_tokens=500,
                temperature=0.1
            )

            result = response.choices[0].message.content
            return self._parse_ocr_result(result)

        except Exception as e:
            logger.error(f"AI OCR 识别失败 (URL模式): {e}")
            return []

    async def extract_text_batch(
        self,
        images: List["Image.Image"],
        max_concurrent: int = 5
    ) -> List[List[str]]:
        """
        批量提取多张图片的文字

        Args:
            images: PIL Image 列表
            max_concurrent: 最大并发数

        Returns:
            文字列表的列表，与输入顺序对应
        """
        if not self.enabled or not images:
            return [[] for _ in images]

        semaphore = asyncio.Semaphore(max_concurrent)

        async def process_one(img: "Image.Image") -> List[str]:
            async with semaphore:
                return await self.extract_text_from_image(img)

        tasks = [process_one(img) for img in images]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理异常，返回空列表
        output = []
        for result in results:
            if isinstance(result, Exception):
                logger.debug(f"批量 OCR 单项失败: {result}")
                output.append([])
            else:
                output.append(result)

        return output

    def is_available(self) -> bool:
        """检查 AI OCR 服务是否可用"""
        return self.enabled


# 单例实例，方便复用
_ai_ocr_instance: Optional[AIOCRService] = None


def get_ai_ocr_service() -> AIOCRService:
    """获取 AI OCR 服务单例"""
    global _ai_ocr_instance
    if _ai_ocr_instance is None:
        _ai_ocr_instance = AIOCRService()
    return _ai_ocr_instance
