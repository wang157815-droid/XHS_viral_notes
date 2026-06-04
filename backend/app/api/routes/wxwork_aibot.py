"""
企业微信「智能机器人」消息回调路由

对应文档：https://developer.work.weixin.qq.com/document/path/100719
适用场景：数据与智能专区 → 智能机器人 → 接收消息

与「自建应用」回调的关键区别：
  - 解密后内容是 JSON（不是 XML）
  - 直接 POST 到消息里的 response_url 回复（不需要调 message/send API）
  - 支持外部联系人消息
  - 超时时间 6 分钟（支持流式消息）

流程：
  GET /wxwork/ai-callback  —— 企微验证回调 URL
  POST /wxwork/ai-callback —— 接收用户消息 → 调 Minimax → POST response_url 回复
"""
import json
import re
import time
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

import httpx
from fastapi import APIRouter, BackgroundTasks, Query, Request
from fastapi.responses import PlainTextResponse
from loguru import logger

from ...core.config import settings
from ...services.wxwork_crypto import WXBizMsgCrypt

router = APIRouter(prefix="/wxwork", tags=["wxwork-aibot"])

_LOG_DIR = Path(__file__).resolve().parents[5] / "datas" / "wxwork_logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_HISTORY_DIR = _LOG_DIR / "history"
_HISTORY_DIR.mkdir(parents=True, exist_ok=True)

# 已处理消息 ID 去重集合（防止企微重复回调，重启后清空，可接受）
_processed_msgids: set[str] = set()
_MAX_MSGID_CACHE = 10000

_history_cache: dict[str, list] = {}


# ------------------------------------------------------------------ #
# 加解密（和自建应用共用同一套 AESKey/Token）
# ------------------------------------------------------------------ #

@lru_cache(maxsize=1)
def _get_crypto() -> WXBizMsgCrypt:
    return WXBizMsgCrypt(
        token=settings.wxwork_token,
        encoding_aes_key=settings.wxwork_encoding_aes_key,
        corp_id=settings.wxwork_corp_id,
    )


# ------------------------------------------------------------------ #
# 对话历史（与自建应用共用同一套文件，按 user_id 隔离）
# ------------------------------------------------------------------ #

def _history_file(user_id: str) -> Path:
    safe = "".join(c for c in user_id if c.isalnum() or c in {"_", "-"})
    return _HISTORY_DIR / f"{safe}.json"


def _load_history(user_id: str) -> list:
    if user_id in _history_cache:
        return _history_cache[user_id]
    path = _history_file(user_id)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            _history_cache[user_id] = data.get("messages", [])
            return _history_cache[user_id]
        except Exception:
            pass
    _history_cache[user_id] = []
    return _history_cache[user_id]


