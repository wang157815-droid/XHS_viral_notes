"""
诊断脚本：验证 warmup 使用的 cookie 是否正确
运行方式：python scripts/diag_warmup_cookie.py
"""
import sys
import os
import json
from pathlib import Path

# 确保项目根目录在 path 中
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def check_credential_store():
    """列出 XhsCredentialStore 中所有凭据"""
    print("\n=== XhsCredentialStore 凭据列表 ===")
    try:
        from backend.app.services.xhs_auth import get_credential_store
        credentials = get_credential_store().list_credentials()
        if not credentials:
            print("  ❌ 凭据表为空，请先在设置→数据源授权中绑定小红书账号")
            return []
        for c in credentials:
            print(f"  redmuse_user_id : {c.redmuse_user_id}")
            print(f"  xhs_nickname    : {c.xhs_nickname}")
            print(f"  status          : {c.status}")
            print(f"  last_validated  : {c.last_validated_at}")
            print(f"  cookies_path    : {c.cookies_path}")
            # 检查文件是否存在
            if c.cookies_path:
                p = ROOT / c.cookies_path
                if p.exists():
                    try:
                        data = json.loads(p.read_text(encoding="utf-8"))
                        cookie_str = data.get("cookie", "")
                        print(f"  cookie_file     : ✅ 存在  cookie_len={len(cookie_str)}")
                        # 检查关键字段
                        has_web_session = "web_session=" in cookie_str
                        has_a1 = "a1=" in cookie_str
                        print(f"  关键字段        : web_session={'✅' if has_web_session else '❌'} a1={'✅' if has_a1 else '❌'}")
                    except Exception as e:
                        print(f"  cookie_file     : ❌ 文件解析失败: {e}")
                else:
                    print(f"  cookie_file     : ❌ 文件不存在: {p}")
            print()
        return credentials
    except Exception as e:
        print(f"  ❌ 加载 CredentialStore 失败: {e}")
        import traceback; traceback.print_exc()
        return []


def check_warmup_selection():
    """模拟 _find_warmup_user_id 选哪个凭据"""
    print("=== Warmup 选择的凭据 ===")
    try:
        from backend.app.services.xhs_auth import get_credential_store
        credentials = get_credential_store().list_credentials()

        def _sort_key(c):
            return str(c.last_validated_at or c.updated_at or "")

        active = [c for c in credentials if c.redmuse_user_id and c.cookies_path and c.status == "active"]
        if active:
            best = max(active, key=_sort_key)
            print(f"  ✅ 选中 active 凭据: {best.redmuse_user_id} (xhs={best.xhs_nickname})")
            return best.redmuse_user_id

        fallback = [c for c in credentials if c.redmuse_user_id and c.cookies_path and c.status != "unbound"]
        if fallback:
            best = max(fallback, key=_sort_key)
            print(f"  ⚠️ 降级选: {best.redmuse_user_id} (status={best.status})")
            return best.redmuse_user_id

        print("  ❌ 没有可用凭据")
        return None
    except Exception as e:
        print(f"  ❌ 失败: {e}")
        return None


def resolve_cookie(owner_user_id):
    """用 XhsCredentialResolver 解析 cookie"""
    print(f"\n=== 解析 cookie (owner={owner_user_id}) ===")
    try:
        from backend.app.services.xhs_auth import get_credential_resolver
        resolved = get_credential_resolver().resolve(owner_user_id)
        print(f"  source    : {resolved.source}")
        print(f"  path      : {resolved.cookies_path}")
        print(f"  cookie_len: {len(resolved.cookies_str)}")
        has_web = "web_session=" in resolved.cookies_str
        has_a1  = "a1=" in resolved.cookies_str
        print(f"  web_session: {'✅' if has_web else '❌'}  a1: {'✅' if has_a1 else '❌'}")
        return resolved.cookies_str
    except Exception as e:
        print(f"  ❌ 解析失败: {e}")
        import traceback; traceback.print_exc()
        return ""


def test_search(cookies_str, keyword="鲜奶巧克力"):
    """用解析出来的 cookie 做一次实际搜索"""
    print(f"\n=== 实际搜索测试 (keyword='{keyword}') ===")
    if not cookies_str:
        print("  ❌ cookie 为空，跳过搜索")
        return
    try:
        from apis.xhs_pc_apis import XHS_Apis
        client = XHS_Apis()
        success, msg, res_json = client.search_note(
            query=keyword,
            cookies_str=cookies_str,
            page=1,
            sort_type_choice=2,  # 点赞降序
        )
        print(f"  success={success}  msg={msg}")
        if res_json:
            data = res_json.get("data") or {}
            items = data.get("items", [])
            print(f"  res_json keys  : {list(res_json.keys())}")
            print(f"  data keys      : {list(data.keys())}")
            print(f"  items 数量     : {len(items)}")
            if items:
                print(f"  首条 keys      : {list(items[0].keys())}")
            else:
                print("  ⚠️ items 为空 — cookie 可能已过期或账号有搜索限制")
        else:
            print("  ❌ res_json 为空")
    except Exception as e:
        print(f"  ❌ 搜索异常: {e}")
        import traceback; traceback.print_exc()


def check_env_cookies():
    """检查 .env 中是否有 COOKIES 变量"""
    print("\n=== .env COOKIES 变量 ===")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    env_cookie = os.getenv("COOKIES") or os.getenv("COOKIE") or ""
    if env_cookie and "xxx" not in env_cookie.lower():
        print(f"  ✅ 找到 COOKIES  len={len(env_cookie)}")
        print(f"  web_session={'✅' if 'web_session=' in env_cookie else '❌'}  a1={'✅' if 'a1=' in env_cookie else '❌'}")
        return env_cookie
    else:
        print("  （未配置或为示例值）")
        return ""


if __name__ == "__main__":
    print("=" * 60)
    print("  Warmup Cookie 诊断脚本")
    print("=" * 60)

    check_credential_store()
    owner_id = check_warmup_selection()

    cookies_str = ""
    if owner_id:
        cookies_str = resolve_cookie(owner_id)

    env_cookie = check_env_cookies()

    # 先用 warmup cookie 测试
    if cookies_str:
        test_search(cookies_str)

    # 如果 warmup cookie 搜索失败且 .env 有 cookie，对比测试
    if env_cookie and env_cookie != cookies_str:
        print("\n--- 对比：用 .env COOKIES 测试 ---")
        test_search(env_cookie)

    print("\n诊断完成。")
