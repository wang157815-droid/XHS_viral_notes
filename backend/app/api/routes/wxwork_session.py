"""
企业微信会话内容存档 API

GET  /wxwork/session/messages  — 实时拉取并解密最新会话记录
POST /wxwork/session/sync      — 增量同步（从上次断点继续，存入 JSONL）
GET  /wxwork/session/logs      — 查看已同步的本地记录
GET  /wxwork/session/seq       — 查看当前同步进度（last_seq）

所有接口均需要 ?secret=WXWORK_ADMIN_SECRET 鉴权。
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from loguru import logger

from ...core.config import settings
from ...services.wxwork_session_archive import FinanceSDKError, fetch_and_decrypt

router = APIRouter(prefix="/wxwork/session", tags=["wxwork-session"])

# 本地存档目录：datas/wxwork_session/
_STORE_DIR = Path(__file__).resolve().parents[5] / "datas" / "wxwork_session"
_STORE_DIR.mkdir(parents=True, exist_ok=True)
_SEQ_FILE = _STORE_DIR / "last_seq.txt"


# ------------------------------------------------------------------ #
# 工具
# ------------------------------------------------------------------ #

def _load_last_seq() -> int:
    if _SEQ_FILE.exists():
        try:
            return int(_SEQ_FILE.read_text(encoding="utf-8").strip())
        except Exception:
            pass
    return 0


def _save_last_seq(seq: int) -> None:
    _SEQ_FILE.write_text(str(seq), encoding="utf-8")


def _check_secret(secret: str) -> None:
    if secret != settings.wxwork_admin_secret:
        raise HTTPException(status_code=403, detail="secret 错误")


# ------------------------------------------------------------------ #
# GET /wxwork/session/seq — 查看同步进度
# ------------------------------------------------------------------ #

@router.get("/seq")
async def get_last_seq(secret: str = Query(...)):
    """查看当前已同步到的最大 seq 值"""
    _check_secret(secret)
    return {"last_seq": _load_last_seq()}


# ------------------------------------------------------------------ #
# GET /wxwork/session/messages — 实时拉取解密
# ------------------------------------------------------------------ #

@router.get("/messages")
async def get_session_messages(
    secret: str = Query(...),
    seq: int = Query(0, ge=0, description="起始 seq（不含），0 表示从最新拉取"),
    limit: int = Query(100, ge=1, le=1000, description="最多拉取条数"),
):
    """
    实时从企微拉取指定 seq 之后的会话记录并解密返回。
    不写入本地文件，适合查询测试。
    """
    _check_secret(secret)
    try:
        messages, max_seq = fetch_and_decrypt(seq=seq, limit=limit)
    except FinanceSDKError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "count": len(messages),
        "max_seq": max_seq,
        "messages": messages,
    }


# ------------------------------------------------------------------ #
# POST /wxwork/session/sync — 增量同步存档
# ------------------------------------------------------------------ #

async def _do_sync(limit: int) -> dict:
    """实际同步逻辑，可被后台任务调用"""
    last_seq = _load_last_seq()

    try:
        messages, max_seq = fetch_and_decrypt(seq=last_seq, limit=limit)
    except (FinanceSDKError, ValueError) as exc:
        logger.error("[wxwork-session] 同步失败: {}", exc)
        raise

    if not messages:
        return {"synced": 0, "last_seq": last_seq}

    # 按天写入 JSONL
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = _STORE_DIR / f"{today}.jsonl"
    with log_file.open("a", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    _save_last_seq(max_seq)
    logger.info("[wxwork-session] 同步完成：{} 条，last_seq={}", len(messages), max_seq)
    return {"synced": len(messages), "last_seq": max_seq}


@router.post("/sync")
async def sync_session(
    secret: str = Query(...),
    limit: int = Query(100, ge=1, le=1000),
    background: bool = Query(False, description="True 则立即返回，后台执行"),
    background_tasks: BackgroundTasks = None,
):
    """
    从上次断点继续增量同步，将解密后的消息存入
    datas/wxwork_session/YYYY-MM-DD.jsonl。

    background=true 时立即返回 queued，适合定时任务触发。
    """
    _check_secret(secret)

    if background and background_tasks is not None:
        background_tasks.add_task(_do_sync, limit)
        return {"queued": True, "last_seq": _load_last_seq()}

    try:
        result = await _do_sync(limit)
    except (FinanceSDKError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return result


# ------------------------------------------------------------------ #
# GET /wxwork/session/logs — 查看本地存档
# ------------------------------------------------------------------ #

@router.get("/logs")
async def get_session_logs(
    secret: str = Query(...),
    date: str = Query(None, description="日期 YYYY-MM-DD，不填则今天"),
    user: str = Query(None, description="按 from 或 tolist 过滤用户 ID"),
    msgtype: str = Query(None, description="按消息类型过滤，如 text/image/file"),
    limit: int = Query(200, ge=1, le=2000),
):
    """
    查看已同步到本地的会话存档记录。
    支持按日期、用户、消息类型过滤。
    """
    _check_secret(secret)

    target_date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = _STORE_DIR / f"{target_date}.jsonl"

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
            if user and user != rec.get("from") and user not in rec.get("tolist", []):
                continue
            if msgtype and rec.get("msgtype") != msgtype:
                continue
            records.append(rec)
    except Exception as exc:
        logger.error("[wxwork-session] 读取存档失败: {}", exc)
        raise HTTPException(status_code=500, detail="读取存档文件失败")

    records.reverse()
    records = records[:limit]

    return {
        "date": target_date,
        "total": len(records),
        "records": records,
    }
