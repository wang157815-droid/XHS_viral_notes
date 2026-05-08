"""独立诊断脚本：直接用 cookie 调一次小红书搜索,立刻知道 cookie 是否有效。

**核心用途**：定位"哪份 cookie 能用"。系统里可能有多份 cookie 来源：
- datas/users/<username>/cookies.json      (UserDataService 写入,含 Playwright 扫码)
- .env 的 COOKIE / COOKIES 变量             (手动从浏览器复制粘贴)
- 直接粘贴的 cookie 字符串                   (命令行 --cookie 参数)

跑法：

    # 1. 测默认位置 datas/users/admin/cookies.json
    python -m backend.tests._diagnose_xhs_cookie

    # 2. 指定关键词和用户名
    python -m backend.tests._diagnose_xhs_cookie --keyword "完美日记" --user admin

    # 3. 同时测所有源 (最常用,一次看清谁能用)
    python -m backend.tests._diagnose_xhs_cookie --all

    # 4. 粘贴 cookie 字符串直接测（从浏览器复制后）
    python -m backend.tests._diagnose_xhs_cookie --cookie "abRequestId=...;a1=...;..."

输出：
- SUCCESS: 能拿到 N 条结果 → 这份 cookie 可用
- EMPTY:   success=True 但 items=[] → 软风控（典型原因：Playwright 自动化浏览器 a1 被识别）
- FAILED:  API 直接报错 → cookie 失效或签名问题
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional, List, Tuple


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _read_cookies_json(username: str) -> Tuple[str, Optional[str]]:
    """返回 (cookie_str, source_desc);不存在返回 ("", None)."""
    path = ROOT / "datas" / "users" / username / "cookies.json"
    if not path.exists():
        return "", None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return "", f"{path} (解析失败: {exc})"
    cookie = (payload.get("cookie") or "").strip()
    if not cookie:
        return "", f"{path} (cookie 字段为空)"
    return cookie, f"{path} · updated_at={payload.get('updated_at', '?')} · len={len(cookie)}"


def _read_env_cookie() -> Tuple[str, Optional[str]]:
    """从 .env 读 COOKIES/COOKIE 变量。"""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    for key in ("COOKIES", "COOKIE"):
        v = (os.getenv(key) or "").strip()
        if v and "xxx" not in v.lower():  # 过滤占位符
            return v, f"env:{key} (len={len(v)})"
    return "", ".env 中 COOKIES/COOKIE 未配置或仍是占位符"


def _test_one_cookie(cookie: str, label: str, keyword: str) -> str:
    """返回 'SUCCESS' | 'EMPTY' | 'FAILED'。"""
    print(f"\n---- [{label}] 测试 ----")
    if not cookie:
        print("  cookie 为空,跳过")
        return "FAILED"

    from apis.xhs_pc_apis import XHS_Apis

    # 简要展示前 60 字符方便核对是哪份
    print(f"  cookie prefix: {cookie[:60]}...")

    # 检查关键字段
    required = ("a1=", "web_session=", "webId=")
    missing = [f for f in required if f not in cookie]
    if missing:
        print(f"  [WARN] 缺少关键字段: {missing} (可能无法正常请求)")

    client = XHS_Apis()
    try:
        success, msg, res = client.search_note(
            query=keyword,
            cookies_str=cookie,
            page=1,
            sort_type_choice=2,
            note_type=0,
            note_time=0,
        )
    except Exception as exc:
        print(f"  [FAILED] 调用异常: {type(exc).__name__}: {exc}")
        return "FAILED"

    items = (res or {}).get("data", {}).get("items", []) if res else []
    code = (res or {}).get("code")
    print(f"  success={success}  msg={msg!r}  code={code}  items={len(items)}")

    if success and items:
        print(f"  [SUCCESS] 能正常搜索,前 2 条:")
        for item in items[:2]:
            note = item.get("note_card") or {}
            title = (note.get("display_title") or note.get("title") or "(无标题)")[:40]
            # Windows 终端的 GBK 编码不支持 emoji,用 ascii-safe 打印
            safe = title.encode("ascii", "replace").decode("ascii")
            print(f"    - {safe}")
        return "SUCCESS"
    if success and not items:
        print("  [EMPTY] API 返回 success=True 但 items=[] → 软反爬,cookie 被降权")
        return "EMPTY"
    print(f"  [FAILED] API 报错")
    return "FAILED"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keyword", "-k", default="咖啡", help="用于测试的搜索关键词")
    parser.add_argument("--user", "-u", default="admin", help="从哪个用户目录读 cookies.json")
    parser.add_argument("--cookie", "-c", default="", help="直接粘贴 cookie 字符串测试")
    parser.add_argument("--all", "-a", action="store_true", help="同时测试 所有 可用来源")
    args = parser.parse_args()

    print(f"=== XHS Cookie 诊断 ===")
    print(f"测试关键词: {args.keyword}")
    print(f"工作目录:   {ROOT}")

    sources: List[Tuple[str, str]] = []  # (cookie, label)

    if args.cookie:
        sources.append((args.cookie.strip(), "命令行 --cookie 粘贴"))

    if args.all or not args.cookie:
        # 文件源
        c, src = _read_cookies_json(args.user)
        if c:
            sources.append((c, f"cookies.json [{args.user}]"))
            print(f"\n  发现: {src}")
        else:
            print(f"\n  未发现 cookies.json: {src or args.user}")

        # env 源
        c, src = _read_env_cookie()
        if c:
            sources.append((c, "环境变量 COOKIES/COOKIE"))
            print(f"  发现: {src}")
        else:
            print(f"  {src}")

    if not sources:
        print("\n[FAIL] 没有任何 cookie 源可测。请：")
        print("  - 使用 --cookie 'xxx' 直接粘贴")
        print("  - 或扫码登录生成 datas/users/admin/cookies.json")
        print("  - 或在 .env 配置 COOKIES='a1=...;web_session=...;...'")
        sys.exit(2)

    results = []
    for cookie, label in sources:
        results.append((label, _test_one_cookie(cookie, label, args.keyword)))

    print("\n=== 汇总 ===")
    for label, status in results:
        icon = {"SUCCESS": "[OK] ", "EMPTY": "[BAN]", "FAILED": "[ERR]"}.get(status, "?")
        print(f"  {icon} {label:<40} → {status}")

    any_ok = any(s == "SUCCESS" for _, s in results)
    any_empty = any(s == "EMPTY" for _, s in results)

    if any_ok:
        print("\n>>> 至少有一份 cookie 能用,把它设置进 CrawlerAgent 的取值路径即可。")
        print("    推荐：把能用的 cookie 写进 .env 的 COOKIES=...,")
        print("    我们可以给 CrawlerAgent 加一层 env 兜底（见下条建议）。")
        sys.exit(0)

    if any_empty:
        print("\n>>> 所有源都是 EMPTY → 这不是 cookie 存储问题,而是:")
        print("    1) 可能性最高：Playwright 自动化浏览器的 a1 被小红书识别为机器人")
        print("       **解决**：用本机 Chrome 浏览器手动登录小红书,")
        print("                打开 DevTools → Network → 刷新页面 → 任意请求的 Request Headers → Cookie")
        print("                复制完整 cookie,再用 --cookie \"...\" 参数测试本脚本")
        print("    2) 账号被平台限流,换一个小红书账号登录试试")
        sys.exit(1)

    print("\n>>> 所有源都 FAILED。重点排查:")
    print("    - cookie 是否完整(必须含 a1, web_session, webId)")
    print("    - 签名 JS 文件是否能正常加载(看 execjs 日志)")
    print("    - 本机 Node.js 是否安装(execjs 依赖)")
    sys.exit(1)


if __name__ == "__main__":
    main()
