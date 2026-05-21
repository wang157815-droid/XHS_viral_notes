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

            # 向思考框发出本模型开始提示（让步骤行立即展开）
            await self._emit_thinking_chunk(
                task_id,
                f"模型 {mid}（{mname[:24]}）— 生成 playbook 分类叙事…\n",
                is_reasoning=True,
            )

            # ── 最多 2 次：第1次流式（含思考），第2次无思考非流式重试 ──────────────
            expected_rows = sum(
                len(m.get("elements", {}).get(c) or [])
                for c in _ELEMENT_CODES
            )
            n_applied = 0
            _skip_model = False
            for _attempt in range(1, 3):
                if _attempt == 1:
                    # 第一次：流式 + 允许思考模式
                    try:
                        text = await self.chat_stream_and_emit(
                            task_id,
                            messages,
                            overrides={
                                "temperature": 0.35,
                                "max_tokens": 8192,
                                "timeout": _SHEET2_HTTP_TIMEOUT,
                            },
                        )
                    except ModelInvocationError as exc:
                        logger.warning(
                            "[Sheet2NarrativeAgent] task={} model_index={} LLM 调用异常,跳过: {}",
                            task_id, mi, exc,
                        )
                        await self.emit_log(
                            task_id, "warn",
                            f"Sheet2 叙事 model_index={mi} 跳过:{type(exc).__name__}:{exc}",
                        )
                        _skip_model = True
                        break
                else:
                    # 第二次：非流式 + 关闭思考，要求直接输出完整 JSON
                    await self.emit_log(
                        task_id, "warn",
                        f"Sheet2 叙事 {mid}: 第1次仅写入 {n_applied}/{expected_rows} 条，触发无思考重试…",
                    )
                    retry_msgs = list(messages)
                    last = retry_msgs[-1]
                    retry_msgs[-1] = {
                        "role": last["role"],
                        "content": last["content"]
                            + "\n\n【重要】请直接输出覆盖所有条目的完整 JSON 数组，不要输出任何思考过程。",
                    }
                    try:
                        resp = await self._gateway.chat(
                            self.agent_id,
                            retry_msgs,
                            task_id=task_id,
                            overrides={"temperature": 0.2, "max_tokens": 8192, "timeout": 300},
                        )
                        text = (resp.get("content") or "").strip()
                        if "</think>" in text:
                            text = text.split("</think>", 1)[1].strip()
                        elif "<think>" in text:
                            import re as _re_mod
                            text = _re_mod.sub(r"<think>[\s\S]*", "", text, flags=_re_mod.IGNORECASE).strip()
                        logger.debug(
                            "[Sheet2NarrativeAgent] task={} model_index={} 第2次非流式回复 {} 字符",
                            task_id, mi, len(text),
                        )
                    except Exception as exc:
                        logger.warning(
                            "[Sheet2NarrativeAgent] task={} model_index={} 非流式重试失败: {}",
                            task_id, mi, exc,
                        )
                        break

                text = text.strip()
                logger.debug(
                    "[Sheet2NarrativeAgent] task={} model_index={} 第{}次原始响应 {} 字符;"
                    " 首100: {!r}; 尾100: {!r}",
                    task_id, mi, _attempt, len(text),
                    text[:100], text[-100:] if len(text) > 100 else "",
                )
                parsed = _extract_json_array(text)
                if not parsed:
                    logger.warning(
                        "[Sheet2NarrativeAgent] task={} model_index={} 第{}次无法解析 JSON"
                        "(原始回复前400字: {!r})",
                        task_id, mi, _attempt, text[:400],
                    )
                    await self.emit_log(
                        task_id, "warn",
                        f"Sheet2 叙事 model_index={mi} 第{_attempt}次无法解析 JSON，触发重试…",
                    )
                    continue

                n_applied = _apply_narrative_patches(vm, parsed)
                n_applied_total += n_applied

                if n_applied == 0:
                    logger.warning(
                        "[Sheet2NarrativeAgent] model_index={} 第{}次解析到 {} 条 JSON 但未匹配任何分类"
                        "(期望 {} 条,请检查 element/category_index 字段名是否匹配)",
                        mi, _attempt, len(parsed), expected_rows,
                    )
                    await self.emit_log(
                        task_id, "warn",
                        f"Sheet2 叙事 model_index={mi}: 第{_attempt}次解析 {len(parsed)} 条但 0 条命中分类（期望 {expected_rows} 条）",
                    )
                    continue

                if n_applied < expected_rows:
                    logger.warning(
                        "[Sheet2NarrativeAgent] task={} 模型 {} 第{}次写入 {}/{} 条",
                        task_id, mid, _attempt, n_applied, expected_rows,
                    )
                    # 未全部写入且还有重试机会，继续循环
                    if _attempt < 2:
                        continue
                else:
                    logger.info(
                        "[Sheet2NarrativeAgent] task={} 模型 {} 第{}次完成,写入 {}/{} 条分类叙事",
                        task_id, mid, _attempt, n_applied, expected_rows,
                    )
                # 成功或已耗尽重试 → 结束本模型循环
                break

            if _skip_model:
                continue

            if n_applied > 0:
                await self.emit_log(
                    task_id, "info",
                    f"Sheet2 叙事 {mid}: 写入 {n_applied}/{expected_rows} 条",
                )
                # 向思考框追加本模型写入结果
                await self._emit_thinking_chunk(
                    task_id,
                    f"  ✓ {mid}（{mname[:20]}）写入 {n_applied}/{expected_rows} 条分类叙事。\n",
                    is_reasoning=True,
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
        """构建单模型的完整 payload（不分批，包含所有分类条目）。"""
        mid = str(m.get("model_id") or f"M{model_index + 1}")
        name = str(m.get("name") or "")
        header = f"## model_index={model_index} model_id={mid} name={name}"

        all_rows: List[str] = []
        elements = m.get("elements") or {}
        for code in _ELEMENT_CODES:
            cats = elements.get(code) or []
            if not isinstance(cats, list):
                continue
            for ci, cat in enumerate(cats):
                if not isinstance(cat, dict):
                    continue
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
                all_rows.append(
                    f"- element={code} category_index={ci} type={ctype!r} "
                    f"ratio={ratio:.4f} example_titles={ex_titles!r}"
                )

        if not all_rows:
            return ""

        lines = ["请仅为下列条目生成 JSON 数组(规则见 system)。\n", header] + all_rows
        return "\n".join(lines)


def _escape_ctrl_in_strings(text: str) -> str:
    """转义 JSON 字符串值内的真实控制字符。"""
    out: list[str] = []
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and in_string:
            out.append(ch)
            i += 1
            if i < len(text):
                out.append(text[i])
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
        elif in_string and ch == "\n":
            out.append("\\n")
        elif in_string and ch == "\r":
            out.append("\\r")
        elif in_string and ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
        i += 1
    return "".join(out)

# _escape_control_chars_in_strings 别名，兼容旧调用
_escape_control_chars_in_strings = _escape_ctrl_in_strings


def _try_decode_json(text: str, start_char: str = "[") -> Optional[Any]:
    """raw_decode 多位置尝试：对所有 start_char 出现位置逐一尝试，返回第一个成功结果。

    相比只尝试第一个位置，可以正确跳过思考前缀文本中意外出现的 `[` 字符。
    """
    decoder = json.JSONDecoder()
    for candidate in (text, _escape_ctrl_in_strings(text)):
        pos = 0
        while True:
            idx = candidate.find(start_char, pos)
            if idx == -1:
                break
            try:
                data, _ = decoder.raw_decode(candidate, idx)
                return data
            except (json.JSONDecodeError, ValueError):
                pos = idx + 1
    return None


def _try_recover_truncated_array(text: str) -> Optional[List[Any]]:
    """处理被 max_tokens 截断的 JSON 数组。

    当 LLM 输出在数组中途被截断时（如 [..., {last incomplete}），
    尝试通过补齐 `}]` 恢复已完成的部分，返回所有已完整生成的条目。
    """
    s = text.find("[")
    if s == -1:
        return None
    body = text[s:].rstrip()
    if body.endswith("]"):
        return None  # 已完整，由标准层处理

    # 逐渐向前裁剪，尝试找到最长的可合法关闭前缀
    for suffix in ("}", "}]", "]"):
        candidate = body.rstrip(",").rstrip() + suffix
        try:
            data = json.loads(candidate)
            if isinstance(data, list) and data:
                return data
        except (json.JSONDecodeError, ValueError):
            pass

    # 再尝试 escape 后修复
    body_esc = _escape_ctrl_in_strings(body)
    for suffix in ("}", "}]", "]"):
        candidate = body_esc.rstrip(",").rstrip() + suffix
        try:
            data = json.loads(candidate)
            if isinstance(data, list) and data:
                return data
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def _split_flat_json_objects(text: str) -> Optional[List[Any]]:
    """处理所有条目被 LLM 扁平化进单个 { } 的情况。"""
    s = text.find("[")
    e = text.rfind("]")
    if s == -1 or e == -1 or e <= s:
        return None
    inner = text[s + 1 : e].strip()
    if inner.startswith("{"):
        inner = inner[1:]
    if inner.endswith("}"):
        inner = inner[:-1]
    inner = inner.strip()
    positions = [m.start() for m in re.finditer(r'"model_index"\s*:', inner)]
    if len(positions) < 2:
        return None
    results: List[Any] = []
    for idx, pos in enumerate(positions):
        next_pos = positions[idx + 1] if idx + 1 < len(positions) else len(inner)
        chunk = inner[pos:next_pos].strip().rstrip(",").strip()
        obj_text = "{" + chunk + "}"
        for candidate in (obj_text, _escape_ctrl_in_strings(obj_text)):
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict) and "element" in obj:
                    results.append(obj)
                    break
            except (json.JSONDecodeError, ValueError):
                pass
    return results if results else None


