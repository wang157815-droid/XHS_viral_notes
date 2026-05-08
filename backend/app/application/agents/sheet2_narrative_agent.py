"""
Sheet2NarrativeAgent: 为 viral_model 各要素分类补充 summary / explanation / example_text,
供「爆文总结详情2」playbook 导出使用。不设 asyncio 层超时,由底层 HTTP 读超时控制(默认 86400s,
见 SHEET2_NARRATIVE_HTTP_TIMEOUT)。单模型请求失败时跳过该模型,不阻塞主链路。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from loguru import logger

from ...domain.task_context import TaskContextWriter
from ...llm.model_gateway import ModelInvocationError
from .base import AgentContext, AgentResult, BaseAgent
from .prompts import prompt_registry

# 不设 asyncio.wait_for；仅传入底层 HTTP 读超时(秒)，默认 86400，可用环境变量收紧
_SHEET2_HTTP_TIMEOUT = float(os.getenv("SHEET2_NARRATIVE_HTTP_TIMEOUT", "86400"))

_ELEMENT_CODES = [
    "A_cover",
    "B_cover_text",
    "C_title",
    "D_opening",
    "E_product_intro",
    "F_product_placement",
]
_ELEMENT_CODE_CANON = {c.lower(): c for c in _ELEMENT_CODES}


class Sheet2NarrativeAgent(BaseAgent):
    agent_id = "Sheet2NarrativeAgent"
    write_partition = "viral_model_output"
    provides: List[str] = []
    depends_on: List[str] = []

    async def run(self, context: AgentContext) -> AgentResult:
        task_id = context.task_id
        vm = context.task_context.get("viral_model_output")
        if not isinstance(vm, dict):
            return AgentResult(ok=True, produced_modules=[], output={"skipped": True})

        models = vm.get("models") or []
        if not models:
            return AgentResult(ok=True, produced_modules=[], output={"skipped": True})

        logger.info(
            "[Sheet2NarrativeAgent] task={} 开始: {} 个爆文模型,"
            "将按模型依次请求 LLM(无 asyncio 截断,HTTP 读超时≈{:.0f}s)",
            task_id,
            len(models),
            _SHEET2_HTTP_TIMEOUT,
        )
        await self.emit_progress(
            task_id,
            f"Sheet2 叙事:准备为 {len(models)} 个模型生成 playbook 文案…",
            progress=60,
        )

        crawler = context.task_context.get("crawler_output") or {}
        all_notes = crawler.get("all_notes") or []
        notes_by_id: Dict[str, Dict[str, Any]] = {}
        if isinstance(all_notes, list):
            for n in all_notes:
                if isinstance(n, dict) and n.get("note_id"):
                    notes_by_id[str(n["note_id"])] = n

        system_prompt = prompt_registry.load("sheet2_narrative.md")
        n_applied_total = 0
        n_batches = 0

        for mi, m in enumerate(models):
            if not isinstance(m, dict):
                continue
            user_blob = self._build_user_payload_single_model(mi, m, notes_by_id)
            if not user_blob:
                continue
            n_batches += 1
            mid = str(m.get("model_id") or f"M{mi + 1}")
            mname = str(m.get("name") or "")
            logger.info(
                "[Sheet2NarrativeAgent] task={} 模型 {}/{} {} {} 请求 LLM "
                "(payload≈{} 字符)…",
                task_id,
                mi + 1,
                len(models),
                mid,
                mname[:24],
                len(user_blob),
            )
            await self.emit_progress(
                task_id,
                f"Sheet2 叙事:模型 {mid} LLM 生成中(第 {n_batches} 次调用)…",
                progress=min(65, 61 + n_batches),
            )
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_blob},
            ]

            try:
                response = await self._gateway.chat(
                    agent_id=self.agent_id,
                    messages=messages,
                    modality="text",
                    task_id=task_id,
                    overrides={
                        "temperature": 0.35,
                        "max_tokens": 4096,
                        "timeout": _SHEET2_HTTP_TIMEOUT,
                    },
                )
            except ModelInvocationError as exc:
                await self.emit_log(
                    task_id,
                    "warn",
                    f"Sheet2 叙事 model_index={mi} 跳过:{type(exc).__name__}:{exc}",
                )
                continue

            text = (response.get("content") or "").strip()
            parsed = _extract_json_array(text)
            if not parsed:
                await self.emit_log(
                    task_id,
                    "warn",
                    f"Sheet2 叙事 model_index={mi} 无法解析 JSON,跳过本模型",
                )
                continue

            n_applied = _apply_narrative_patches(vm, parsed)
            n_applied_total += n_applied
            if n_applied == 0:
                logger.warning(
                    "[Sheet2NarrativeAgent] model_index={} 解析到 {} 条 JSON 但未匹配任何分类",
                    mi,
                    len(parsed),
                )
            else:
                logger.info(
                    "[Sheet2NarrativeAgent] task={} 模型 {} LLM 完成,写入 {} 条分类叙事",
                    task_id,
                    mid,
                    n_applied,
                )

        TaskContextWriter(context.task_context).write(
            self.write_partition,
            vm,
            agent_id=self.agent_id,
            note=f"sheet2_narrative_{n_applied_total}",
        )
        await self.emit_progress(
            task_id,
            f"Sheet2 playbook 叙事已写入 {n_applied_total} 条({n_batches} 个模型批次)",
            progress=66,
        )
        return AgentResult(
            ok=True,
            produced_modules=[],
            output={
                "patches_applied": n_applied_total,
                "model_batches": n_batches,
            },
        )

    def _build_user_payload_single_model(
        self,
        model_index: int,
        m: Dict[str, Any],
        notes_by_id: Dict[str, Dict[str, Any]],
    ) -> str:
        mid = str(m.get("model_id") or f"M{model_index + 1}")
        name = str(m.get("name") or "")
        lines: List[str] = [
            "请仅为下列条目生成 JSON 数组(规则见 system)。\n",
            f"## model_index={model_index} model_id={mid} name={name}",
        ]
        any_row = False
        elements = m.get("elements") or {}
        for code in _ELEMENT_CODES:
            cats = elements.get(code) or []
            if not isinstance(cats, list):
                continue
            for ci, cat in enumerate(cats):
                if not isinstance(cat, dict):
                    continue
                any_row = True
                ctype = str(cat.get("type") or "")
                ratio = float(cat.get("ratio") or 0)
                ex_titles: List[str] = []
                for ex in (cat.get("examples") or [])[:3]:
                    if isinstance(ex, dict):
                        nid = str(ex.get("note_id") or "")
                        tit = str(ex.get("title") or "")
                        if not tit and nid:
                            nn = notes_by_id.get(nid) or {}
                            tit = str(nn.get("title") or "")[:80]
                        if tit:
                            ex_titles.append(tit[:80])
                lines.append(
                    f"- element={code} category_index={ci} type={ctype!r} "
                    f"ratio={ratio:.4f} example_titles={ex_titles!r}"
                )
        if not any_row:
            return ""
        return "\n".join(lines)


def _extract_json_array(text: str) -> Optional[List[Any]]:
    text = re.sub(
        r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE
    ).strip()
    m = re.search(r"```(?:json|JSON)?\s*\n?([\s\S]*?)```", text)
    if m:
        text = m.group(1)
    match = re.search(r"\[[\s\S]*\]", text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, list) else None
    except (json.JSONDecodeError, ValueError):
        return None


def _normalize_element_code(raw: str) -> str:
    s = (raw or "").strip()
    if s in _ELEMENT_CODES:
        return s
    return _ELEMENT_CODE_CANON.get(s.lower(), s)


def _apply_narrative_patches(vm: Dict[str, Any], items: List[Any]) -> int:
    models = vm.get("models") or []
    applied = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            mi = int(it.get("model_index", -1))
        except (TypeError, ValueError):
            continue
        code = _normalize_element_code(
            str(it.get("element") or it.get("element_code") or "")
        )
        try:
            ci = int(it.get("category_index", -1))
        except (TypeError, ValueError):
            continue
        if mi < 0 or mi >= len(models) or code not in _ELEMENT_CODES or ci < 0:
            continue
        m = models[mi]
        if not isinstance(m, dict):
            continue
        elements = m.get("elements") or {}
        cats = elements.get(code)
        if not isinstance(cats, list) or ci >= len(cats):
            continue
        cat = cats[ci]
        if not isinstance(cat, dict):
            continue
        patched = False
        for key in ("summary", "explanation", "example_text"):
            val = it.get(key)
            if val is None:
                continue
            text = str(val).strip() if not isinstance(val, str) else val.strip()
            if text:
                cat[key] = text
                patched = True
        if patched:
            applied += 1
    return applied
