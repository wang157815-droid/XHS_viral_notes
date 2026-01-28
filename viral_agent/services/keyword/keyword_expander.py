"""
AI 关键词扩展服务

当采集样本量不足时，自动生成更宽泛/相关的关键词进行补采
"""
import json
import os
from typing import List, Optional
from datetime import datetime
from loguru import logger
from openai import OpenAI
from dotenv import load_dotenv

from viral_agent.models.keyword_expansion import (
    ExpandedKeyword,
    KeywordExpansionResult
)
from viral_agent.prompts.keyword_expansion_prompts import (
    KEYWORD_EXPANSION_SYSTEM_PROMPT,
    build_keyword_expansion_prompt
)

load_dotenv()


class KeywordExpander:
    """
    AI 关键词扩展器

    使用大模型生成与原始关键词相关的扩展关键词，
    包括泛化（broader）、相关（related）、同义（synonym）三种策略。
    """

    # 扩展策略说明
    EXPANSION_STRATEGIES = {
        "broader": "将关键词泛化为更大的类别",
        "related": "生成语义相关但角度不同的关键词",
        "synonym": "生成同义词或用户习惯的其他表达"
    }

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model_name: Optional[str] = None
    ):
        """
        初始化关键词扩展器（复用现有 AI 配置）

        Args:
            api_key: API 密钥，默认从环境变量读取
            api_base: API 基础 URL，默认从环境变量读取
            model_name: 模型名称，默认从环境变量读取
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.api_base = api_base or os.getenv(
            "OPENAI_API_BASE", "https://api.openai.com/v1"
        )
        self.model_name = model_name or os.getenv("MODEL_NAME", "deepseek-chat")

        if self.api_key:
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.api_base
            )
            logger.info(
                f"关键词扩展器初始化: 模型={self.model_name}, "
                f"API={self.api_base[:30]}..."
            )
        else:
            self.client = None
            logger.warning("关键词扩展器未配置 API Key，扩展功能不可用")

    async def expand_keywords(
        self,
        original_keywords: List[str],
        context: Optional[str] = None,
        max_expansion: int = 5,
        exclude_keywords: Optional[List[str]] = None
    ) -> KeywordExpansionResult:
        """
        扩展关键词

        Args:
            original_keywords: 原始关键词列表
            context: 上下文信息（如已采集笔记的标题摘要）
            max_expansion: 最大扩展数量
            exclude_keywords: 需要排除的关键词（已尝试过的）

        Returns:
            关键词扩展结果
        """
        if not self.client:
            return KeywordExpansionResult(
                original_keywords=original_keywords,
                success=False,
                error_message="AI 服务未配置，请在 .env 中设置 OPENAI_API_KEY"
            )

        if not original_keywords:
            return KeywordExpansionResult(
                original_keywords=[],
                success=False,
                error_message="原始关键词列表为空"
            )

        try:
            # 构建提示词
            user_prompt = build_keyword_expansion_prompt(
                original_keywords=original_keywords,
                context=context or "",
                exclude_keywords=exclude_keywords or [],
                max_expansion=max_expansion
            )

            logger.info(
                f"开始扩展关键词: {original_keywords}, "
                f"排除: {exclude_keywords or []}"
            )

            # 调用 AI
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": KEYWORD_EXPANSION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
                max_tokens=1000
            )

            # 解析响应
            ai_response = response.choices[0].message.content
            expanded_keywords = self._parse_expansion_response(ai_response)

            # 过滤掉已排除的关键词
            if exclude_keywords:
                exclude_set = set(exclude_keywords)
                expanded_keywords = [
                    ek for ek in expanded_keywords
                    if ek.keyword not in exclude_set
                ]

            # 统计 token 使用
            prompt_tokens = response.usage.prompt_tokens if response.usage else 0
            completion_tokens = response.usage.completion_tokens if response.usage else 0

            result = KeywordExpansionResult(
                original_keywords=original_keywords,
                expanded_keywords=expanded_keywords,
                expansion_time=datetime.now().isoformat(),
                ai_model=self.model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                success=True
            )

            logger.success(
                f"关键词扩展成功: {len(expanded_keywords)} 个新关键词, "
                f"tokens={prompt_tokens + completion_tokens}"
            )

            return result

        except Exception as e:
            logger.error(f"关键词扩展失败: {e}")
            return KeywordExpansionResult(
                original_keywords=original_keywords,
                success=False,
                error_message=str(e)
            )

    def _parse_expansion_response(self, response: str) -> List[ExpandedKeyword]:
        """
        解析 AI 响应，提取扩展关键词

        Args:
            response: AI 原始响应文本

        Returns:
            扩展关键词列表
        """
        expanded_keywords = []

        try:
            # 提取 JSON 部分（处理可能的 markdown 代码块）
            json_str = response
            if "```json" in response:
                json_str = response.split("```json")[1].split("```")[0]
            elif "```" in response:
                json_str = response.split("```")[1].split("```")[0]

            # 解析 JSON
            data = json.loads(json_str.strip())

            # 提取关键词列表
            keywords_data = data.get("expanded_keywords", [])

            for item in keywords_data:
                keyword = item.get("keyword", "").strip()
                if not keyword:
                    continue

                expanded_keywords.append(ExpandedKeyword(
                    keyword=keyword,
                    reason=item.get("reason", "AI 扩展"),
                    similarity_score=float(item.get("similarity", 0.5)),
                    expansion_type=item.get("type", "related")
                ))

        except json.JSONDecodeError as e:
            logger.warning(f"JSON 解析失败，尝试正则提取: {e}")
            # 降级方案：使用正则提取关键词
            import re
            keyword_pattern = r'"keyword"\s*:\s*"([^"]+)"'
            matches = re.findall(keyword_pattern, response)
            for keyword in matches:
                expanded_keywords.append(ExpandedKeyword(
                    keyword=keyword,
                    reason="AI 扩展（降级提取）",
                    similarity_score=0.5,
                    expansion_type="related"
                ))

        except Exception as e:
            logger.error(f"解析扩展响应失败: {e}")

        return expanded_keywords

    def expand_keywords_sync(
        self,
        original_keywords: List[str],
        context: Optional[str] = None,
        max_expansion: int = 5,
        exclude_keywords: Optional[List[str]] = None
    ) -> KeywordExpansionResult:
        """
        同步版本的关键词扩展（供非异步环境使用）

        Args:
            original_keywords: 原始关键词列表
            context: 上下文信息
            max_expansion: 最大扩展数量
            exclude_keywords: 需要排除的关键词

        Returns:
            关键词扩展结果
        """
        import asyncio

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(
            self.expand_keywords(
                original_keywords=original_keywords,
                context=context,
                max_expansion=max_expansion,
                exclude_keywords=exclude_keywords
            )
        )