def _extract_objects_by_field_scan(text: str) -> Optional[List[Any]]:
    """终极兜底：按已知字段正则扫描，完全绕开 JSON 语法合法性。

    专为 Sheet2 输出设计：模型名字段固定为 model_index / element /
    category_index / summary / explanation / example_text。
    处理代码围栏、真实换行符、混用 \\n 转义等一切 LLM 格式问题。
    """
    # 标准化：把真实换行符替换为 ↵（不影响非字符串区域的 JSON 结构符号）
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    # 保留结构换行（对象间的），只替换最终会出现在字符串值里的
    # 做法：先把所有 \n 变为占位 ↵，然后在解析时还原字符串值里的
    flat = normalized.replace("\n", "↵")

    results: List[Any] = []

    def _get_str_field(field: str, scope: str) -> str:
        """在 scope 内提取 "field": "..." 的值，支持 JSON 转义序列。"""
        m = re.search(
            rf'"{re.escape(field)}"\s*:\s*"((?:[^"\\↵]|\\.|↵)*)"',
            scope,
        )
        if not m:
            return ""
        val = m.group(1)
        # 还原占位换行 → \n 分隔符
        val = val.replace("↵", "\n")
        # 解码 JSON 字符串转义（\\n → \n 等）
        try:
            val = json.loads('"' + val + '"')
        except (json.JSONDecodeError, ValueError):
            val = val.replace("\\n", "\n").replace('\\"', '"')
        return val.strip()

    # 找所有 "model_index": N, "element": "X", "category_index": N 的头部
    header_re = re.compile(
        r'"model_index"\s*:\s*(\d+)[^{}\[\]]*?"element"\s*:\s*"([A-Za-z_]+)"'
        r'[^{}\[\]]*?"category_index"\s*:\s*(\d+)',
        re.DOTALL,
    )
    matches = list(header_re.finditer(flat))
    if not matches:
        return None

    for idx, hm in enumerate(matches):
        mi = int(hm.group(1))
        element = hm.group(2)
        ci = int(hm.group(3))
        # 仅在本对象范围内提取字段（到下一个 "model_index" 前）
        scope_start = hm.end()
        scope_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(flat)
        scope = flat[scope_start:scope_end]

        summary = _get_str_field("summary", scope)
        explanation = _get_str_field("explanation", scope)
        example_text = _get_str_field("example_text", scope)

        if not summary and not example_text:
            continue  # 空条目，跳过

        results.append(
            {
                "model_index": mi,
                "element": element,
                "category_index": ci,
                "summary": summary,
                "explanation": explanation,
                "example_text": example_text,
            }
        )

    return results if results else None


