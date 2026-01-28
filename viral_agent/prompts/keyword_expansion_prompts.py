"""
关键词扩展提示词模板

用于 AI 关键词扩展服务，生成更宽泛或相关的搜索关键词
"""

KEYWORD_EXPANSION_SYSTEM_PROMPT = """你是小红书内容营销专家，擅长理解用户搜索意图并扩展相关关键词。

你的任务是：根据用户提供的原始关键词，生成更宽泛或相关的搜索关键词，以便采集到更多高质量内容。

扩展策略：
1. broader（泛化）：将具体关键词泛化为更大的类别
   例如：「奶茶店推荐」→「茶饮」「饮品店」「网红店铺」
2. related（相关）：生成语义相关但角度不同的关键词
   例如：「奶茶店推荐」→「网红饮品」「下午茶打卡」「约会去处」
3. synonym（同义）：生成同义词或用户习惯的其他表达
   例如：「奶茶店推荐」→「奶茶测评」「奶茶种草」「喝什么奶茶」

注意事项：
- 保持关键词与原意相关，不要偏离太远
- 优先生成小红书用户常用的搜索词
- 避免过于专业或冷门的词汇
- 每个关键词控制在 2-8 个字
- 生成的关键词要有搜索价值，能够找到相关内容
"""

KEYWORD_EXPANSION_USER_TEMPLATE = """## 原始关键词
{original_keywords}

## 采集上下文（可选，帮助理解搜索意图）
{context}

## 需要排除的关键词（已尝试过，请勿重复）
{exclude_keywords}

## 请生成 {max_expansion} 个扩展关键词

要求：
1. 每个关键词都要标注扩展类型（broader/related/synonym）
2. 给出简短的扩展理由
3. 评估与原关键词的语义相似度（0-1，1表示完全相同）

请严格按以下 JSON 格式返回（不要添加其他内容）：
```json
{{
    "expanded_keywords": [
        {{
            "keyword": "扩展后的关键词",
            "reason": "扩展理由（15字以内）",
            "type": "broader",
            "similarity": 0.8
        }}
    ]
}}
```
"""


def build_keyword_expansion_prompt(
    original_keywords: list,
    context: str = "",
    exclude_keywords: list = None,
    max_expansion: int = 5
) -> str:
    """
    构建关键词扩展提示词

    Args:
        original_keywords: 原始关键词列表
        context: 采集上下文（可选）
        exclude_keywords: 需要排除的关键词
        max_expansion: 最大扩展数量

    Returns:
        格式化后的提示词
    """
    return KEYWORD_EXPANSION_USER_TEMPLATE.format(
        original_keywords="、".join(original_keywords),
        context=context if context else "无",
        exclude_keywords="、".join(exclude_keywords) if exclude_keywords else "无",
        max_expansion=max_expansion
    )
