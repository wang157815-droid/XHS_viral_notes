"""
企业微信消息回调路由

流程：
  GET /wxwork/callback  —— 企微验证回调 URL（首次配置时）
  POST /wxwork/callback —— 接收用户消息 → 后台调 Minimax → 主动发回复
  GET /wxwork/messages  —— 查看历史消息记录（需要 ?secret=WXWORK_ADMIN_SECRET）

采用「立即返回 success + 后台异步处理」方案，规避企微 5 秒超时限制。
消息记录独立存储在 datas/wxwork_logs/YYYY-MM-DD.jsonl，与 RedMuse 会话系统无关。
"""
import asyncio
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from loguru import logger
from pydantic import BaseModel

from ...core.config import settings
from ...services.wxwork_crypto import WXBizMsgCrypt

router = APIRouter(prefix="/wxwork", tags=["wxwork-callback"])

# 日志目录：项目根/datas/wxwork_logs/
_LOG_DIR = Path(__file__).resolve().parents[5] / "datas" / "wxwork_logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

# 每个用户的对话历史存储目录
_HISTORY_DIR = _LOG_DIR / "history"
_HISTORY_DIR.mkdir(parents=True, exist_ok=True)

# 内存中的对话历史缓存：{user_id: [{"role": ..., "content": ...}, ...]}
# 仅缓存最近若干轮，重启后从文件恢复
_history_cache: dict[str, list] = {}

# ------------------------------------------------------------------ #
# 工具：加解密实例（单例）
# ------------------------------------------------------------------ #

@lru_cache(maxsize=1)
def _get_crypto() -> WXBizMsgCrypt:
    return WXBizMsgCrypt(
        token=settings.wxwork_token,
        encoding_aes_key=settings.wxwork_encoding_aes_key,
        corp_id=settings.wxwork_corp_id,
    )


# ------------------------------------------------------------------ #
# 工具：获取企微 access_token（带内存缓存，自动刷新）
# ------------------------------------------------------------------ #

_token_cache: dict = {"token": "", "expires_at": 0.0}


async def _get_access_token() -> str:
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 120:
        return _token_cache["token"]

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
            params={
                "corpid": settings.wxwork_corp_id,
                "corpsecret": settings.wxwork_corp_secret,
            },
        )
        data = resp.json()

    if data.get("errcode", 0) != 0:
        raise RuntimeError(f"获取企微 access_token 失败: {data}")

    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = now + data.get("expires_in", 7200)
    logger.info("[wxwork] access_token 已刷新，有效期 {}s", data.get("expires_in"))
    return _token_cache["token"]


# ------------------------------------------------------------------ #
# 工具：用户对话历史管理（按用户持久化存储）
# ------------------------------------------------------------------ #

def _history_file(user_id: str) -> Path:
    safe = "".join(c for c in user_id if c.isalnum() or c in {"_", "-"})
    return _HISTORY_DIR / f"{safe}.json"


