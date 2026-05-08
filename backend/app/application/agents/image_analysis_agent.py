"""
ImageAnalysisAgent: 图文笔记 6 要素标注器(阶段 4.3pre.2 重写)。

**变化(相比 4.1 版本)**:
- 输出从 "cover_types/opening_hooks/closing_hooks/ocr_highlights" 等汇总报告
  改为 **每条图文笔记的 8 字段标注**(与 VideoAnalysisAgent 同 schema)
- 写到 `multimodal_output.annotations[note_id]`,和 VideoAgent merge 共享同 dict
- 下游 ViralModelAgent 统一消费一个 annotations dict

职责:
- 从 crawler_output.notes_image 选样:**四源样本表可见图文优先**,再按互动分补足至 floor/hard 上限
- 用 ModelGateway multimodal 调模型给每条图文笔记做"6 要素 + pain + direction"标注
- 产品引出/植入(E/F)广义理解「主推对象」(含实物/服务/观点/测评对象);弱带货时也要从候选里选最接近的一类
- 各要素标签:优先 taxonomy 枚举原文;无匹配时可**自拟**中文短语;不得为 ``_NONE``/空串(校验不通过会丢弃本条)

env:
- IMAGE_ANALYSIS_TOP_N: 全池「至少」分析条数(与四源可见数取 max),默认 6
- IMAGE_ANALYSIS_HARD_MAX: 单任务绝对上限(控成本),默认 80
- IMAGE_ANALYSIS_TIMEOUT: 单条推理超时,默认 60
- IMAGE_ANALYSIS_CONCURRENCY: 并发上限,默认 3
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

from loguru import logger

from ...domain.task_context import TaskContextWriter
from ...domain.viral_model import ElementCode
from ...llm.model_gateway import ModelInvocationError
from ._json_parsing import extract_json_object
from .base import AgentContext, AgentResult, BaseAgent
from .multimodal_note_pick import pick_multimodal_notes
from .prompts import prompt_registry


_TOP_N = int(os.getenv("IMAGE_ANALYSIS_TOP_N", "6"))
_HARD_MAX = int(os.getenv("IMAGE_ANALYSIS_HARD_MAX", "80"))
_PER_NOTE_TIMEOUT = int(os.getenv("IMAGE_ANALYSIS_TIMEOUT", "60"))
_CONCURRENCY = int(os.getenv("IMAGE_ANALYSIS_CONCURRENCY", "3"))

_ANNOTATION_KEYS = [
    "cover_type",
    "cover_text_type",
    "title_type",
    "opening_type",
    "product_intro_type",
    "product_placement_type",
    "pain_keywords",
    "content_direction",
]


class ImageAnalysisAgent(BaseAgent):
    agent_id = "ImageAnalysisAgent"
    # 4.3pre.3: 不再独立展示 `mod-image-analysis` 画布模块,
    # 标注结果融入 `mod-viral-model-matrix` 的 A/B/C/D/E/F 要素
    provides: List[str] = []
    depends_on: List[str] = []
    write_partition = "multimodal_output"

    async def run(self, context: AgentContext) -> AgentResult:
        task_id = context.task_id
        crawler = context.task_context.get("crawler_output") or {}
        notes_image: List[Dict[str, Any]] = list(crawler.get("notes_image") or [])
        with_cover = [
            n
            for n in notes_image
            if (n.get("cover_url") or (n.get("image_urls") or []))
        ]
        top_notes = pick_multimodal_notes(
            with_cover,
            crawler,
            media_type="image",
            floor_n=_TOP_N,
            hard_max=_HARD_MAX,
        )

        if not top_notes:
            self._write_empty_annotations(
                context, reason="no_images", total=len(notes_image)
            )
            await self.emit_progress(
                task_id, "图文分析:无图文样本,跳过", progress=35
            )
            return AgentResult(
                ok=True,
                produced_modules=self.provides,
                output={"image_stats": {"total": 0}},
            )

        await self.emit_progress(
            task_id,
            f"图文分析:开始并发标注 {len(top_notes)} 条图文(concurrency={_CONCURRENCY})",
            progress=35,
        )

        system_prompt_base = prompt_registry.load("image_6elements.md")
        taxonomy_hint = await self._build_taxonomy_hint()
        system_prompt = system_prompt_base + "\n\n" + taxonomy_hint

        semaphore = asyncio.Semaphore(_CONCURRENCY)

        async def _one(note: Dict[str, Any]) -> tuple[str, Optional[Dict[str, Any]]]:
            async with semaphore:
                return await self._annotate_image(task_id, system_prompt, note)

        results = await asyncio.gather(*[_one(n) for n in top_notes], return_exceptions=False)

        annotations: Dict[str, Dict[str, Any]] = {}
        success = 0
        failed = 0
        for note_id, ann in results:
            if ann is not None:
                annotations[note_id] = ann
                success += 1
            else:
                failed += 1

        # 把不在枚举内的自拟类型直接注册进 taxonomy(与 VideoAgent 同样策略)
        await self._feed_pending_types(annotations)

        # 深 merge annotations: 先读已有 annotations(可能 VideoAgent 先写过),
        # update 进新增的 image annotations,再整个写回(TaskContextWriter merge=True
        # 是浅 merge,annotations 这个 dict 子字段必须手动合并)。
        existing_mm = context.task_context.get("multimodal_output") or {}
        merged_anns = dict(existing_mm.get("annotations") or {})
        merged_anns.update(annotations)
        TaskContextWriter(context.task_context).write(
            "multimodal_output",
            {
                "annotations": merged_anns,
                "image_stats": {
                    "total": len(top_notes),
                    "success": success,
                    "failed": failed,
                    "source_agent": "ImageAnalysisAgent",
                },
            },
            agent_id=self.agent_id,
            merge=True,
            note="image_6elements_sync",
        )

        await self.emit_progress(
            task_id,
            f"图文分析:{success}/{len(top_notes)} 成功 · 失败 {failed}",
            progress=45,
        )
        return AgentResult(
            ok=True,
            produced_modules=self.provides,
            output={
                "annotations_count": len(annotations),
                "image_stats": {"total": len(top_notes), "success": success, "failed": failed},
            },
        )

    # ------------------------------------------------------------------
    # 单条图文标注
    # ------------------------------------------------------------------

    async def _annotate_image(
        self,
        task_id: str,
        system_prompt: str,
        note: Dict[str, Any],
    ) -> tuple[str, Optional[Dict[str, Any]]]:
        note_id = note.get("note_id") or ""
        cover_url = note.get("cover_url") or ""
        image_urls = list(note.get("image_urls") or [])
        if not cover_url and image_urls:
            cover_url = image_urls[0]
        if not note_id or not cover_url:
            return (note_id, None)

        user_text = (
            f"【笔记标题】{note.get('title', '')}\n"
            f"【点赞】{note.get('likes', 0)} · 评论 {note.get('comments', 0)} · "
            f"收藏 {note.get('collects', 0)}\n"
            f"【文案摘要】{(note.get('desc') or '')[:280]}\n\n"
            "请对这条图文笔记按上面 6 要素 + pain + direction 做结构化标注,严格 JSON 输出。"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": cover_url}},
                ],
            },
        ]

        for attempt in range(2):
            try:
                response = await asyncio.wait_for(
                    self._gateway.chat(
                        agent_id=self.agent_id,
                        messages=messages,
                        modality="multimodal",
                        task_id=task_id,
                        overrides={
                            "temperature": 0.2,
                            "max_tokens": 400,
                            "response_format": {"type": "json_object"},
                        },
                    ),
                    timeout=_PER_NOTE_TIMEOUT,
                )
                text = (response.get("content") or "").strip()
                parsed = extract_json_object(text)
                if parsed and self._is_valid_annotation(parsed):
                    parsed = self._coerce_annotation(parsed)
                    parsed["source_agent"] = "ImageAnalysisAgent"
                    return (note_id, parsed)
            except asyncio.TimeoutError:
                if attempt == 1:
                    await self.emit_log(task_id, "warn", f"图文 {note_id} 分析超时")
            except ModelInvocationError as exc:
                if attempt == 1:
                    await self.emit_log(
                        task_id, "warn", f"图文 {note_id} 模型降级:{exc.code}"
                    )
            except Exception as exc:  # noqa: BLE001
                if attempt == 1:
                    await self.emit_log(
                        task_id, "warn", f"图文 {note_id} 异常:{type(exc).__name__}:{exc}"
                    )
        return (note_id, None)

    @staticmethod
    def _annotation_field_str(value: Any) -> str:
        """LLM JSON 偶发返回数字/bool;先转成可校验的短字符串,避免整条标注被误丢。"""
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float, bool)):
            return str(value).strip()
        return str(value).strip()

    @staticmethod
    def _is_valid_annotation(parsed: Dict[str, Any]) -> bool:
        core = [
            "cover_type",
            "cover_text_type",
            "title_type",
            "opening_type",
            "product_intro_type",
            "product_placement_type",
        ]
        for k in core:
            if not ImageAnalysisAgent._annotation_field_str(parsed.get(k)):
                return False
        for k in ("product_intro_type", "product_placement_type"):
            u = ImageAnalysisAgent._annotation_field_str(parsed.get(k)).upper()
            if not u or u == "_NONE":
                return False
        return True

    @staticmethod
    def _coerce_annotation(parsed: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k in _ANNOTATION_KEYS:
            v = parsed.get(k) or ""
            out[k] = str(v).strip() if isinstance(v, str) else str(v)
        return out

    # ------------------------------------------------------------------
    # 辅助(和 VideoAgent 同构)
    # ------------------------------------------------------------------

    async def _build_taxonomy_hint(self) -> str:
        try:
            from ...services.viral_taxonomy_loader import get_viral_taxonomy_loader

            data = await get_viral_taxonomy_loader().get()
            taxonomy = data.get("taxonomy", {})
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[ImageAgent] 读 taxonomy 失败: {exc}")
            return ""

        label_map = {
            "A_cover": "cover_type",
            "B_cover_text": "cover_text_type",
            "C_title": "title_type",
            "D_opening": "opening_type",
            "E_product_intro": "product_intro_type",
            "F_product_placement": "product_placement_type",
        }
        lines = ["## 候选枚举表(当前品类)"]
        for element_code, field_name in label_map.items():
            values = taxonomy.get(element_code, [])
            if values:
                lines.append(f"- **{field_name}**: {' / '.join(values)}")
        return "\n".join(lines)

    async def _feed_pending_types(
        self, annotations: Dict[str, Dict[str, Any]]
    ) -> None:
        try:
            from ...services.viral_taxonomy_loader import get_viral_taxonomy_loader

            loader = get_viral_taxonomy_loader()
            data = await loader.get()
            taxonomy = data.get("taxonomy", {})
            pending = data.get("pending_additions", {})
        except Exception:
            return

        field_to_code = {
            "cover_type": ElementCode.A_COVER,
            "cover_text_type": ElementCode.B_COVER_TEXT,
            "title_type": ElementCode.C_TITLE,
            "opening_type": ElementCode.D_OPENING,
            "product_intro_type": ElementCode.E_PRODUCT_INTRO,
            "product_placement_type": ElementCode.F_PRODUCT_PLACEMENT,
        }
        for ann in annotations.values():
            for field, code in field_to_code.items():
                val = (ann.get(field) or "").strip()
                if not val or val in ("_OTHER", "_NONE"):
                    continue
                allowed = set(taxonomy.get(code.value, [])) | set(
                    pending.get(code.value, [])
                )
                if val not in allowed:
                    try:
                        await loader.propose_new_type(code, val)
                    except Exception:
                        pass

    def _write_empty_annotations(
        self,
        context: AgentContext,
        *,
        reason: str,
        total: int,
    ) -> None:
        # 即使空也要保留已有 annotations(VideoAgent 可能已写)
        existing_mm = context.task_context.get("multimodal_output") or {}
        existing_anns = dict(existing_mm.get("annotations") or {})
        TaskContextWriter(context.task_context).write(
            "multimodal_output",
            {
                "annotations": existing_anns,
                "image_stats": {
                    "total": total,
                    "success": 0,
                    "failed": 0,
                    "skipped_reason": reason,
                    "source_agent": "ImageAnalysisAgent",
                },
            },
            agent_id=self.agent_id,
            merge=True,
            note=f"image_empty_{reason}",
        )