def _save_history(user_id: str, messages: list) -> None:
    _history_cache[user_id] = messages
    try:
        _history_file(user_id).write_text(
            json.dumps({"user_id": user_id, "messages": messages}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.error("[wxwork-aibot] 保存历史失败 user={}: {}", user_id, exc)


def _update_history(user_id: str, user_input: str, reply: str) -> None:
    max_turns = settings.wxwork_max_history_turns
    messages = _load_history(user_id)
    messages.append({"role": "user", "content": user_input})
    messages.append({"role": "assistant", "content": reply})
    if len(messages) > max_turns * 2:
        messages = messages[-(max_turns * 2):]
    _save_history(user_id, messages)


# ------------------------------------------------------------------ #
# 日志
# ------------------------------------------------------------------ #

def _append_log(from_user: str, user_input: str, reply: str, success: bool) -> None:
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = _LOG_DIR / f"aibot-{today}.jsonl"
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "from_user": from_user,
        "user_input": user_input,
        "reply": reply,
        "model": settings.minimax_model,
        "success": success,
        "source": "aibot",
    }
    try:
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.error("[wxwork-aibot] 写日志失败: {}", exc)


# ------------------------------------------------------------------ #
# 调用 Minimax
# ------------------------------------------------------------------ #

async def _call_minimax(user_id: str, user_message: str) -> str:
    history = _load_history(user_id)
    messages = [{"role": "system", "content": settings.minimax_system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{settings.minimax_api_base}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.minimax_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.minimax_model,
                "messages": messages,
                "max_tokens": settings.minimax_max_tokens,
                "temperature": 0.7,
            },
        )
    resp.raise_for_status()
    data = resp.json()
    choices = data.get("choices") or []
    if choices:
        return choices[0].get("message", {}).get("content", "")
    return "（暂时无法回复，请稍后再试）"


# ------------------------------------------------------------------ #
# 回复：直接 POST 到 response_url（智能机器人专属方式）
# ------------------------------------------------------------------ #

async def _reply_via_response_url(response_url: str, content: str) -> None:
    """
    智能机器人回复消息的唯一方式：POST 到消息里附带的 response_url。
    不需要 access_token，不需要调 message/send API。
    """
    payload = {
        "msgtype": "text",
        "text": {"content": content},
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(response_url, json=payload)
        result = resp.json()
        if result.get("errcode", 0) != 0:
            logger.error("[wxwork-aibot] response_url 回复失败: {}", result)
        else:
            logger.info("[wxwork-aibot] 回复成功")
    except Exception as exc:
        logger.error("[wxwork-aibot] response_url 回复异常: {}", exc)


# ------------------------------------------------------------------ #
# 工具：去除模型思考内容（<think>…</think> 等标签）
# ------------------------------------------------------------------ #

_THINK_PATTERN = re.compile(
    r"<think(?:ing)?>\s*.*?\s*</think(?:ing)?>",
    re.DOTALL | re.IGNORECASE,
)


def _strip_thinking(text: str) -> str:
    cleaned = _THINK_PATTERN.sub("", text)
    return cleaned.strip()


# ------------------------------------------------------------------ #
# 后台任务：完整处理流程
# ------------------------------------------------------------------ #

async def _process_and_reply(from_user: str, user_input: str, response_url: str) -> None:
    logger.info("[wxwork-aibot] 收到消息 from={} content={!r}", from_user, user_input[:50])
    success = True
    try:
        raw_reply = await _call_minimax(from_user, user_input)
        reply = _strip_thinking(raw_reply)
        if not reply:
            reply = "（暂时无法回复，请稍后再试）"
        _update_history(from_user, user_input, reply)
    except Exception as exc:
        logger.error("[wxwork-aibot] Minimax 调用失败: {}", exc)
        reply = "抱歉，AI 服务暂时不可用，请稍后再试。"
        success = False
    _append_log(from_user, user_input, reply, success)
    await _reply_via_response_url(response_url, reply)


# ------------------------------------------------------------------ #
# GET /wxwork/ai-callback —— URL 验证（与自建应用相同方式）
# ------------------------------------------------------------------ #

@router.get("/ai-callback", response_class=PlainTextResponse)
async def aibot_verify(
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    """企微配置智能机器人回调 URL 时的验证请求"""
    crypto = _get_crypto()
    if not crypto.verify_signature(msg_signature, timestamp, nonce, echostr):
        logger.warning("[wxwork-aibot] URL 验证签名失败")
        return PlainTextResponse("signature error", status_code=403)
    try:
        plain = crypto.decrypt(echostr)
    except Exception as exc:
        logger.error("[wxwork-aibot] echostr 解密失败: {}", exc)
        return PlainTextResponse("decrypt error", status_code=500)
    logger.info("[wxwork-aibot] URL 验证成功")
    return PlainTextResponse(plain)


# ------------------------------------------------------------------ #
# POST /wxwork/ai-callback —— 接收智能机器人消息
# ------------------------------------------------------------------ #

@router.post("/ai-callback")
async def aibot_receive(
    request: Request,
    background_tasks: BackgroundTasks,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
):
    """
    接收企微智能机器人推送的消息。

    解密后内容是 JSON，包含 from.userid、text.content、response_url 等字段。
    立即返回空响应，后台调 Minimax 并通过 response_url 回复。
    """
    body = await request.body()
    if not body:
        return PlainTextResponse("")

    # 外层仍是加密 XML 信封
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        logger.error("[wxwork-aibot] XML 解析失败: {}", exc)
        return PlainTextResponse("")

    encrypt_msg = (root.findtext("Encrypt") or "").strip()
    if not encrypt_msg:
        return PlainTextResponse("")

    crypto = _get_crypto()
    if not crypto.verify_signature(msg_signature, timestamp, nonce, encrypt_msg):
        logger.warning("[wxwork-aibot] 签名校验失败")
        return PlainTextResponse("")

    # 解密 → 得到 JSON 字符串（不是 XML）
    try:
        json_str = crypto.decrypt(encrypt_msg)
        msg = json.loads(json_str)
    except Exception as exc:
        logger.error("[wxwork-aibot] 解密/解析 JSON 失败: {}", exc)
        return PlainTextResponse("")

    # 消息去重
    msgid = msg.get("msgid", "")
    if msgid:
        if msgid in _processed_msgids:
            logger.debug("[wxwork-aibot] 重复消息忽略 msgid={}", msgid)
            return PlainTextResponse("")
        _processed_msgids.add(msgid)
        if len(_processed_msgids) > _MAX_MSGID_CACHE:
            # 简单清理：删掉最老的一半
            to_remove = list(_processed_msgids)[:_MAX_MSGID_CACHE // 2]
            for mid in to_remove:
                _processed_msgids.discard(mid)

    msg_type = msg.get("msgtype", "")
    from_user = (msg.get("from") or {}).get("userid", "")
    response_url = msg.get("response_url", "")

    # 流式消息刷新事件（msgtype=stream），直接忽略
    if msg_type == "stream":
        return PlainTextResponse("")

    # 只处理文本消息
    if msg_type != "text":
        logger.debug("[wxwork-aibot] 忽略非文本消息 type={}", msg_type)
        return PlainTextResponse("")

    user_input = (msg.get("text") or {}).get("content", "").strip()

    # 去掉 @机器人 前缀
    if user_input.startswith("@"):
        parts = user_input.split(" ", 1)
        user_input = parts[1].strip() if len(parts) > 1 else ""

    if not user_input or not from_user or not response_url:
        return PlainTextResponse("")

    background_tasks.add_task(_process_and_reply, from_user, user_input, response_url)
    return PlainTextResponse("")
