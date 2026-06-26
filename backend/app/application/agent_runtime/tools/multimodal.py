"""多模态分析工具 analyze_image / analyze_video。

通过 ModelGateway 的 multimodal 档调用视觉模型（OpenAI 兼容 image_url/video_url）。
为薄封装：把 URL + 分析提示交给视觉模型，返回模型的结构化文字描述。
"""

from __future__ import annotations

from typing import Any, Dict

from loguru import logger

from ....llm.model_gateway import ModelInvocationError, model_gateway
from ..tool_registry import ToolContext, ToolRegistry, ToolResult, ToolSpec


_DEFAULT_IMAGE_PROMPT = (
    "请分析这张小红书笔记配图，输出：主体内容、视觉风格、文字/卖点信息、"
    "可借鉴的封面/构图技巧。用简体中文分点说明。"
)
_DEFAULT_VIDEO_PROMPT = (
    "请分析这段小红书视频，输出：核心内容、节奏与镜头、口播/字幕要点、"
    "引流钩子与可复用的脚本结构。用简体中文分点说明。"
)


async def _analyze_image(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    url = str(args.get("image_url") or "").strip()
    if not url:
        return ToolResult(ok=False, content="analyze_image 需要 image_url 参数", error="MISSING_ARGS")
    prompt = str(args.get("prompt") or "").strip() or _DEFAULT_IMAGE_PROMPT
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": url}},
            ],
        }
    ]
    try:
        result = await model_gateway.chat(
            "ImageAnalysisAgent", messages, modality="multimodal",
            task_id=ctx.task_id, overrides={"max_tokens": 800},
        )
    except ModelInvocationError as exc:
        return ToolResult(ok=False, content=f"图片分析失败（{exc.code}）：{exc}", error=exc.code)
    content = str(result.get("content") or "").strip() or "（视觉模型未返回有效内容）"
    return ToolResult(ok=True, content=content, data={"image_url": url}, display="图片分析完成")


async def _analyze_video(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    url = str(args.get("video_url") or "").strip()
    if not url:
        return ToolResult(ok=False, content="analyze_video 需要 video_url 参数", error="MISSING_ARGS")
    prompt = str(args.get("prompt") or "").strip() or _DEFAULT_VIDEO_PROMPT
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "video_url", "video_url": {"url": url}},
            ],
        }
    ]
    try:
        result = await model_gateway.chat(
            "VideoAnalysisAgent", messages, modality="multimodal",
            task_id=ctx.task_id, overrides={"max_tokens": 900},
        )
    except ModelInvocationError as exc:
        return ToolResult(ok=False, content=f"视频分析失败（{exc.code}）：{exc}", error=exc.code)
    content = str(result.get("content") or "").strip() or "（视觉模型未返回有效内容）"
    logger.info("[tool.analyze_video] url={} chars={}", url, len(content))
    return ToolResult(ok=True, content=content, data={"video_url": url}, display="视频分析完成")


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="analyze_image",
            description="用视觉模型分析一张图片（小红书封面/配图），返回视觉风格、卖点信息、可借鉴的构图技巧等。",
            parameters={
                "type": "object",
                "properties": {
                    "image_url": {"type": "string", "description": "图片可访问 URL"},
                    "prompt": {"type": "string", "description": "可选：自定义分析侧重点"},
                },
                "required": ["image_url"],
            },
            handler=_analyze_image,
            cost="expensive",
            category="capability",
        )
    )
    registry.register(
        ToolSpec(
            name="analyze_video",
            description="用视觉模型分析一段视频（小红书视频笔记），返回内容、节奏、口播要点、引流钩子与脚本结构。",
            parameters={
                "type": "object",
                "properties": {
                    "video_url": {"type": "string", "description": "视频可访问 URL"},
                    "prompt": {"type": "string", "description": "可选：自定义分析侧重点"},
                },
                "required": ["video_url"],
            },
            handler=_analyze_video,
            cost="expensive",
            category="capability",
        )
    )
