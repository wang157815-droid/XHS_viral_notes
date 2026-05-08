"""模块/段落重生执行器（4.3）：后台跑 Agent + LLM 子树合并 + SSE 全模块快照。"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from loguru import logger

from ..domain.error_codes import ErrorCode
from ..domain.events import TaskEventType
from ..domain.module_status import ModuleStatus
from ..domain.task_context import TaskContextWriter, task_context_store
from ..infrastructure.event_bus import task_event_bus
from ..infrastructure.repository import task_repository
from ..llm import model_gateway
from ..llm.model_gateway import ModelInvocationError
from .agents import AgentContext
from .agents._json_parsing import extract_json_object
from .agents.canvas_render_agent import (
    _build_overview_stats,
    _build_pain_points,
    _build_sample_module,
    _build_seo_insights,
    _build_viral_model_matrix,
)
from .agents.insight_agent import InsightAgent
from .agents.prompts import prompt_registry
from .agents.sheet2_narrative_agent import Sheet2NarrativeAgent
from .agents.viral_model_agent import ViralModelAgent
from .paragraph_feedback import list_feedback_hints_for_paragraphs
from .task_service import task_service
from ..infrastructure.execution import TaskExecutionHandle


class ParagraphScope(str, Enum):
    MATRIX_MODEL = "matrix_model"
    MATRIX_CATEGORY = "matrix_category"
    PAIN_ITEM = "pain_item"
    SEO_CORE_ITEM = "seo_core_item"
    SEO_LONG_ITEM = "seo_long_item"
    SAMPLE_NOTE = "sample_note"
    UNKNOWN = "unknown"


@dataclass
class ParagraphRoute:
    scope: ParagraphScope
    model_index: Optional[int] = None
    element_code: Optional[str] = None
    category_index: Optional[int] = None
    list_index: Optional[int] = None
    sample_module_id: Optional[str] = None
    note_id: Optional[str] = None


_RE_MATRIX_MODEL = re.compile(r"^M(\d+)$", re.I)
_RE_MATRIX_CAT = re.compile(r"^M(\d+)-([A-Za-z_]+)-C(\d+)$", re.I)
_RE_PAIN = re.compile(r"^P(\d+)$", re.I)
_RE_SEO = re.compile(r"^(S-core|S-long)-(\d+)$", re.I)
def parse_paragraph_id(paragraph_id: str) -> ParagraphRoute:
    pid = (paragraph_id or "").strip()
    if not pid:
        return ParagraphRoute(scope=ParagraphScope.UNKNOWN)

    m = _RE_MATRIX_MODEL.match(pid)
    if m:
        return ParagraphRoute(scope=ParagraphScope.MATRIX_MODEL, model_index=int(m.group(1)))

    m = _RE_MATRIX_CAT.match(pid)
    if m:
        return ParagraphRoute(
            scope=ParagraphScope.MATRIX_CATEGORY,
            model_index=int(m.group(1)),
            element_code=m.group(2),
            category_index=int(m.group(3)),
        )

    m = _RE_PAIN.match(pid)
    if m:
        return ParagraphRoute(scope=ParagraphScope.PAIN_ITEM, list_index=int(m.group(1)))

    m = _RE_SEO.match(pid)
    if m:
        idx = int(m.group(2))
        if m.group(1).lower() == "s-core":
            return ParagraphRoute(scope=ParagraphScope.SEO_CORE_ITEM, list_index=idx)
        return ParagraphRoute(scope=ParagraphScope.SEO_LONG_ITEM, list_index=idx)

    if "-note-" in pid:
        left, right = pid.rsplit("-note-", 1)
        if left and right:
            return ParagraphRoute(
                scope=ParagraphScope.SAMPLE_NOTE,
                sample_module_id=left,
                note_id=right,
            )

    return ParagraphRoute(scope=ParagraphScope.UNKNOWN)


def _feedback_resolver(
    saved: Dict[str, Dict[str, Any]],
) -> Callable[[str], Dict[str, Any]]:
    def _fn(module_id: str) -> Dict[str, Any]:
        return copy.deepcopy((saved.get(module_id) or {}).get("feedback_map") or {})

    return _fn


def _save_feedback_snapshots(canvas: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for m in canvas.modules:
        c = m.content if isinstance(m.content, dict) else {}
        fm = c.get("feedback_map")
        if isinstance(fm, dict) and fm:
            out[m.module_id] = {"feedback_map": copy.deepcopy(fm)}
    return out


def _merge_feedback(into_content: Dict[str, Any], feedback_map: Dict[str, Any]) -> Dict[str, Any]:
    content = copy.deepcopy(into_content)
    if feedback_map:
        content["feedback_map"] = copy.deepcopy(feedback_map)
    return content


async def _emit_full_module(task_id: str, module_id: str) -> None:
    canvas = task_service.get_canvas(task_id)
    mod = canvas.find_module(module_id)
    if mod:
        await task_event_bus.publish_event(
            task_id=task_id,
            type=TaskEventType.CANVAS_MODULE_UPDATED,
            payload=mod.to_dict(),
        )


async def _emit_error(task_id: str, message: str) -> None:
    await task_event_bus.publish_event(
        task_id=task_id,
        type=TaskEventType.ERROR,
        payload={"code": ErrorCode.SYSTEM_INTERNAL.value, "message": message},
    )


def _bump_repo_ctx(task_id: str, ctx: Any) -> None:
    task_repository.bump_context_version(task_id, ctx.context_version)


async def _llm_refinement(
    task_id: str,
    *,
    scope: str,
    paragraph_id: str,
    subtree: Dict[str, Any],
    instruction: str,
    extra_hints: str,
) -> Dict[str, Any]:
    system = prompt_registry.load("paragraph_refinement.md")
    user = json.dumps(
        {
            "scope": scope,
            "paragraph_id": paragraph_id,
            "instruction": instruction,
            "hints": extra_hints,
            "subtree": subtree,
        },
        ensure_ascii=False,
    )
    try:
        raw = await model_gateway.chat(
            "ParagraphRefinementAgent",
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            task_id=task_id,
        )
        text = str((raw or {}).get("content") or "")
        data = extract_json_object(text) or {}
        if not data:
            raise ModelInvocationError(
                ErrorCode.MODEL_UPSTREAM_ERROR.value,
                "段落精炼:LLM 未返回可解析 JSON",
            )
        return data
    except ModelInvocationError:
        raise
    except Exception as exc:
        raise ModelInvocationError(
            ErrorCode.MODEL_UPSTREAM_ERROR.value,
            f"段落精炼解析失败: {exc}",
        ) from exc


def _get_matrix_category_subtree(
    vm: Dict[str, Any], route: ParagraphRoute
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    models = list(vm.get("models") or [])
    mi = (route.model_index or 1) - 1
    if mi < 0 or mi >= len(models):
        raise ValueError("matrix model index 越界")
    model = copy.deepcopy(models[mi])
    code = route.element_code or ""
    el = model.get("elements") or {}
    cats = list(el.get(code) or [])
    ci = (route.category_index or 1) - 1
    if ci < 0 or ci >= len(cats):
        raise ValueError("matrix category index 越界")
    return model, copy.deepcopy(cats[ci])


def _apply_matrix_category(
    vm: Dict[str, Any], route: ParagraphRoute, delta: Dict[str, Any]
) -> Dict[str, Any]:
    if "category" not in delta:
        return copy.deepcopy(vm)
    out = copy.deepcopy(vm)
    models = list(out.get("models") or [])
    mi = (route.model_index or 1) - 1
    code = route.element_code or ""
    ci = (route.category_index or 1) - 1
    model = copy.deepcopy(models[mi])
    el = dict(model.get("elements") or {})
    cats = list(el.get(code) or [])
    cat_obj = delta.get("category")
    if cat_obj is None:
        if 0 <= ci < len(cats):
            cats.pop(ci)
    elif isinstance(cat_obj, dict):
        pid = None
        if 0 <= ci < len(cats):
            pid = (cats[ci] or {}).get("paragraph_id")
        cat_obj = dict(cat_obj)
        if pid:
            cat_obj["paragraph_id"] = pid
        if 0 <= ci < len(cats):
            cats[ci] = cat_obj
    el[code] = cats
    model["elements"] = el
    models[mi] = model
    out["models"] = models
    return out


def _get_model_subtree(vm: Dict[str, Any], route: ParagraphRoute) -> Dict[str, Any]:
    models = list(vm.get("models") or [])
    mi = (route.model_index or 1) - 1
    if mi < 0 or mi >= len(models):
        raise ValueError("matrix model index 越界")
    return copy.deepcopy(models[mi])


def _apply_matrix_model(vm: Dict[str, Any], route: ParagraphRoute, delta: Dict[str, Any]) -> Dict[str, Any]:
    if "model" not in delta:
        return copy.deepcopy(vm)
    out = copy.deepcopy(vm)
    models = list(out.get("models") or [])
    mi = (route.model_index or 1) - 1
    mobj = delta.get("model")
    if mobj is None:
        if 0 <= mi < len(models):
            models.pop(mi)
    elif isinstance(mobj, dict):
        mobj = dict(mobj)
        if mi < len(models) and models[mi].get("paragraph_id"):
            mobj.setdefault("paragraph_id", models[mi].get("paragraph_id"))
        if mi < len(models):
            models[mi] = mobj
        else:
            models.append(mobj)
    out["models"] = models
    return out


def _apply_pain_item(semantic: Dict[str, Any], index1: int, delta: Dict[str, Any]) -> Dict[str, Any]:
    if "item" not in delta:
        return copy.deepcopy(semantic)
    out = copy.deepcopy(semantic)
    items = list(out.get("pain_points_top") or [])
    ii = index1 - 1
    item = delta.get("item")
    if item is None:
        if 0 <= ii < len(items):
            items.pop(ii)
    elif isinstance(item, dict) and 0 <= ii < len(items):
        old = items[ii] if isinstance(items[ii], dict) else {}
        merged = {**old, **{k: v for k, v in item.items() if v is not None}}
        items[ii] = merged
    out["pain_points_top"] = items
    return out


def _apply_seo_item(
    semantic: Dict[str, Any],
    *,
    kind: ParagraphScope,
    index1: int,
    delta: Dict[str, Any],
) -> Dict[str, Any]:
    if "item" not in delta:
        return copy.deepcopy(semantic)
    out = copy.deepcopy(semantic)
    seo = dict(out.get("seo_aggregation") or {})
    key = "core_keywords" if kind == ParagraphScope.SEO_CORE_ITEM else "long_tail"
    items = list(seo.get(key) or [])
    ii = index1 - 1
    item = delta.get("item")
    if item is None:
        if 0 <= ii < len(items):
            items.pop(ii)
    elif isinstance(item, dict) and 0 <= ii < len(items):
        old = items[ii] if isinstance(items[ii], dict) else {}
        merged = {**old, **{k: v for k, v in item.items() if v is not None}}
        items[ii] = merged
    seo[key] = items
    out["seo_aggregation"] = seo
    return out


def _apply_sample_patch(
    content: Dict[str, Any], note_id: str, patch: Dict[str, Any]
) -> Dict[str, Any]:
    out = copy.deepcopy(content)
    notes = list(out.get("notes") or [])
    for i, row in enumerate(notes):
        if not isinstance(row, dict):
            continue
        if str(row.get("note_id") or "") == note_id:
            notes[i] = {**row, **{k: v for k, v in patch.items() if v is not None}}
            break
    out["notes"] = notes
    return out


async def run_module_regeneration(
    *,
    task_id: str,
    module_id: str,
    paragraph_id: Optional[str],
    instruction: str,
    feedback_hint: str,
    cascade: bool,
) -> None:
    _ = cascade  # 级联 stale 已在 API 同步完成；执行器内专注内容刷新
    try:
        canvas = task_service.get_canvas(task_id)
        fb_saved = _save_feedback_snapshots(canvas)
        feedback_fn = _feedback_resolver(fb_saved)

        ctx = task_context_store.require(task_id)
        handle = TaskExecutionHandle(task_id=task_id)
        agent_ctx = AgentContext(task_id=task_id, task_context=ctx, handle=handle)

        route = parse_paragraph_id(paragraph_id) if paragraph_id else ParagraphRoute(
            scope=ParagraphScope.UNKNOWN
        )

        mod = canvas.find_module(module_id)
        content_hints = (
            list_feedback_hints_for_paragraphs(mod.content if mod else {}, [paragraph_id])
            if paragraph_id and mod
            else {}
        )
        hint_text = " ".join(
            x for x in [feedback_hint, content_hints.get(paragraph_id or "", "")] if x
        )

        if module_id == "mod-viral-model-matrix":
            await _regen_viral_matrix(
                task_id,
                agent_ctx,
                route,
                paragraph_id or "",
                instruction,
                hint_text,
                feedback_fn,
            )
            return

        if module_id in ("mod-pain-points", "mod-seo-insights"):
            await _regen_insight_modules(
                task_id,
                agent_ctx,
                module_id,
                route,
                paragraph_id or "",
                instruction,
                hint_text,
                feedback_fn,
            )
            return

        if module_id == "mod-overview-stats":
            await _regen_overview(task_id, agent_ctx, feedback_fn)
            return

        if "samples" in module_id:
            await _regen_sample_module(
                task_id,
                module_id,
                route,
                paragraph_id or "",
                instruction,
                hint_text,
                feedback_fn,
            )
            return

        # 其它模块：仅解除 generating
        task_service.update_module_content(
            task_id,
            module_id,
            status=ModuleStatus.READY,
            agent_id="module_regeneration",
        )
        await _emit_full_module(task_id, module_id)
    except Exception as exc:
        logger.exception("[module_regeneration] task={} module={} 失败: {}", task_id, module_id, exc)
        try:
            task_service.update_module_content(
                task_id,
                module_id,
                status=ModuleStatus.READY,
                agent_id="module_regeneration",
            )
            await _emit_full_module(task_id, module_id)
        except Exception:
            logger.exception("[module_regeneration] 降级写回失败 task={}", task_id)
        await _emit_error(task_id, f"模块重生失败: {exc}")


def _write_viral_matrix(ctx: Any, vm: Dict[str, Any]) -> None:
    TaskContextWriter(ctx).write(
        "viral_model_output",
        vm,
        agent_id="module_regeneration",
        note="paragraph_refine",
    )
    _bump_repo_ctx(ctx.task_id, ctx)


def _write_semantic(ctx: Any, semantic: Dict[str, Any]) -> None:
    TaskContextWriter(ctx).write(
        "semantic_output",
        semantic,
        agent_id="module_regeneration",
        note="paragraph_refine",
    )
    _bump_repo_ctx(ctx.task_id, ctx)


async def _regen_viral_matrix(
    task_id: str,
    agent_ctx: AgentContext,
    route: ParagraphRoute,
    paragraph_id: str,
    instruction: str,
    hint_text: str,
    feedback_fn: Callable[[str], Dict[str, Any]],
) -> None:
    ctx = agent_ctx.task_context
    vm = ctx.get("viral_model_output")
    if not isinstance(vm, dict):
        vm = {}

    if route.scope == ParagraphScope.MATRIX_CATEGORY and paragraph_id:
        try:
            _, cat = _get_matrix_category_subtree(vm, route)
            action = (feedback_fn("mod-viral-model-matrix").get(paragraph_id) or {}).get("action")
            if action == "delete":
                vm2 = _apply_matrix_category(vm, route, {"category": None})
            else:
                delta = await _llm_refinement(
                    task_id,
                    scope="matrix_category",
                    paragraph_id=paragraph_id,
                    subtree=cat,
                    instruction=instruction,
                    extra_hints=hint_text,
                )
                vm2 = _apply_matrix_category(vm, route, delta)
            _write_viral_matrix(ctx, vm2)
            await Sheet2NarrativeAgent().run(agent_ctx)
            _push_matrix_module(task_id, ctx, feedback_fn("mod-viral-model-matrix"))
            await _emit_full_module(task_id, "mod-viral-model-matrix")
            return
        except (ValueError, ModelInvocationError) as exc:
            logger.warning(
                "[module_regeneration] 矩阵分类定向失败,降级整表: {}",
                exc,
            )

    if route.scope == ParagraphScope.MATRIX_MODEL and paragraph_id:
        try:
            subtree = _get_model_subtree(vm, route)
            action = (feedback_fn("mod-viral-model-matrix").get(paragraph_id) or {}).get("action")
            if action == "delete":
                delta = {"model": None}
            else:
                delta = await _llm_refinement(
                    task_id,
                    scope="matrix_model",
                    paragraph_id=paragraph_id,
                    subtree=subtree,
                    instruction=instruction,
                    extra_hints=hint_text,
                )
            vm2 = _apply_matrix_model(vm, route, delta)
            _write_viral_matrix(ctx, vm2)
            await Sheet2NarrativeAgent().run(agent_ctx)
            _push_matrix_module(task_id, ctx, feedback_fn("mod-viral-model-matrix"))
            await _emit_full_module(task_id, "mod-viral-model-matrix")
            return
        except (ValueError, ModelInvocationError) as exc:
            logger.warning(
                "[module_regeneration] 矩阵模型定向失败,降级整表: {}",
                exc,
            )

    # 整模块重生
    await ViralModelAgent().run(agent_ctx)
    await Sheet2NarrativeAgent().run(agent_ctx)
    _push_matrix_module(task_id, ctx, feedback_fn("mod-viral-model-matrix"))
    await _emit_full_module(task_id, "mod-viral-model-matrix")


def _push_matrix_module(task_id: str, ctx: Any, feedback_map: Dict[str, Any]) -> None:
    semantic = ctx.get("semantic_output") or {}
    viral_matrix = ctx.get("viral_model_output") or {}
    stats_axis_label = str(
        semantic.get("stats_axis_label") or "高频痛点 / 议程"
    ).strip() or "高频痛点 / 议程"
    canvas = task_service.get_canvas(task_id)
    old = canvas.find_module("mod-viral-model-matrix")
    dep = list(old.depends_on) if old and old.depends_on else None
    mod = _build_viral_model_matrix(
        viral_matrix,
        stats_axis_label=stats_axis_label,
        sample_modules_dep=dep,
    )
    content = _merge_feedback(mod.content, feedback_map)
    task_service.update_module_content(
        task_id,
        "mod-viral-model-matrix",
        content=content,
        status=ModuleStatus.READY,
        summary=mod.summary,
        agent_id="module_regeneration",
    )


async def _regen_insight_modules(
    task_id: str,
    agent_ctx: AgentContext,
    requested_module: str,
    route: ParagraphRoute,
    paragraph_id: str,
    instruction: str,
    hint_text: str,
    feedback_fn: Callable[[str], Dict[str, Any]],
) -> None:
    ctx = agent_ctx.task_context
    semantic = ctx.get("semantic_output") or {}
    if not isinstance(semantic, dict):
        semantic = {}

    pain_pid = requested_module == "mod-pain-points" and paragraph_id
    seo_pid = requested_module == "mod-seo-insights" and paragraph_id

    if route.scope == ParagraphScope.PAIN_ITEM and pain_pid:
        items = list(semantic.get("pain_points_top") or [])
        ii = (route.list_index or 1) - 1
        if 0 <= ii < len(items):
            try:
                subtree = copy.deepcopy(items[ii])
                action = (feedback_fn("mod-pain-points").get(paragraph_id) or {}).get("action")
                if action == "delete":
                    delta = {"item": None}
                else:
                    delta = await _llm_refinement(
                        task_id,
                        scope="pain_item",
                        paragraph_id=paragraph_id,
                        subtree=subtree if isinstance(subtree, dict) else {"raw": subtree},
                        instruction=instruction,
                        extra_hints=hint_text,
                    )
                sem2 = _apply_pain_item(semantic, route.list_index or 1, delta)
                _write_semantic(ctx, sem2)
                _push_insight_canvas(task_id, ctx, feedback_fn)
                await _emit_full_module(task_id, "mod-pain-points")
                await _emit_full_module(task_id, "mod-seo-insights")
                return
            except ModelInvocationError as exc:
                logger.warning("[module_regeneration] 痛点条目 LLM 失败,降级整表: {}", exc)

    if route.scope in (ParagraphScope.SEO_CORE_ITEM, ParagraphScope.SEO_LONG_ITEM) and seo_pid:
        seo = semantic.get("seo_aggregation") or {}
        key = (
            "core_keywords"
            if route.scope == ParagraphScope.SEO_CORE_ITEM
            else "long_tail"
        )
        items = list(seo.get(key) or [])
        ii = (route.list_index or 1) - 1
        if 0 <= ii < len(items):
            try:
                subtree = copy.deepcopy(items[ii])
                action = (feedback_fn("mod-seo-insights").get(paragraph_id) or {}).get("action")
                if action == "delete":
                    delta = {"item": None}
                else:
                    scope = (
                        "seo_core_item"
                        if route.scope == ParagraphScope.SEO_CORE_ITEM
                        else "seo_long_item"
                    )
                    delta = await _llm_refinement(
                        task_id,
                        scope=scope,
                        paragraph_id=paragraph_id,
                        subtree=subtree if isinstance(subtree, dict) else {},
                        instruction=instruction,
                        extra_hints=hint_text,
                    )
                sem2 = _apply_seo_item(
                    semantic, kind=route.scope, index1=route.list_index or 1, delta=delta
                )
                _write_semantic(ctx, sem2)
                _push_insight_canvas(task_id, ctx, feedback_fn)
                await _emit_full_module(task_id, "mod-pain-points")
                await _emit_full_module(task_id, "mod-seo-insights")
                return
            except ModelInvocationError as exc:
                logger.warning("[module_regeneration] SEO 条目 LLM 失败,降级整表: {}", exc)

    await InsightAgent().run(agent_ctx)
    _push_insight_canvas(task_id, ctx, feedback_fn)
    await _emit_full_module(task_id, "mod-pain-points")
    await _emit_full_module(task_id, "mod-seo-insights")


def _push_insight_canvas(task_id: str, ctx: Any, feedback_fn: Callable[[str], Dict[str, Any]]) -> None:
    semantic = ctx.get("semantic_output") or {}
    canvas = task_service.get_canvas(task_id)
    seo_old = canvas.find_module("mod-seo-insights")
    dep = list(seo_old.depends_on) if seo_old and seo_old.depends_on else None
    pm = _build_pain_points(semantic)
    sm = _build_seo_insights(semantic, competitor_depends_on=dep)
    task_service.update_module_content(
        task_id,
        "mod-pain-points",
        content=_merge_feedback(pm.content, feedback_fn("mod-pain-points")),
        status=ModuleStatus.READY,
        summary=pm.summary,
        agent_id="module_regeneration",
    )
    task_service.update_module_content(
        task_id,
        "mod-seo-insights",
        content=_merge_feedback(sm.content, feedback_fn("mod-seo-insights")),
        status=ModuleStatus.READY,
        summary=sm.summary,
        agent_id="module_regeneration",
    )


async def _regen_overview(
    task_id: str,
    agent_ctx: AgentContext,
    feedback_fn: Callable[[str], Dict[str, Any]],
) -> None:
    ctx = agent_ctx.task_context
    crawler = ctx.get("crawler_output") or {}
    input_spec = ctx.get("input_spec") or {}
    multimodal = ctx.get("multimodal_output") or {}
    annotations = multimodal.get("annotations") or {}
    if not isinstance(annotations, dict):
        annotations = {}
    mod = _build_overview_stats(crawler, input_spec, annotations=annotations)
    content = _merge_feedback(mod.content, feedback_fn("mod-overview-stats"))
    task_service.update_module_content(
        task_id,
        "mod-overview-stats",
        content=content,
        status=ModuleStatus.READY,
        summary=mod.summary,
        agent_id="module_regeneration",
    )
    await _emit_full_module(task_id, "mod-overview-stats")


async def _regen_sample_module(
    task_id: str,
    module_id: str,
    route: ParagraphRoute,
    paragraph_id: str,
    instruction: str,
    hint_text: str,
    feedback_fn: Callable[[str], Dict[str, Any]],
) -> None:
    canvas = task_service.get_canvas(task_id)
    mod = canvas.find_module(module_id)
    if not mod:
        return
    ctx = task_context_store.require(task_id)
    crawler = ctx.get("crawler_output") or {}
    multimodal = ctx.get("multimodal_output") or {}
    annotations = multimodal.get("annotations") or {}
    if not isinstance(annotations, dict):
        annotations = {}

    content = mod.content if isinstance(mod.content, dict) else {}
    if (
        route.scope == ParagraphScope.SAMPLE_NOTE
        and route.note_id
        and route.sample_module_id == module_id
    ):
        notes = list(content.get("notes") or [])
        row = next(
            (n for n in notes if isinstance(n, dict) and str(n.get("note_id")) == route.note_id),
            {},
        )
        action = (feedback_fn(module_id).get(paragraph_id) or {}).get("action")
        if action == "delete":
            new_notes = [
                n
                for n in notes
                if not (isinstance(n, dict) and str(n.get("note_id")) == route.note_id)
            ]
            c2 = {**content, "notes": new_notes, "sample_count": len(new_notes)}
        else:
            delta = await _llm_refinement(
                task_id,
                scope="sample_note",
                paragraph_id=paragraph_id,
                subtree=row if isinstance(row, dict) else {},
                instruction=instruction,
                extra_hints=hint_text,
            )
            patch = delta.get("patch") if isinstance(delta.get("patch"), dict) else {}
            c2 = _apply_sample_patch(content, route.note_id, patch)
        task_service.update_module_content(
            task_id,
            module_id,
            content=_merge_feedback(c2, feedback_fn(module_id)),
            status=ModuleStatus.READY,
            agent_id="module_regeneration",
        )
        await _emit_full_module(task_id, module_id)
        return

    # 整表重建（无 note 定位或与路由不一致）
    sources = crawler.get("sources") or {}
    source_type = str(content.get("source_type") or "")
    media_kind = content.get("media_kind")
    notes_override = None
    for key in ("competitor", "top_interaction", "serp_top"):
        if key == source_type:
            raw = list(sources.get(key) or [])
            if media_kind == "video":
                raw = [n for n in raw if isinstance(n, dict) and n.get("media_type") == "video"]
            elif media_kind == "image":
                raw = [n for n in raw if isinstance(n, dict) and n.get("media_type") == "image"]
            notes_override = raw
            break

    layer = int(mod.layer) if int(mod.layer) in (1, 2, 3) else 3
    rebuilt = _build_sample_module(
        module_id=module_id,
        title=mod.title,
        source_type=source_type or "competitor",
        crawler=crawler,
        annotations=annotations,
        source_key=source_type or "competitor",
        layer=layer,  # type: ignore[arg-type]
        default_expanded=bool(mod.default_expanded),
        notes_override=notes_override,
        media_kind=str(media_kind) if media_kind else None,
    )
    task_service.update_module_content(
        task_id,
        module_id,
        content=_merge_feedback(rebuilt.content, feedback_fn(module_id)),
        status=ModuleStatus.READY,
        summary=rebuilt.summary,
        agent_id="module_regeneration",
    )
    await _emit_full_module(task_id, module_id)
