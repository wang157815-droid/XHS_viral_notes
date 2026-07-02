"""
三方 Redbook API 薄封装客户端。

支持：
  - POST /redbook/search  — 笔记搜索（page 分页）
  - GET  /redbook/comments — 评论采集（cursor 分页）

每次成功请求后，从顶层 `count` 字段读取剩余可用次数并以 INFO 级别打印日志。
配置通过 Settings.redbook_api_base / redbook_api_key 注入。
"""

from __future__ import annotations

import datetime as _dt
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from loguru import logger

from ...core.config import settings


# ── 内部常量 ──────────────────────────────────────────────────────────────────

_TIMEOUT = 30  # HTTP 请求超时（秒）
_HIGHLIGHT_RE = re.compile(r"#(.+?)\[搜索高亮\]#")


# ── 客户端 ────────────────────────────────────────────────────────────────────

class RedbookApiClient:
    """三方 Redbook API 的薄封装，所有方法均为同步，调用方需用 asyncio.to_thread 转异步。"""

    def __init__(self) -> None:
        self._base = (settings.redbook_api_base or "").rstrip("/")
        self._key = settings.redbook_api_key or ""
        # 三方 API 为国内直连地址，忽略系统/环境变量代理（HTTP_PROXY 等），
        # 避免请求被本地代理（如 Clash 127.0.0.1:7890）劫持导致超时/502
        self._session = requests.Session()
        self._session.trust_env = False

    # ── 私有工具 ──────────────────────────────────────────────────────────────

    def _headers(self) -> Dict[str, str]:
        if not self._key:
            raise RuntimeError(
                "REDBOOK_API_KEY 未配置，请在 .env 中设置 REDBOOK_API_KEY=<your-key>"
            )
        return {
            "authorization": self._key,
            "Content-Type": "application/json",
        }

    def _log_remaining(self, resp_json: Dict[str, Any]) -> None:
        remaining = resp_json.get("count")
        if remaining is not None:
            logger.info(f"[RedbookApi] 数据请求总次数剩余{remaining}次")
        else:
            logger.warning("[RedbookApi] 响应中未携带 count 字段，无法获知剩余次数")

    # ── 公开方法 ──────────────────────────────────────────────────────────────

    def search(
        self,
        keyword: str,
        page: int = 1,
        sort: str = "general",
        note_type: int = 0,
        search_request_id: str = "",
        _max_retries: int = 3,
        _retry_delay: float = 2.0,
    ) -> Dict[str, Any]:
        """调用 POST /redbook/search，返回原始响应 dict。

        Args:
            keyword:           搜索关键词
            page:              页码（从 1 开始）
            sort:              排序方式，"general"（综合）/ "time_descending"（最新）/
                                "popularity_descending"（按互动量降序，与自研路径
                                `apis/xhs_pc_apis.py` 用的 XHS 原生 sort_type 词表一致）。
                                本方法不做取值校验，原样透传给三方接口。
            note_type:         0=全部，1=视频，2=图文
            search_request_id: 首次搜索后从响应提取，翻页时原样回传以维持同一会话

        网络抖动（RemoteDisconnected / ConnectionError 等）时自动重试最多
        _max_retries 次，间隔 _retry_delay 秒，重试时重建底层 HTTP 连接。
        """
        url = f"{self._base}/search"
        payload: Dict[str, Any] = {
            "keyword": keyword,
            "page": page,
            "sort": sort,
            "note_type": note_type,
        }
        if search_request_id:
            payload["search_request_id"] = search_request_id

        last_exc: Exception = RuntimeError("未发起请求")
        for attempt in range(1, _max_retries + 1):
            try:
                logger.info(
                    f"[RedbookApi] search keyword={keyword!r} page={page} sort={sort}"
                    + (f" search_request_id={search_request_id}" if search_request_id else "")
                )
                resp = self._session.post(
                    url, json=payload, headers=self._headers(), timeout=_TIMEOUT
                )
                resp.raise_for_status()
                data = resp.json()
                self._log_remaining(data)
                return data
            except Exception as exc:
                last_exc = exc
                if attempt < _max_retries:
                    logger.warning(
                        f"[RedbookApi] search keyword={keyword!r} page={page} "
                        f"第{attempt}次失败: {exc}，{_retry_delay}s 后重试..."
                    )
                    time.sleep(_retry_delay)
                    # RemoteDisconnected 后连接池可能持有已关闭连接，强制重建
                    try:
                        self._session.close()
                    except Exception:
                        pass
                    self._session = type(self._session)()
                    self._session.trust_env = False
        raise last_exc

    def get_detail(
        self,
        note_id: str,
        _max_retries: int = 2,
        _retry_delay: float = 1.0,
    ) -> Dict[str, Any]:
        """调用 GET /redbook/detail，返回完整笔记详情（含全文 desc + 话题标签）。

        Args:
            note_id: 笔记 ID

        失败时重试最多 _max_retries 次。网络稳定性较高时一般一次成功；
        失败后调用方应 fail-open（保留搜索截断摘要继续流程）。
        """
        url = f"{self._base}/detail"
        params: Dict[str, Any] = {"note_id": note_id}

        last_exc: Exception = RuntimeError("未发起请求")
        for attempt in range(1, _max_retries + 1):
            try:
                resp = self._session.get(
                    url, params=params, headers=self._headers(), timeout=_TIMEOUT
                )
                resp.raise_for_status()
                data = resp.json()
                self._log_remaining(data)
                return data
            except Exception as exc:
                last_exc = exc
                if attempt < _max_retries:
                    logger.warning(
                        f"[RedbookApi] get_detail note_id={note_id} "
                        f"第{attempt}次失败: {exc}，{_retry_delay}s 后重试..."
                    )
                    time.sleep(_retry_delay)
                else:
                    logger.debug(
                        f"[RedbookApi] get_detail note_id={note_id} "
                        f"已重试 {_max_retries} 次仍失败，调用方将 fail-open"
                    )
        raise last_exc

    def get_comments(
        self,
        note_id: str,
        cursor: str = "",
        _max_retries: int = 3,
        _retry_delay: float = 2.0,
    ) -> Dict[str, Any]:
        """调用 GET /redbook/comments，返回原始响应 dict。

        Args:
            note_id: 笔记 ID
            cursor:  翻页游标。首次必须传空字符串（接口要求 cursor 参数始终存在），
                     后续翻页从上一页响应的 data.data.cursor 字段取值原样传入

        三方服务偶发 502/连接错误时自动重试最多 _max_retries 次，每次间隔 _retry_delay 秒。
        """
        url = f"{self._base}/comments"
        # 接口要求 cursor 参数必须出现在 query string 中（首次为空字符串），缺失会 502
        params: Dict[str, Any] = {"note_id": note_id, "cursor": cursor}

        last_exc: Exception = RuntimeError("未发起请求")
        for attempt in range(1, _max_retries + 1):
            try:
                resp = self._session.get(
                    url, params=params, headers=self._headers(), timeout=_TIMEOUT
                )
                resp.raise_for_status()
                data = resp.json()
                self._log_remaining(data)
                return data
            except Exception as exc:
                last_exc = exc
                if attempt < _max_retries:
                    logger.warning(
                        f"[RedbookApi] get_comments note_id={note_id} "
                        f"第{attempt}次失败: {exc}，{_retry_delay}s 后重试..."
                    )
                    time.sleep(_retry_delay)
                else:
                    logger.warning(
                        f"[RedbookApi] get_comments note_id={note_id} "
                        f"已重试 {_max_retries} 次仍失败，放弃本次请求"
                    )
        raise last_exc

    # ── 解析方法 ──────────────────────────────────────────────────────────────

    @staticmethod
    def parse_search_notes(response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从搜索响应中提取 model_type=="note" 的内层 note 数据列表。

        实际结构：items[] = {mix_track_id, model_type, note: {...笔记数据...}}，
        笔记字段（id/title/desc/liked_count 等）在 item["note"] 内层。
        """
        try:
            items = (
                response.get("data", {})
                .get("data", {})
                .get("items") or []
            )
            return [
                item["note"]
                for item in items
                if item.get("model_type") == "note" and isinstance(item.get("note"), dict)
            ]
        except Exception:
            return []

    @staticmethod
    def parse_search_request_id(response: Dict[str, Any]) -> str:
        """从搜索响应中提取 search_request_id，供后续翻页回传。"""
        try:
            inner = (response.get("data") or {}).get("data") or {}
            return str(inner.get("search_request_id") or "").strip()
        except Exception:
            return ""

    @staticmethod
    def parse_search_note_ids(response: Dict[str, Any]) -> List[str]:
        """从搜索响应中提取 note_id 列表（用于翻页诊断）。"""
        return [
            str(n.get("id") or "").strip()
            for n in RedbookApiClient.parse_search_notes(response)
            if n.get("id")
        ]

    @staticmethod
    def _extract_video_urls(raw_note: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
        """从 video_info_v2 提取视频直链候选列表（图文笔记无该字段，返回空）。

        结构：video_info_v2.media.stream.{h264,h265}[] = [{master_url, backup_urls, ...}]
        优先级：h264 在前（多数 AI 多模态接口/播放器兼容性优于 h265/hevc），
        每种编码内部保留响应原始顺序（三方/XHS 已按清晰度做过排序）。
        主直链取不到时回退 media.video.opaque1.default_screencast_stream。

        Returns:
            (video_url, video_urls) — 主直链 + 候选列表
            [{"url", "type": "h264"/"h265", "quality", "backup_urls"}]。
        """
        media = ((raw_note.get("video_info_v2") or {}).get("media")) or {}
        stream = media.get("stream") or {}

        candidates: List[Dict[str, Any]] = []
        for codec in ("h264", "h265"):
            for item in stream.get(codec) or []:
                if not isinstance(item, dict):
                    continue
                master_url = str(item.get("master_url") or "").strip()
                if not master_url:
                    continue
                candidates.append({
                    "url": master_url,
                    "type": codec,
                    "quality": str(item.get("quality_type") or ""),
                    "backup_urls": [
                        str(u).strip() for u in (item.get("backup_urls") or []) if str(u or "").strip()
                    ],
                })

        video_url = candidates[0]["url"] if candidates else ""
        if not video_url:
            fallback = ((media.get("video") or {}).get("opaque1") or {}).get(
                "default_screencast_stream"
            )
            video_url = str(fallback or "").strip()

        return video_url, candidates

    @staticmethod
    def _extract_video_cover(raw_note: Dict[str, Any]) -> str:
        """视频笔记封面优先取 video_info_v2.media.image.thumbnail/first_frame。"""
        image_meta = ((raw_note.get("video_info_v2") or {}).get("media") or {}).get("image") or {}
        url = str(image_meta.get("thumbnail") or image_meta.get("first_frame") or "").strip()
        return RedbookApiClient._force_jpg(url)

    @staticmethod
    def _force_jpg(url: str) -> str:
        """把三方 CDN 图片 URL 的 `format/heif` 替换成 `format/jpg`。

        三方接口的图片/视频封面全部为 `imageView2` 处理链路生成的 HEIF 格式
        （`Content-Type: image/heif` 已实测确认），多数多模态大模型（通义千问/
        GPT-4V 等）无法直接解码 HEIF，导致图片分析失败。已实测验证：这条链路的
        `sign` 签名不校验 `format` 参数，只替换 `format/heif` → `format/jpg`
        不会导致签名失效（其余参数原样保留），CDN 正常返回 200 + image/jpeg。
        自研路径（XHS PC-web `note_card.image_list[].info_list[]`）本身就是
        jpg/webp，不受影响，此函数只对三方响应生效。
        """
        if not url or "format/heif" not in url:
            return url
        return url.replace("format/heif", "format/jpg")

    @staticmethod
    def sanitize_note_media_urls(note: Dict[str, Any]) -> Dict[str, Any]:
        """防御性修复：对笔记 dict 的 cover_url / image_urls 就地强转 heif→jpg。

        三方响应已在 `note_to_dict` 里转换过，但 L1/Redis、L2/pgvector 缓存里
        可能仍留有修复上线前写入的旧数据（缓存 TTL 6 小时/长期持久化），
        重启进程不会清空缓存。此方法在**每次从缓存读出笔记时**兜底调用一遍，
        保证无论缓存新旧都不会再把 heif 地址喂给多模态模型。对非三方
        （自研）笔记的 jpg/webp 地址是无操作（no-op）。
        """
        if not isinstance(note, dict):
            return note
        cover_url = note.get("cover_url")
        if isinstance(cover_url, str) and cover_url:
            note["cover_url"] = RedbookApiClient._force_jpg(cover_url)
        image_urls = note.get("image_urls")
        if isinstance(image_urls, list):
            note["image_urls"] = [
                RedbookApiClient._force_jpg(u) if isinstance(u, str) else u
                for u in image_urls
                if u
            ]
        return note

    @staticmethod
    def note_to_dict(raw_note: Dict[str, Any], source_keyword: str) -> Dict[str, Any]:
        """将三方 API 的原始 note 映射为 pipeline 内部 note dict 格式。"""
        note_id = str(raw_note.get("id") or "").strip()
        xsec_token = str(raw_note.get("xsec_token") or "").strip()

        # 发布时间：优先 timestamp（Unix 秒，样例全部携带且格式稳定），
        # 回退 corner_tag_info（list，找 type=="publish_time" 的 text，
        # 但其格式不稳定：可能为 "2025-11-11" / "03-21" / "1天前"）
        publish_time = ""
        ts = raw_note.get("timestamp")
        if ts:
            try:
                ts_sec = int(ts) // 1000 if int(ts) > 1e10 else int(ts)
                publish_time = _dt.datetime.fromtimestamp(ts_sec).strftime("%Y-%m-%d")
            except Exception:
                pass
        if not publish_time:
            for tag in raw_note.get("corner_tag_info") or []:
                if isinstance(tag, dict) and tag.get("type") == "publish_time":
                    publish_time = str(tag.get("text") or "").strip()
                    break

        # 封面与图片列表（三方返回的 url 均为 HEIF 格式，转成 jpg 供多模态模型解析）
        images_list = raw_note.get("images_list") or []
        cover_url = ""
        if images_list and isinstance(images_list[0], dict):
            cover_url = RedbookApiClient._force_jpg(str(images_list[0].get("url") or ""))
        image_urls = [
            RedbookApiClient._force_jpg(str(img.get("url")))
            for img in images_list
            if isinstance(img, dict) and img.get("url")
        ]

        # 视频直链（图文笔记无 video_info_v2，均返回空值/空列表，不影响现有字段）
        video_url, video_urls = RedbookApiClient._extract_video_urls(raw_note)
        video_cover = RedbookApiClient._extract_video_cover(raw_note) or cover_url

        # 用户信息
        user = raw_note.get("user") or {}
        user_id = str(user.get("userid") or user.get("user_id") or "").strip()
        nickname = str(user.get("nickname") or "").strip()

        # 互动数据
        liked_count = int(raw_note.get("liked_count") or 0)
        collected_count = int(raw_note.get("collected_count") or 0)
        comments_count = int(raw_note.get("comments_count") or 0)
        shared_count = int(raw_note.get("shared_count") or 0)

        return {
            "note_id":          note_id,
            "title":            str(raw_note.get("title") or "").strip(),
            "desc":             str(raw_note.get("desc") or "").strip(),
            "tags":             [],
            "tag_list":         [],
            "url":              (
                f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={xsec_token}"
                if note_id else ""
            ),
            "likes":            liked_count,
            "collects":         collected_count,
            "comments":         comments_count,
            "share_count":      shared_count,
            "interaction_score": liked_count + collected_count + comments_count,
            "publish_time":     publish_time,
            "xsec_token":       xsec_token,
            "note_type":        str(raw_note.get("type") or "normal"),
            "user_id":          user_id,
            "nickname":         nickname,
            "cover_url":        video_cover,
            "image_urls":       image_urls,
            "video_url":        video_url,
            "video_urls":       video_urls,
            "source_keyword":   source_keyword,
        }

    @staticmethod
    def parse_comments(
        response: Dict[str, Any],
    ) -> Tuple[List[Dict[str, Any]], bool, str, int]:
        """从评论响应中解包评论列表、翻页信息和总评论数。

        Returns:
            (comments_raw, has_more, next_cursor, comment_count_l1)
        """
        try:
            data = (response.get("data") or {}).get("data") or {}
            comments_raw = data.get("comments") or []
            has_more = bool(data.get("has_more", False))
            next_cursor = str(data.get("cursor") or "")
            comment_count = int(data.get("comment_count_l1") or data.get("comment_count") or 0)
            return comments_raw, has_more, next_cursor, comment_count
        except Exception:
            return [], False, "", 0

    @staticmethod
    def parse_detail_note(response: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """从详情响应中提取 note_list[0] 的笔记对象。

        详情接口响应结构：
            response.data.data[0].note_list[0]  →  完整笔记 dict
        """
        try:
            data_list = (response.get("data") or {}).get("data") or []
            if not data_list:
                return None
            entry = data_list[0]
            note_list = entry.get("note_list") or []
            return note_list[0] if note_list else None
        except Exception:
            return None

    @staticmethod
    def extract_detail_fields(detail_note: Dict[str, Any]) -> Dict[str, Any]:
        """从详情 note 对象中提取完整 desc 和话题标签列表。

        Returns:
            {
                "desc": str,          # 完整笔记正文（含换行/话题嵌入，不截断）
                "tags": List[str],    # 话题名列表，如 ["二手钢琴", "钢琴选购"]
            }
        """
        full_desc = str(detail_note.get("desc") or "").strip()

        # 优先 hash_tag（type=="topic"，最完整）；回退到 topics 列表
        hash_tags = detail_note.get("hash_tag") or []
        topic_names: List[str] = [
            str(t.get("name") or "").strip()
            for t in hash_tags
            if t.get("type") == "topic" and t.get("name")
        ]
        if not topic_names:
            topics = detail_note.get("topics") or []
            topic_names = [
                str(t.get("name") or "").strip()
                for t in topics
                if t.get("name")
            ]

        return {
            "desc": full_desc,
            "tags": [n for n in topic_names if n],
        }

    @staticmethod
    def clean_comment_text(text: str) -> str:
        """清洗搜索高亮标记：#xxx[搜索高亮]# → xxx。"""
        return _HIGHLIGHT_RE.sub(r"\1", text).strip()

    @staticmethod
    def parse_comment_item(
        c: Dict[str, Any],
        *,
        note_id: str,
        note_url: str,
        note_title: str,
        is_sub: bool,
        parent_id: str,
    ) -> Optional[Dict[str, Any]]:
        """将三方 API 评论条目映射为 pipeline comment dict。返回 None 表示跳过。"""
        if not isinstance(c, dict):
            return None

        raw_content = c.get("content") or ""
        if isinstance(raw_content, dict):
            raw_content = str(raw_content.get("text") or "").strip()
        else:
            raw_content = str(raw_content).strip()

        text = RedbookApiClient.clean_comment_text(raw_content)
        if not text:
            return None

        cid = str(c.get("id") or "").strip()
        if not cid:
            return None

        user = c.get("user") or c.get("user_info") or {}
        user_id = str(user.get("userid") or user.get("user_id") or "").strip()
        nickname = str(user.get("nickname") or "").strip()

        # create_time：Unix 秒整数（DB 列 xhs_comment_data.create_time 为 BIGINT）
        raw_time = c.get("time") or c.get("create_time")
        create_time: Optional[int] = None
        if raw_time is not None:
            try:
                ts = int(raw_time)
                create_time = ts // 1000 if ts > 1e10 else ts
            except Exception:
                pass

        return {
            "comment_id":        cid,
            "content":           text,
            "like_count":        int(c.get("like_count") or 0),
            "author":            nickname,
            "user_id":           user_id,
            "ip_location":       str(c.get("ip_location") or "").strip(),
            "create_time":       create_time,
            "note_id":           note_id,
            "note_url":          note_url,
            "note_title":        note_title,
            "is_sub_comment":    is_sub,
            "parent_comment_id": parent_id,
            "sub_comment_count": int(c.get("sub_comment_count") or 0),
        }