def _extract_json_array(text: str) -> Optional[List[Any]]:
    """从 LLM 输出中提取 Sheet2 数组，五层兜底：

    1. 标准 raw_decode（最快路径，扫描所有 [ 位置）
    2. 转义字符串内控制符后 raw_decode
    2.5. 截断数组修复（max_tokens 截断导致数组不完整时补齐）
    3. 扁平对象拆分（处理所有条目并入单个 {} 的情况）
    4. 字段扫描（终极兜底，完全绕开 JSON 语法）
    """
    # 去除 <think> 推理链
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()
    # 提取代码围栏内容（若有）
    m = re.search(r"```(?:json|JSON)?\s*\n?([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()

    # 层 1 & 2：标准 JSON 解析 + escape 兜底（_try_decode_json 已扫描所有 [ 位置）
    data = _try_decode_json(text, "[")
    if isinstance(data, list) and data:
        if len(data) == 1 and isinstance(data[0], dict):
            count = len(re.findall(r'"model_index"\s*:', text))
            if count > 1:
                split_result = _split_flat_json_objects(text)
                if split_result:
                    return split_result
        return data

    # 检查是否是 {"narratives": [...]} 外包对象，提取内层数组
    outer = _try_decode_json(text, "{")
    if isinstance(outer, dict):
        for v in outer.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v

    # 层 2.5：截断数组修复
    recovered = _try_recover_truncated_array(text)
    if recovered:
        return recovered

    # 层 3：扁平对象拆分
    split_result = _split_flat_json_objects(text)
    if split_result:
        return split_result

    # 层 4：字段扫描终极兜底
    return _extract_objects_by_field_scan(text)


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