def _load_history(user_id: str) -> list:
    """从缓存或文件加载用户历史，返回消息列表"""
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
    """持久化用户历史到文件"""
    _history_cache[user_id] = messages
    try:
        _history_file(user_id).write_text(
            json.dumps({"user_id": user_id, "messages": messages}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.error("[wxwork] 保存用户历史失败 user={}: {}", user_id, exc)


def _update_history(user_id: str, user_input: str, reply: str) -> None:
    """追加最新一轮对话，超出最大轮数时裁剪最早的"""
    max_turns = settings.wxwork_max_history_turns  # 每轮 = 1 user + 1 assistant
    messages = _load_history(user_id)
    messages.append({"role": "user", "content": user_input})
    messages.append({"role": "assistant", "content": reply})
    # 保留最近 max_turns 轮（每轮2条），system prompt 不计入
    if len(messages) > max_turns * 2:
        messages = messages[-(max_turns * 2):]
    _save_history(user_id, messages)


# ------------------------------------------------------------------ #
# 工具：调用 Minimax Chat API（带用户对话历史）
# ------------------------------------------------------------------ #

async def _call_minimax(user_id: str, user_message: str) -> str:
    """调用 Minimax，将该用户的历史消息一并传入以保持对话上下文"""
    history = _load_history(user_id)

    messages = [{"role": "system", "content": settings.minimax_system_prompt}]
    messages.extend(history)                                    # 历史上下文
    messages.append({"role": "user", "content": user_message}) # 本次问题

    payload = {
        "model": settings.minimax_model,
        "messages": messages,
        "max_tokens": settings.minimax_max_tokens,
        "temperature": 0.7,
    }
    headers = {
        "Authorization": f"Bearer {settings.minimax_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{settings.minimax_api_base}/chat/completions",
            headers=headers,
            json=payload,
        )
    resp.raise_for_status()
    data = resp.json()
    choices = data.get("choices") or data.get("reply") or []
    if isinstance(choices, list) and choices:
        return choices[0].get("message", {}).get("content", "") or choices[0].get("text", "")
    return "（暂时无法回复，请稍后再试）"


# ------------------------------------------------------------------ #
# 工具：通过企微主动发消息给用户
# ------------------------------------------------------------------ #

async def _send_wxwork_message(to_user: str, content: str) -> None:
    """调用企微「发送应用消息」接口，主动推送回复"""
    try:
        token = await _get_access_token()
        payload = {
            "touser": to_user,
            "msgtype": "text",
            "agentid": settings.wxwork_agent_id,
            "text": {"content": content},
            "safe": 0,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}",
                json=payload,
            )
        result = resp.json()
        if result.get("errcode", 0) != 0:
            logger.error("[wxwork] 发送消息失败: to={} result={}", to_user, result)
        else:
            logger.info("[wxwork] 消息已发送: to={}", to_user)
    except Exception as exc:
        logger.error("[wxwork] 发送消息异常: {}", exc)


# ------------------------------------------------------------------ #
# 消息日志：独立写入 JSONL 文件
# ------------------------------------------------------------------ #

def _append_log(from_user: str, user_input: str, reply: str, success: bool) -> None:
    """按天写入 JSONL，每行一条对话记录"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = _LOG_DIR / f"{today}.jsonl"
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "from_user": from_user,
        "user_input": user_input,
        "reply": reply,
        "model": settings.minimax_model,
        "success": success,
    }
    try:
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.error("[wxwork] 写入消息日志失败: {}", exc)


# ------------------------------------------------------------------ #
# 工具：去除模型思考内容（<think>…</think> 等标签）
# ------------------------------------------------------------------ #

_THINK_PATTERN = re.compile(
    r"<think(?:ing)?>\s*.*?\s*</think(?:ing)?>",
    re.DOTALL | re.IGNORECASE,
)


def _strip_thinking(text: str) -> str:
    """移除模型输出中的思考标签及其内容，返回干净的回复文本"""
    cleaned = _THINK_PATTERN.sub("", text)
    return cleaned.strip()


# ------------------------------------------------------------------ #
# 意图检测：识别用户是否需要「定时提醒」或「转发消息」
# ------------------------------------------------------------------ #

_INTENT_SYSTEM = """\
你是企业微信机器人的意图提取助手。根据用户最新的一句话，判断是否需要执行以下动作之一：

1. remind        — 在指定时间后给用户发提醒（如"30分钟后提醒我开会"）
2. forward       — 把消息转发给另一个成员（如"帮我告诉张三明天9点开会"）
3. broadcast     — 给所有人广播通知（如"广播通知：明天下午3点全体会议"）
4. push_msg      — 推送指定内容给指定用户（如"给李四发：报告有问题"）
5. session_sync  — 触发会话记录增量同步（如"同步一下会话记录"/"更新会话存档"）
6. session_query — 查询会话存档（如"查看今天的会话"/"张三最近说了什么"）
7. clear_history — 清空自己的对话历史（如"忘掉之前的内容"/"重置对话"/"清空历史"）
8. none          — 普通问答，不需要额外动作

只返回合法 JSON，不要加任何说明：
- {"action":"remind","delay_minutes":30,"message":"提醒你：开会时间到了"}
- {"action":"forward","to_user":"ZhangSan","message":"明天9点开会，请准时参加"}
- {"action":"broadcast","message":"明天下午3点全体会议，请准时参加"}
- {"action":"push_msg","to_user":"LiSi","message":"你的报告有问题，请重新提交"}
- {"action":"session_sync"}
- {"action":"session_query","user":"ZhangSan","date":"today"}
- {"action":"clear_history"}
- {"action":"none"}

规则：
- delay_minutes 必须是正整数，最小1，最大1440（24小时）
- to_user 填企业微信的英文账号/userid，若用户只提了中文名则填该中文名
- date 填 today / yesterday 或 YYYY-MM-DD，不确定时填 today
- 若用户表达不明确，返回 {"action":"none"}
"""


async def _extract_intent(user_input: str) -> dict:
    """用轻量 LLM 调用识别用户意图，超时或解析失败时返回 none"""
    try:
        headers = {
            "Authorization": f"Bearer {settings.minimax_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.minimax_model,
            "messages": [
                {"role": "system", "content": _INTENT_SYSTEM},
                {"role": "user", "content": user_input},
            ],
            "max_tokens": 128,
            "temperature": 0.0,  # 确定性输出
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{settings.minimax_api_base}/chat/completions",
                headers=headers,
                json=payload,
            )
        resp.raise_for_status()
        raw = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        raw = _strip_thinking(raw)
        # 容错：去掉可能包裹的 markdown 代码块
        raw = re.sub(r"^```[a-z]*\n?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        intent = json.loads(raw)
        _VALID_ACTIONS = {
            "remind", "forward", "broadcast", "push_msg",
            "session_sync", "session_query", "clear_history", "none",
        }
        if intent.get("action") in _VALID_ACTIONS:
            return intent
    except Exception as exc:
        logger.debug("[wxwork] 意图提取失败（忽略）: {}", exc)
    return {"action": "none"}


# 会话存档本地目录（与 wxwork_session.py 保持一致）
_SESSION_STORE_DIR = Path(__file__).resolve().parents[5] / "datas" / "wxwork_session"
_SESSION_SEQ_FILE = _SESSION_STORE_DIR / "last_seq.txt"


async def _query_session_for_bot(user: str, date_str: str, limit: int = 15) -> str:
    """
    为机器人意图查询本地会话存档，返回可读的纯文本摘要。
    date_str 支持 today / yesterday / YYYY-MM-DD。
    """
    from datetime import timedelta

    if date_str in ("today", ""):
        target_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    elif date_str == "yesterday":
        target_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        target_date = date_str

    log_file = _SESSION_STORE_DIR / f"{target_date}.jsonl"
    if not log_file.exists():
        return f"{target_date} 暂无本地会话存档，可发送「同步会话记录」先拉取数据。"

    records = []
    for line in log_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if user:
            if user != rec.get("from") and user not in rec.get("tolist", []):
                continue
        records.append(rec)

    if not records:
        suffix = f"（关键词：{user}）" if user else ""
        return f"{target_date} 无匹配会话记录{suffix}。"

    total = len(records)
    records = records[-limit:]
    lines = [f"[会话存档] {target_date}，最近 {len(records)}/{total} 条："]
    for rec in records:
        from_who = rec.get("from", "?")
        to_list = "、".join(rec.get("tolist", []))
        msgtype = rec.get("msgtype", "?")
        if msgtype == "text":
            text = rec.get("text", {}).get("content", "")[:50]
        elif msgtype == "image":
            text = "[图片]"
        elif msgtype == "file":
            text = f"[文件: {rec.get('file', {}).get('filename', '?')}]"
        elif msgtype == "voice":
            text = "[语音消息]"
        else:
            text = f"[{msgtype}]"
        lines.append(f"  {from_who} → {to_list}: {text}")

    return "\n".join(lines)


async def _delayed_send(to_user: str, message: str, delay_seconds: int) -> None:
    """等待指定秒数后发送消息（用于定时提醒）"""
    await asyncio.sleep(delay_seconds)
    await _send_wxwork_message(to_user, message)
    logger.info("[wxwork] 定时提醒已发送: to={} delay={}s", to_user, delay_seconds)


async def _execute_intent(intent: dict, from_user: str) -> str | None:
    """
    执行意图动作，返回要追加到回复末尾的确认文案。
    返回 None 表示无动作。
    """
    action = intent.get("action", "none")

    if action == "remind":
        delay_min = max(1, min(int(intent.get("delay_minutes", 1)), 1440))
        message = intent.get("message", "你的提醒时间到了！")
        delay_sec = delay_min * 60
        # 不阻塞当前协程，后台独立运行
        asyncio.get_event_loop().create_task(
            _delayed_send(from_user, f"[定时提醒] {message}", delay_sec)
        )
        logger.info("[wxwork] 定时提醒已注册: to={} delay={}min", from_user, delay_min)
        return f"\n\n已设置提醒，{delay_min} 分钟后我会再通知你。"

    if action == "forward":
        to_user = intent.get("to_user", "").strip()
        message = intent.get("message", "").strip()
        if to_user and message:
            await _send_wxwork_message(to_user, f"[来自机器人转发] {message}")
            logger.info("[wxwork] 消息已转发: to={}", to_user)
            return f"\n\n已转发给 {to_user}。"

    if action == "broadcast":
        message = intent.get("message", "").strip()
        if message:
            await _send_wxwork_message("@all", f"[全员通知] {message}")
            logger.info("[wxwork] 已广播全员通知")
            return "\n\n已向全体成员广播通知。"

    if action == "push_msg":
        to_user = intent.get("to_user", "").strip()
        message = intent.get("message", "").strip()
        if to_user and message:
            await _send_wxwork_message(to_user, message)
            logger.info("[wxwork] 意图推送: to={}", to_user)
            return f"\n\n消息已发送给 {to_user}。"

    if action == "session_sync":
        try:
            from .wxwork_session import _do_sync
            result = await _do_sync(limit=100)
            synced = result.get("synced", 0)
            last_seq = result.get("last_seq", 0)
            if synced:
                return f"\n\n会话同步完成：新增 {synced} 条，当前进度 seq={last_seq}。"
            return f"\n\n无新会话记录，当前进度 seq={last_seq}。"
        except Exception as exc:
            logger.error("[wxwork] 意图-会话同步失败: {}", exc)
            return "\n\n会话同步失败，请确认 Finance SDK 已配置正确。"

    if action == "session_query":
        try:
            summary = await _query_session_for_bot(
                user=intent.get("user", ""),
                date_str=intent.get("date", "today"),
            )
            return f"\n\n{summary}"
        except Exception as exc:
            logger.error("[wxwork] 意图-会话查询失败: {}", exc)
            return "\n\n会话记录查询失败。"

    if action == "clear_history":
        _save_history(from_user, [])
        logger.info("[wxwork] 意图-清空历史: user={}", from_user)
        return "\n\n已清空你的对话历史，我们重新开始。"

    return None


# ------------------------------------------------------------------ #
# 后台任务：Minimax 推理 + 意图检测 + 企微回复 + 记录日志
# ------------------------------------------------------------------ #

async def _process_and_reply(from_user: str, user_input: str) -> None:
    logger.info("[wxwork] 收到消息 from={} content={!r}", from_user, user_input[:50])
    success = True
    try:
        # 主回复 + 意图检测并发执行，减少总耗时
        raw_reply, intent = await asyncio.gather(
            _call_minimax(from_user, user_input),
            _extract_intent(user_input),
        )
        reply = _strip_thinking(raw_reply)
        if not reply:
            reply = "（暂时无法回复，请稍后再试）"

        # 执行意图动作，将确认文案追加到回复末尾
        action_note = await _execute_intent(intent, from_user)
        if action_note:
            reply += action_note

        _update_history(from_user, user_input, reply)
    except Exception as exc:
        logger.error("[wxwork] Minimax 调用失败: {}", exc)
        reply = "抱歉，AI 服务暂时不可用，请稍后再试。"
        success = False
    _append_log(from_user, user_input, reply, success)
    await _send_wxwork_message(from_user, reply)


# ------------------------------------------------------------------ #
# GET /wxwork/callback —— 企微 URL 验证
# ------------------------------------------------------------------ #

@router.get("/callback", response_class=PlainTextResponse)
async def wxwork_verify(
    msg_signature: str = Query(..., description="企微签名"),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(..., description="待解密的随机串"),
):
    """
    企微配置回调 URL 时会先发一个 GET 请求验证。
    验证通过后解密 echostr 并原文返回。
    """
    crypto = _get_crypto()
    if not crypto.verify_signature(msg_signature, timestamp, nonce, echostr):
        logger.warning("[wxwork] URL 验证签名失败")
        return PlainTextResponse("signature error", status_code=403)

    try:
        plain = crypto.decrypt(echostr)
    except Exception as exc:
        logger.error("[wxwork] echostr 解密失败: {}", exc)
        return PlainTextResponse("decrypt error", status_code=500)

    logger.info("[wxwork] URL 验证成功")
    return PlainTextResponse(plain)


# ------------------------------------------------------------------ #
# POST /wxwork/callback —— 接收企微消息
# ------------------------------------------------------------------ #

@router.post("/callback")
async def wxwork_receive(
    request: Request,
    background_tasks: BackgroundTasks,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
):
    """
    企微推送用户消息时调用此接口。
    立即返回 "success"，后台异步调用 Minimax 并主动发送回复，
    规避企微 5 秒超时导致重试。
    """
    body = await request.body()
    if not body:
        return PlainTextResponse("success")

    # 解析外层加密 XML
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        logger.error("[wxwork] XML 解析失败: {}", exc)
        return PlainTextResponse("success")

    encrypt_msg = (root.findtext("Encrypt") or "").strip()
    if not encrypt_msg:
        return PlainTextResponse("success")

    # 验签
    crypto = _get_crypto()
    if not crypto.verify_signature(msg_signature, timestamp, nonce, encrypt_msg):
        logger.warning("[wxwork] 消息签名校验失败，忽略")
        return PlainTextResponse("success")

    # 解密
    try:
        xml_msg = crypto.decrypt(encrypt_msg)
    except Exception as exc:
        logger.error("[wxwork] 消息解密失败: {}", exc)
        return PlainTextResponse("success")

    # 解析消息体
    try:
        msg_root = ET.fromstring(xml_msg)
    except ET.ParseError as exc:
        logger.error("[wxwork] 解密后 XML 解析失败: {}", exc)
        return PlainTextResponse("success")

    msg_type = (msg_root.findtext("MsgType") or "").strip()
    from_user = (msg_root.findtext("FromUserName") or "").strip()

    # 只处理文本消息，其他类型静默忽略
    if msg_type != "text":
        logger.debug("[wxwork] 忽略非文本消息 type={}", msg_type)
        return PlainTextResponse("success")

    user_input = (msg_root.findtext("Content") or "").strip()
    if not user_input or not from_user:
        return PlainTextResponse("success")

    # 后台异步处理，立即返回避免超时
    background_tasks.add_task(_process_and_reply, from_user, user_input)
    return PlainTextResponse("success")


# ------------------------------------------------------------------ #
# GET /wxwork/messages —— 查看历史消息记录
# ------------------------------------------------------------------ #

@router.get("/messages")
async def wxwork_messages(
    secret: str = Query(..., description="管理员密钥，即 .env 中的 WXWORK_ADMIN_SECRET"),
    date: str = Query(None, description="查询日期，格式 YYYY-MM-DD，不填则查今天"),
    user: str = Query(None, description="按企微用户ID过滤，不填则返回所有"),
    limit: int = Query(100, ge=1, le=500, description="最多返回条数"),
):
    """
    查看企微用户与 Minimax 的对话记录。
    访问示例：GET /api/v1/wxwork/messages?secret=你的密钥&date=2026-06-03
    """
    if secret != settings.wxwork_admin_secret:
        raise HTTPException(status_code=403, detail="secret 错误")

    target_date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = _LOG_DIR / f"{target_date}.jsonl"

    if not log_file.exists():
        return {"date": target_date, "total": 0, "records": []}

    records = []
    try:
        for line in log_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if user and rec.get("from_user") != user:
                continue
            records.append(rec)
    except Exception as exc:
        logger.error("[wxwork] 读取消息日志失败: {}", exc)
        raise HTTPException(status_code=500, detail="读取日志文件失败")

    # 最新的在前
    records.reverse()
    records = records[:limit]

    return {
        "date": target_date,
        "total": len(records),
        "log_file": str(log_file),
        "records": records,
    }


@router.get("/messages/dates")
async def wxwork_message_dates(
    secret: str = Query(..., description="管理员密钥"),
):
    """列出所有有记录的日期"""
    if secret != settings.wxwork_admin_secret:
        raise HTTPException(status_code=403, detail="secret 错误")

    dates = sorted(
        [p.stem for p in _LOG_DIR.glob("*.jsonl")],
        reverse=True,
    )
    return {"dates": dates}


@router.get("/history/{user_id}")
async def wxwork_user_history(
    user_id: str,
    secret: str = Query(..., description="管理员密钥"),
):
    """查看某个用户当前的完整对话历史（Minimax 能看到的上下文）"""
    if secret != settings.wxwork_admin_secret:
        raise HTTPException(status_code=403, detail="secret 错误")

    messages = _load_history(user_id)
    return {
        "user_id": user_id,
        "turn_count": len(messages) // 2,
        "max_turns": settings.wxwork_max_history_turns,
        "messages": messages,
    }


@router.delete("/history/{user_id}")
async def wxwork_clear_history(
    user_id: str,
    secret: str = Query(..., description="管理员密钥"),
):
    """清空某个用户的对话历史（让 Minimax 忘掉之前的内容，重新开始）"""
    if secret != settings.wxwork_admin_secret:
        raise HTTPException(status_code=403, detail="secret 错误")

    _save_history(user_id, [])
    logger.info("[wxwork] 已清空用户 {} 的对话历史", user_id)
    return {"user_id": user_id, "cleared": True}


# ------------------------------------------------------------------ #
# 主动推送接口（手动触发 / 定时任务 / 前端调用 均走这里）
# ------------------------------------------------------------------ #

class PushRequest(BaseModel):
    to_users: list[str]   # 目标用户ID列表，传 ["@all"] 表示全员广播
    content: str = ""     # 直接发送的文本（use_ai=False 时必填）
    use_ai: bool = False  # True：Minimax 生成内容再发；False：直接发 content
    ai_prompt: str = ""   # use_ai=True 时给 Minimax 的指令


@router.post("/push")
async def wxwork_push(
    body: PushRequest,
    background_tasks: BackgroundTasks,
    secret: str = Query(..., description="管理员密钥"),
):
    """
    主动给企微用户发消息，支持三种用法：

    1. 直接发固定文本：
       {"to_users": ["zhangsan"], "content": "今日早报：..."}

    2. AI 生成内容后发送（每个用户带各自的历史上下文）：
       {"to_users": ["zhangsan", "lisi"], "use_ai": true,
        "ai_prompt": "根据用户最近的问题，给他推送一条今日小红书运营技巧"}

    3. 广播给所有人（企微限制：@all 仅限应用可见范围内的成员）：
       {"to_users": ["@all"], "content": "系统公告：..."}
    """
    if secret != settings.wxwork_admin_secret:
        raise HTTPException(status_code=403, detail="secret 错误")
    if not body.to_users:
        raise HTTPException(status_code=400, detail="to_users 不能为空")
    if not body.use_ai and not body.content.strip():
        raise HTTPException(status_code=400, detail="content 与 use_ai 至少指定一个")

    background_tasks.add_task(_do_push, body.to_users, body.content, body.use_ai, body.ai_prompt)
    return {"queued": True, "to_users": body.to_users, "use_ai": body.use_ai}


async def _do_push(to_users: list[str], content: str, use_ai: bool, ai_prompt: str) -> None:
    """后台执行推送，对每个用户独立生成内容（use_ai=True 时携带各自历史）"""
    for user_id in to_users:
        try:
            if use_ai:
                # 以 ai_prompt 为问题，携带该用户历史，让 Minimax 生成个性化内容
                msg = await _call_minimax(user_id, ai_prompt)
                _update_history(user_id, f"[系统推送指令] {ai_prompt}", msg)
            else:
                msg = content
            await _send_wxwork_message(user_id, msg)
            _append_log(user_id, f"[主动推送] {ai_prompt or content[:30]}", msg, True)
        except Exception as exc:
            logger.error("[wxwork] 推送失败 to={}: {}", user_id, exc)
