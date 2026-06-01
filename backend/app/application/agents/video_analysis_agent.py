"""
VideoAnalysisAgent: 视频笔记 6 要素标注器(阶段 4.3pre.2 重写)。

**重大变化(相比 4.2 版本)**:
- 从 fire-and-forget 异步支路回退为**并发同步 stage**(Semaphore 5,主流程 await)
- 输出从"汇总报告 segments/golden_quotes/..." 改为**每条视频的 6 要素标注**
- 不再发 TASK_VIDEO_DONE / async_pending / disabled_reason,简化协议

职责(新):
- 从 crawler_output.notes_video 选样:**四源样本表可见视频优先**,再按互动分补足至 floor/hard 上限
- 用 qwen3-vl URL 直传给每条视频做"6 要素 + pain + direction" 8 字段结构化分析
- 写到 `multimodal_output.annotations[note_id]`(和 ImageAgent 共享同一 dict)
- 标签优先 taxonomy 枚举;无匹配可自拟中文;禁止 `_NONE`/空串(校验不通过则本条丢弃)

产出结构(写 multimodal_output):
  {
    "annotations": {
      "note_id_1": {
        "cover_type": "...",
        "cover_text_type": "...",
        "title_type": "...",
        "opening_type": "...",
        "product_intro_type": "...",
        "product_placement_type": "...",
        "pain_keywords": "...",
        "content_direction": "...",
        "source_agent": "VideoAnalysisAgent",
      },
      ...
    },
    "video_stats": { "total": N, "success": M, "failed": K, "skipped_disabled": 0|N }
  }

env 开关:
- VIDEO_ANALYSIS_ENABLED: system_settings 里的 toggle(保持 4.2 逻辑)
- VIDEO_ANALYSIS_MAX_COUNT: 全池「至少」分析条数(与四源可见视频数取 max),默认 30
- VIDEO_ANALYSIS_HARD_MAX: 单任务绝对上限(控成本),默认 80
- VIDEO_ANALYSIS_CONCURRENCY: Semaphore 并发,默认 5
- VIDEO_ANALYSIS_PER_TIMEOUT: 单条推理超时,默认 60
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from ...domain.task_context import TaskContextWriter
from ...domain.viral_model import ElementCode
from ...llm.model_gateway import ModelInvocationError
from ._json_parsing import extract_json_object
from .base import AgentContext, AgentResult, BaseAgent
from .multimodal_note_pick import pick_multimodal_notes
from .prompts import prompt_registry


_MAX_VIDEOS = int(os.getenv("VIDEO_ANALYSIS_MAX_COUNT", "30"))
_HARD_MAX = int(os.getenv("VIDEO_ANALYSIS_HARD_MAX", "80"))
_CONCURRENCY = int(os.getenv("VIDEO_ANALYSIS_CONCURRENCY", "2"))
_PER_VIDEO_TIMEOUT = int(os.getenv("VIDEO_ANALYSIS_PER_TIMEOUT", "60"))
# 限流重试退避基准（秒），遇到 429 后等待 _RATE_LIMIT_BACKOFF * (attempt+1)
_RATE_LIMIT_BACKOFF = float(os.getenv("VIDEO_ANALYSIS_RATE_LIMIT_BACKOFF", "10"))

# 视频源模式：
#   url   - 直接把 XHS CDN URL 传给 AI（默认；部分 AI 服务商服务器可直接访问 XHS CDN）
#   proxy - 本地下载视频再转 base64 传给 AI（绕过防盗链，但有大小限制）
_VIDEO_SOURCE_MODE = os.getenv("VIDEO_SOURCE_MODE", "url").lower()
# proxy 模式最大原始视频大小（MiMo base64 限制 50MB，base64 膨胀约 1.35x，取 35MB 保留余量）
_PROXY_MAX_BYTES = int(os.getenv("VIDEO_MAX_SIZE_MB", "35")) * 1024 * 1024
# XHS CDN 下载时使用的 Referer（防盗链需要）
_XHS_REFERER = os.getenv("XHS_WEB_ORIGIN", "https://www.xiaohongshu.com")
_DOWNLOAD_TIMEOUT = int(os.getenv("VIDEO_DOWNLOAD_TIMEOUT", "60"))


# 标注字段名(对齐 ViralNote 的 7 个标注字段)
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


class VideoAnalysisAgent(BaseAgent):
    agent_id = "VideoAnalysisAgent"
    # 4.3pre.3: 不再独立展示 `mod-video-analysis` 画布模块,
    # 标注结果融入 `mod-viral-model-matrix` 的 6 要素
    provides: List[str] = []
    depends_on: List[str] = []
    write_partition = "multimodal_output"

    async def run(self, context: AgentContext) -> AgentResult:
        task_id = context.task_id
        crawler = context.task_context.get("crawler_output") or {}
        notes_video: List[Dict[str, Any]] = list(crawler.get("notes_video") or [])

        # ---- 管理员开关(保留 4.α-patch ghost toggle 修复) ----
        enabled = await self._read_enabled_flag(task_id)
        if not enabled:
            await self.emit_progress(
                task_id, "视频分析:设置页已关闭,跳过本次", progress=40
            )
            # 写空标注占位,让下游 ViralModel 能消费同样字段结构
            self._write_empty_annotations(
                context, reason="admin_toggle_off", total=len(notes_video)
            )
            return AgentResult(
                ok=True,
                produced_modules=self.provides,
                output={"video_stats": {"skipped_disabled": True}},
            )

        with_url = [n for n in notes_video if (n.get("video_url") or "").strip()]
        picked = pick_multimodal_notes(
            with_url,
            crawler,
            media_type="video",
            floor_n=_MAX_VIDEOS,
            hard_max=_HARD_MAX,
        )
        if not picked:
            self._write_empty_annotations(
                context, reason="no_videos", total=len(notes_video)
            )
            await self.emit_progress(
                task_id, "视频分析:无视频样本,跳过", progress=40
            )
            return AgentResult(
                ok=True, produced_modules=self.provides, output={"video_stats": {"total": 0}}
            )

        await self.emit_progress(
            task_id,
            f"视频分析:开始并发标注 {len(picked)} 条视频(concurrency={_CONCURRENCY})",
            progress=40,
        )
        await self._emit_thinking_chunk(
            task_id,
            f"开始批量标注 {len(picked)} 条视频笔记，"
            f"逐条提取视觉风格、标题钩子、封面类型、开篇方式、产品引出与植入方式六大要素，"
            f"以及用户痛点关键词与内容方向...\n",
            is_reasoning=True,
        )

        # ---- 并发同步执行 ----
        system_prompt_base = prompt_registry.load("video_6elements.md")
        taxonomy_hint = await self._build_taxonomy_hint()
        system_prompt = system_prompt_base + "\n\n" + taxonomy_hint

        semaphore = asyncio.Semaphore(_CONCURRENCY)
        done_count = 0
        total_count = len(picked)

        async def _one(note: Dict[str, Any]) -> tuple[str, Optional[Dict[str, Any]]]:
            nonlocal done_count
            async with semaphore:
                result = await self._annotate_video(task_id, system_prompt, note)
            done_count += 1
            if done_count % 10 == 0 or done_count == total_count:
                await self._emit_thinking_chunk(
                    task_id,
                    f"已标注 {done_count}/{total_count} 条视频笔记...\n",
                    is_reasoning=True,
                )
            return result

        results = await asyncio.gather(
            *[_one(n) for n in picked], return_exceptions=False
        )

        annotations: Dict[str, Dict[str, Any]] = {}
        success = 0
        failed = 0
        for note_id, ann in results:
            if ann is not None:
                annotations[note_id] = ann
                success += 1
            else:
                failed += 1

        rate = int(success / total_count * 100) if total_count else 0
        await self._emit_thinking_chunk(
            task_id,
            f"视频标注完成：{success}/{total_count} 条成功（成功率 {rate}%），"
            f"已提取六要素分布、用户痛点与内容方向，数据将输入爆文模型聚类。\n",
            is_reasoning=True,
        )
        await self._emit_thinking_done(task_id)

        # 不在枚举内的值直接写入 taxonomy(幻觉/自拟兜底)
        await self._feed_pending_types(annotations)

        # ---- 写回 TaskContext (深 merge annotations,可能 ImageAgent 也在并发写) ----
        existing_mm = context.task_context.get("multimodal_output") or {}
        merged_anns = dict(existing_mm.get("annotations") or {})
        merged_anns.update(annotations)
        TaskContextWriter(context.task_context).write(
            "multimodal_output",
            {
                "annotations": merged_anns,
                "video_stats": {
                    "total": len(picked),
                    "success": success,
                    "failed": failed,
                    "source_agent": "VideoAnalysisAgent",
                },
            },
            agent_id=self.agent_id,
            merge=True,
            note="video_6elements_sync",
        )

        await self.emit_progress(
            task_id,
            f"视频分析:{success}/{len(picked)} 成功 · 失败 {failed}",
            progress=50,
        )

        return AgentResult(
            ok=True,
            produced_modules=self.provides,
            output={
                "annotations_count": len(annotations),
                "video_stats": {"total": len(picked), "success": success, "failed": failed},
            },
        )

    # ------------------------------------------------------------------
    # 单条视频标注
    # ------------------------------------------------------------------

    @staticmethod
    async def _download_video_for_proxy(video_url: str) -> Optional[str]:
        """下载视频并转 base64 Data URL，供 proxy 模式使用。

        返回 ``data:{mime};base64,{b64}`` 字符串，失败时返回 None。
        MiMo base64 限制 50MB；原始视频限制由 _PROXY_MAX_BYTES 控制（默认 35MB）。
        """
        _MIME_MAP = {"mp4": "video/mp4", "mov": "video/quicktime",
                     "avi": "video/x-msvideo", "wmv": "video/x-ms-wmv"}
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": _XHS_REFERER + "/",
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(_DOWNLOAD_TIMEOUT),
            ) as client:
                # HEAD 预检大小，避免下载超大视频
                try:
                    head = await client.head(video_url, headers=headers)
                    cl = int(head.headers.get("content-length", 0))
                    if cl > _PROXY_MAX_BYTES:
                        logger.warning(
                            "[VideoAgent] proxy HEAD 预估 {:.1f}MB > {}MB，跳过下载回退 URL 模式",
                            cl / 1024 / 1024, _PROXY_MAX_BYTES // 1024 // 1024,
                        )
                        return None
                except Exception:
                    pass  # HEAD 失败忽略，继续尝试 GET

                resp = await client.get(video_url, headers=headers)
                resp.raise_for_status()

                data = resp.content
                if len(data) > _PROXY_MAX_BYTES:
                    logger.warning(
                        "[VideoAgent] proxy GET {:.1f}MB > {}MB，超限回退 URL 模式",
                        len(data) / 1024 / 1024, _PROXY_MAX_BYTES // 1024 // 1024,
                    )
                    return None

                # 探测 MIME 类型
                ct = resp.headers.get("content-type", "").split(";")[0].strip()
                if not ct or "octet-stream" in ct:
                    ext = video_url.lower().split("?")[0].rsplit(".", 1)[-1]
                    ct = _MIME_MAP.get(ext, "video/mp4")

                b64 = base64.b64encode(data).decode("ascii")
                logger.debug(
                    "[VideoAgent] proxy 下载成功 {:.1f}MB → base64 {:.1f}MB mime={}",
                    len(data) / 1024 / 1024, len(b64) / 1024 / 1024, ct,
                )
                return f"data:{ct};base64,{b64}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("[VideoAgent] proxy 下载失败: {}", exc)
            return None

    @staticmethod
    def _pick_best_video_url(note: Dict[str, Any]) -> str:
        """从 note 的 video_url / video_urls 中选出最适合传给 AI 模型的 URL。

        XHS stream 的 master_url 是 HLS m3u8 播放列表，AI 模型无法直接处理。
        优先选 origin_key 生成的直接 MP4 URL；如果主 URL 已是直接地址也直接用。
        """
        def _is_m3u8(url: str) -> bool:
            return ".m3u8" in url.lower()

        primary = (note.get("video_url") or "").strip()
        if primary and not _is_m3u8(primary):
            return primary

        # 遍历 video_urls（已按 priority 升序排列），取第一个非 m3u8
        for url_info in (note.get("video_urls") or []):
            url = (url_info.get("url") or "").strip()
            if url and not _is_m3u8(url):
                return url

        # 实在找不到直接 URL，原样回退（让模型尽力尝试）
        return primary

    async def _annotate_video(
        self,
        task_id: str,
        system_prompt: str,
        note: Dict[str, Any],
    ) -> tuple[str, Optional[Dict[str, Any]]]:
        """调 ModelGateway 对单条视频产出 8 字段标注 dict;失败返回 None。"""
        note_id = note.get("note_id") or ""
        video_url = self._pick_best_video_url(note)
        if not note_id or not video_url:
            return (note_id, None)

        # 记录实际使用的 URL 来源，方便排查 MODEL_UPSTREAM_ERROR
        raw_url = (note.get("video_url") or "").strip()
        if video_url != raw_url:
            logger.debug(
                f"[VideoAgent] {note_id} 主URL是m3u8，已切换到备用直链: {video_url[:80]}..."
            )

        # proxy 模式：本地下载视频转 base64，绕过 XHS CDN 防盗链
        # XHS CDN 对外部 IP（如 MiMo 服务器）会返回 403/400；本地下载后 base64 传给 AI 可绕过
        use_base64 = False
        actual_video_url = video_url
        if _VIDEO_SOURCE_MODE == "proxy":
            b64_data_url = await self._download_video_for_proxy(video_url)
            if b64_data_url:
                actual_video_url = b64_data_url
                use_base64 = True
                logger.debug("[VideoAgent] {} 使用 proxy base64 模式", note_id)
            else:
                logger.warning(
                    "[VideoAgent] {} proxy 下载失败，回退 URL 直传模式（可能触发防盗链 400）",
                    note_id,
                )

        user_text = (
            f"【视频标题】{note.get('title', '')}\n"
            f"【点赞】{note.get('likes', 0)} · 评论 {note.get('comments', 0)} · "
            f"收藏 {note.get('collects', 0)}\n"
            f"【文案摘要】{(note.get('desc') or '')[:300]}\n\n"
            "请对这条视频按上面 6 要素 + pain + direction 做结构化标注,严格 JSON 输出。"
        )

        video_content_part: Dict[str, Any] = {
            "type": "video_url",
            "video_url": {"url": actual_video_url},
        }
        # fps / media_resolution 是 MiMo 专用参数，其他供应商（阿里云等）不认这两个字段会返回 400
        # 仅在使用 MiMo provider 且 URL 模式时注入
        if not use_base64:
            try:
                profile = self._gateway._registry.get("multimodal_default")
                if profile and getattr(profile, "provider", "").startswith("xiaomi"):
                    video_content_part["fps"] = 2
                    video_content_part["media_resolution"] = "default"
            except Exception:
                pass

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    video_content_part,
                    {"type": "text", "text": user_text},
                ],
            },
        ]

        # 一次失败重试一次
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
                            # MiMo 使用 max_completion_tokens（Qwen/DeepSeek 也接受此字段）
                            "max_tokens": None,
                            "max_completion_tokens": 400,
                            "response_format": {"type": "json_object"},
                        },
                    ),
                    timeout=_PER_VIDEO_TIMEOUT,
                )
                text = (response.get("content") or "").strip()
                parsed = extract_json_object(text)
                if parsed and self._is_valid_annotation(parsed):
                    parsed = self._coerce_annotation(parsed)
                    parsed["source_agent"] = "VideoAnalysisAgent"
                    return (note_id, parsed)
                # 解析失败,重试
            except asyncio.TimeoutError:
                if attempt == 1:
                    await self.emit_log(task_id, "warn", f"视频 {note_id} 分析超时")
            except ModelInvocationError as exc:
                if exc.code == "MODEL_RATE_LIMIT":
                    wait = _RATE_LIMIT_BACKOFF * (attempt + 1)
                    await self.emit_log(
                        task_id, "info",
                        f"视频 {note_id} 触发限流，{wait:.0f}s 后重试(attempt={attempt})"
                    )
                    await asyncio.sleep(wait)
                elif attempt == 1:
                    await self.emit_log(
                        task_id, "warn", f"视频 {note_id} 模型降级:{exc.code}"
                    )
            except Exception as exc:  # noqa: BLE001
                if attempt == 1:
                    await self.emit_log(
                        task_id, "warn", f"视频 {note_id} 异常:{type(exc).__name__}:{exc}"
                    )
        return (note_id, None)

    @staticmethod
    def _annotation_field_str(value: Any) -> str:
        """与 ImageAnalysisAgent 一致:非字符串 JSON 值先规范化再校验。"""
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float, bool)):
            return str(value).strip()
        return str(value).strip()

    @staticmethod
    def _is_valid_annotation(parsed: Dict[str, Any]) -> bool:
        """6 个核心字段必填;E/F 禁止 _NONE/空串(允许枚举外自拟或 _OTHER)。"""
        core = [
            "cover_type",
            "cover_text_type",
            "title_type",
            "opening_type",
            "product_intro_type",
            "product_placement_type",
        ]
        for k in core:
            if not VideoAnalysisAgent._annotation_field_str(parsed.get(k)):
                return False
        for k in ("product_intro_type", "product_placement_type"):
            u = VideoAnalysisAgent._annotation_field_str(parsed.get(k)).upper()
            if not u or u == "_NONE":
                return False
        return True

    @staticmethod
    def _coerce_annotation(parsed: Dict[str, Any]) -> Dict[str, Any]:
        """把 LLM 输出强制归一化到 8 字段(缺失填空串,多余丢弃)。"""
        out: Dict[str, Any] = {}
        for k in _ANNOTATION_KEYS:
            v = parsed.get(k) or ""
            out[k] = str(v).strip() if isinstance(v, str) else str(v)
        return out

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    async def _read_enabled_flag(self, task_id: str) -> bool:
        """读 system_settings.video_analysis_enabled;异常时默认启用。"""
        try:
            from ...services.system_settings_store import get_system_settings_store

            cfg = await get_system_settings_store().get()
            return bool(cfg.get("video_analysis_enabled", True))
        except Exception as exc:  # noqa: BLE001
            await self.emit_log(
                task_id, "warn", f"读 video_analysis_enabled 失败({exc}),按启用处理"
            )
            return True

    async def _build_taxonomy_hint(self) -> str:
        """从 ViralTaxonomyLoader 构造"候选枚举表"文本,附到 system_prompt 后。"""
        try:
            from ...services.viral_taxonomy_loader import get_viral_taxonomy_loader

            data = await get_viral_taxonomy_loader().get()
            taxonomy = data.get("taxonomy", {})
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[VideoAgent] 读 taxonomy 失败: {exc},用空 hint")
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
                joined = " / ".join(values)
                lines.append(f"- **{field_name}**: {joined}")
        return "\n".join(lines)

    async def _feed_pending_types(
        self, annotations: Dict[str, Dict[str, Any]]
    ) -> None:
        """LLM 产出的 _OTHER 或不在 taxonomy 里的值,调用 propose_new_type 直接并入 taxonomy。

        不阻塞主流程,失败也 ok。
        """
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
                if not val or val == "_OTHER":
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
        """空/禁用场景也要写 multimodal_output.annotations(保留已有 image annotations)。

        不能用空 dict 覆盖,否则会清掉并行写入的 ImageAgent annotations。
        """
        existing_mm = context.task_context.get("multimodal_output") or {}
        existing_anns = dict(existing_mm.get("annotations") or {})
        TaskContextWriter(context.task_context).write(
            "multimodal_output",
            {
                "annotations": existing_anns,
                "video_stats": {
                    "total": total,
                    "success": 0,
                    "failed": 0,
                    "skipped_reason": reason,
                    "source_agent": "VideoAnalysisAgent",
                },
            },
            agent_id=self.agent_id,
            merge=True,
            note=f"video_empty_{reason}",
        )
