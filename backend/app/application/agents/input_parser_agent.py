"""
InputParserAgent：自然语言输入解析（阶段 4.1 真实化）。

职责：
- 读取 input_spec.raw_input（或 input_spec.keywords 兜底）
- 调用 input_parser.md 提示词 + 模型网关解析意图
- 产出标准化字段写回 input_spec（merge 模式）：
    {
      "parsed": {
        "keywords": [...],
        "dimensions": {"brand": [...], "competitor": [...], "industry": [...]},
        "adjustments": [...],
        "confidence": 0.0-1.0,
        "competitor_source": "user_explicit" | "llm_inferred" | "api"
      }
    }

  竞品词来源优先级：
  1. API 字段 ``input_spec.competitor_keywords`` / ``advanced_config.competitor_keywords``
     → competitor_source="api"，直接使用，不走 LLM 推断
  2. LLM 从 raw_input 中判断用户是否提到竞品
     → competitor_source="user_explicit"（用户提到了）或 "llm_inferred"（用户没提到，LLM 推断）
  当 competitor_source 为 "api" 或 "user_explicit" 时，不追加额外推断词。

阶段 4.1：
- 加 response_format=json_object 约束模型输出
- 解析失败 retry 1 次；最终失败走本地 fallback（保证下游 Crawler 能继续）
- JSON 解析统一走 `_json_parsing.extract_json_object`
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from loguru import logger

from ...domain.task_context import TaskContextWriter
from ...llm.model_gateway import ModelInvocationError
from ._json_parsing import extract_json_object
from .base import AgentContext, AgentResult, BaseAgent
from .prompts import prompt_registry

# API 显式传入的竞品词可略多；由大模型推断（含从自然语言抽取）的竞品词单独收紧
_MAX_COMPETITOR_TERMS_API = 5
_MAX_COMPETITOR_TERMS_LLM = 3


def collect_api_competitor_keywords(input_spec: Dict[str, Any]) -> List[str]:
    """从 API 字段（非自然语言）收集显式竞品词：input_spec.competitor_keywords / advanced_config。"""
    found: List[str] = []
    top = input_spec.get("competitor_keywords")
    if isinstance(top, list):
        found.extend(str(x).strip() for x in top if str(x).strip())
    adv = (input_spec.get("advanced_config") or {}).get("competitor_keywords")
    if isinstance(adv, list):
        found.extend(str(x).strip() for x in adv if str(x).strip())
    return _dedupe_preserve_order(found)[:_MAX_COMPETITOR_TERMS_API]


# 保留旧函数名作为兼容别名，供 CrawlerAgent 等外部调用
collect_explicit_competitor_keywords = collect_api_competitor_keywords


def _dedupe_preserve_order(items: List[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _apply_competitor_policy(
    parsed: Dict[str, Any],
    api_competitors: List[str],
) -> Dict[str, Any]:
    """根据竞品来源决定最终的 competitor 列表。

    优先级:
    1. API 字段竞品词（competitor_keywords / advanced_config）→ 只用 API 词，忽略 LLM
    2. LLM 判断 competitor_source="user_explicit" → 只保留 LLM 提取的用户原文竞品词
    3. LLM 判断 competitor_source="llm_inferred" → 保留 LLM 推断的竞品词
    """
    dims = parsed.get("dimensions") or {}
    brand = [str(x) for x in (dims.get("brand") or [])]
    industry = [str(x) for x in (dims.get("industry") or [])]
    llm_comp = [str(x).strip() for x in (dims.get("competitor") or []) if str(x).strip()]

    if api_competitors:
        # API 字段优先：只用 API 给出的词，完全忽略 LLM 推断
        final_comp = list(api_competitors)[:_MAX_COMPETITOR_TERMS_API]
        parsed["competitor_source"] = "api"
    else:
        # 由 LLM 判断：competitor_source 决定是否为用户显式指定
        source = str(parsed.get("competitor_source") or "llm_inferred").strip()
        if source not in ("user_explicit", "llm_inferred"):
            source = "llm_inferred"
        parsed["competitor_source"] = source
        # 无论 user_explicit 还是 llm_inferred，都直接使用 LLM 输出的 competitor 列表
        # 区别在于：user_explicit 意味着 LLM 只提取了用户原文中的竞品，没有额外推断
        final_comp = llm_comp[:_MAX_COMPETITOR_TERMS_LLM]

    parsed["dimensions"] = {
        "brand": brand,
        "competitor": final_comp,
        "industry": industry,
    }
    return parsed


class InputParserAgent(BaseAgent):
    agent_id = "InputParserAgent"
    provides: List[str] = []  # 不产出独立 module；解析结果供下游复用
    depends_on: List[str] = []
    write_partition = "input_spec"

    async def run(self, context: AgentContext) -> AgentResult:
        task_id = context.task_id
        input_spec = context.task_context.get("input_spec") or {}
        raw_input: str = (input_spec.get("raw_input") or "").strip()
        hint_keywords: List[str] = list(input_spec.get("keywords") or [])

        await self.emit_progress(task_id, "输入解析：识别关键词与调整需求", progress=5)

        api_competitors = collect_api_competitor_keywords(input_spec)

        parsed: Dict[str, Any] = {
            "keywords": hint_keywords or ([raw_input] if raw_input else []),
            "dimensions": {
                "brand": [],
                "competitor": list(api_competitors),
                "industry": [],
            },
            "adjustments": [],
            "confidence": 0.5,
            "competitor_source": "api" if api_competitors else "llm_inferred",
            "serp_expanded_keyword": "",
        }

        if raw_input or hint_keywords:
            system_prompt = prompt_registry.load("input_parser.md")

            # 构建 user content：告知 LLM API 层面是否已有竞品词
            if api_competitors:
                comp_hint = (
                    f"\n【用户已给出的竞品关键词】(来自 API 接口,已确定): {', '.join(api_competitors)}"
                    "\n请将这些词原样放入 dimensions.competitor,不要推断额外竞品词。"
                    "\ncompetitor_source 设为 \"user_explicit\"。"
                )
            else:
                comp_hint = (
                    "\n【用户已给出的竞品关键词】(无)"
                    "\n请根据用户输入判断:若用户在自然语言中提到了竞品/对比品牌,则提取出来并设 competitor_source=\"user_explicit\";"
                    "若用户未提及任何竞品,则由你推断（最多 3 个）并设 competitor_source=\"llm_inferred\"。"
                )

            user_content = (
                f"【用户自然语言】{raw_input or '(空)'}\n"
                f"【用户已给出的主关键词】{', '.join(hint_keywords) if hint_keywords else '(无)'}"
                f"{comp_hint}"
            )

            extracted = await self._call_with_retry(
                task_id,
                system_prompt,
                user_content,
                max_attempts=2,
            )
            if extracted:
                parsed = _merge_parsed(parsed, extracted)
            else:
                await self.emit_log(
                    task_id, "warn", "InputParser 两次尝试均未得到合法 JSON，使用本地兜底"
                )

        parsed = _apply_competitor_policy(parsed, api_competitors)

        # 清理 serp_expanded_keyword：去除空值，限制长度
        serp_kw_raw = str(parsed.get("serp_expanded_keyword") or "").strip()
        serp_kw = serp_kw_raw
        if serp_kw:
            # 避免与原始关键词完全相同（失去扩展意义）
            base_kw_set = {str(k).strip().lower() for k in (parsed.get("keywords") or [])}
            if serp_kw.lower() in base_kw_set:
                logger.warning(f"[SERP] 扩展词 '{serp_kw}' 与主关键词重复,已过滤")
                serp_kw = ""
        if serp_kw:
            logger.info(f"[SERP] 扩展词已确定: '{serp_kw}' (LLM原始: '{serp_kw_raw}')")
        else:
            logger.warning(f"[SERP] 扩展词为空 (LLM原始: '{serp_kw_raw}'), Sheet 6 将使用品类兜底")
        parsed["serp_expanded_keyword"] = serp_kw[:32]

        TaskContextWriter(context.task_context).write(
            "input_spec",
            {"parsed": parsed},
            agent_id=self.agent_id,
            merge=True,
            note="input_parser",
        )
        return AgentResult(ok=True, produced_modules=[], output={"parsed": parsed})

    async def _call_with_retry(
        self,
        task_id: str,
        system_prompt: str,
        user_content: str,
        *,
        max_attempts: int = 2,
    ) -> Dict[str, Any] | None:
        """调用模型并尝试解析 JSON，失败最多再 retry 1 次。"""
        last_text = ""
        for attempt in range(1, max_attempts + 1):
            try:
                response = await self._gateway.chat(
                    agent_id=self.agent_id,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    task_id=task_id,
                    overrides={
                        "temperature": 0.2,
                        "max_tokens": 400,
                        "response_format": {"type": "json_object"},
                    },
                )
                last_text = (response.get("content") or "").strip()
                parsed = extract_json_object(last_text)
                if parsed:
                    return parsed
                await self.emit_log(
                    task_id,
                    "warn",
                    f"InputParser 第 {attempt} 次 JSON 解析失败，尝试重试"
                    if attempt < max_attempts
                    else "InputParser 最终解析失败",
                )
            except ModelInvocationError as exc:
                await self.emit_log(
                    task_id,
                    "warn",
                    f"InputParser 调用第 {attempt} 次降级：{exc.code} - {exc}",
                )
                if attempt >= max_attempts:
                    return None
        return None


def _merge_parsed(base: Dict[str, Any], extracted: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    kw = extracted.get("keywords")
    if isinstance(kw, list) and kw:
        out["keywords"] = [str(x) for x in kw][:5]
    dims = extracted.get("dimensions")
    if isinstance(dims, dict):
        out["dimensions"] = {
            "brand": [str(x) for x in (dims.get("brand") or [])],
            "competitor": [str(x) for x in (dims.get("competitor") or [])],
            "industry": [str(x) for x in (dims.get("industry") or [])],
        }
    adjustments = extracted.get("adjustments")
    if isinstance(adjustments, list):
        out["adjustments"] = [str(x) for x in adjustments][:10]
    conf = extracted.get("confidence")
    if isinstance(conf, (int, float)):
        out["confidence"] = max(0.0, min(1.0, float(conf)))
    # 传递 LLM 判断的 competitor_source
    cs = extracted.get("competitor_source")
    if cs and isinstance(cs, str):
        out["competitor_source"] = cs.strip()
    # 传递 SERP 扩展词
    serp = extracted.get("serp_expanded_keyword")
    if serp is not None:
        out["serp_expanded_keyword"] = str(serp).strip()
    return out
