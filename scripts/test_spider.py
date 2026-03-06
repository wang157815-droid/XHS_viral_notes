"""快速验证爬虫签名和 API 是否正常工作"""
import sys
import os

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apis.xhs_pc_apis import XHS_Apis

COOKIES_STR = os.environ.get("XHS_COOKIES", "")


def test_selfinfo(api: XHS_Apis):
    """测试1: 获取自身用户信息（最轻量的验证）"""
    print("\n[测试1] 获取自身用户信息 (selfinfo) ...")
    success, msg, res = api.get_user_self_info(COOKIES_STR)
    print(f"  success={success}, msg={msg}")
    if res and res.get("data"):
        nickname = res["data"].get("nickname", "?")
        print(f"  昵称: {nickname}")
    return success


def test_search(api: XHS_Apis):
    """测试2: 搜索笔记（验证签名+数据获取）"""
    print("\n[测试2] 搜索笔记 (keyword='美食') ...")
    success, msg, res = api.search_note(
        query="美食",
        cookies_str=COOKIES_STR,
        page=1,
    )
    print(f"  success={success}, msg={msg}")
    if res and res.get("data"):
        items = res["data"].get("items", [])
        print(f"  返回笔记数: {len(items)}")
        if items:
            first = items[0].get("note_card", {})
            print(f"  第一篇: {first.get('display_title', '?')[:40]}")
    return success


def test_homefeed_channels(api: XHS_Apis):
    """测试3: 获取主页频道列表"""
    print("\n[测试3] 获取主页频道列表 ...")
    success, msg, res = api.get_homefeed_all_channel(COOKIES_STR)
    print(f"  success={success}, msg={msg}")
    if res and res.get("data"):
        channels = res["data"]
        if isinstance(channels, list):
            print(f"  频道数: {len(channels)}")
    return success


def test_user_info(api: XHS_Apis):
    """测试4: 获取他人用户信息（依赖 selfinfo 获取 user_id）"""
    print("\n[测试4] 获取他人用户信息 (user_info) ...")
    # 先获取自己的 user_id 作为测试目标
    _, _, self_res = api.get_user_self_info(COOKIES_STR)
    if not (self_res and self_res.get("data")):
        print("  跳过: 无法获取自身信息来提取 user_id")
        return None
    user_id = self_res["data"].get("user_id") or self_res["data"].get("userId")
    if not user_id:
        print("  跳过: 响应中未找到 user_id 字段")
        return None

    success, msg, res = api.get_user_info(user_id, COOKIES_STR)
    print(f"  success={success}, msg={msg}")
    if res and res.get("data"):
        basic = res["data"].get("basic_info", {})
        print(f"  昵称: {basic.get('nickname', '?')}")
    return success


def test_note_info(api: XHS_Apis):
    """测试5: 获取笔记详情（依赖 search 获取笔记 URL）"""
    print("\n[测试5] 获取笔记详情 (note_info) ...")
    # 先搜索获取一个有效的笔记 URL
    _, _, search_res = api.search_note(query="美食", cookies_str=COOKIES_STR, page=1)
    if not (search_res and search_res.get("data")):
        print("  跳过: 搜索无结果，无法提取笔记 URL")
        return None
    items = search_res["data"].get("items", [])
    if not items:
        print("  跳过: 搜索结果列表为空")
        return None

    first = items[0]
    note_id = first.get("id", "")
    xsec_token = first.get("xsec_token", "")
    note_url = (
        f"https://www.xiaohongshu.com/explore/{note_id}"
        f"?xsec_token={xsec_token}&xsec_source=pc_search"
    )

    success, msg, res = api.get_note_info(note_url, COOKIES_STR)
    print(f"  success={success}, msg={msg}")
    if res and res.get("data"):
        note_items = res["data"].get("items", [])
        if note_items:
            card = note_items[0].get("note_card", {})
            print(f"  标题: {card.get('title', '?')[:40]}")
    return success


def main():
    if not COOKIES_STR:
        print("ERROR: 请设置 XHS_COOKIES 环境变量")
        print('用法: XHS_COOKIES="your_cookie" python scripts/test_spider.py')
        sys.exit(1)

    print("=" * 50)
    print("小红书爬虫功能测试 (签名版本 4.3.1 + API Guard)")
    print("=" * 50)

    api = XHS_Apis()
    results = {}

    results["selfinfo"] = test_selfinfo(api)
    results["search"] = test_search(api)
    results["channels"] = test_homefeed_channels(api)

    # 以下测试依赖前置接口返回数据
    if results["selfinfo"]:
        results["user_info"] = test_user_info(api)
    else:
        print("\n[测试4] 跳过: selfinfo 失败，无法测试 user_info")
        results["user_info"] = None

    if results["search"]:
        results["note_info"] = test_note_info(api)
    else:
        print("\n[测试5] 跳过: search 失败，无法测试 note_info")
        results["note_info"] = None

    print("\n" + "=" * 50)
    print("测试结果汇总:")
    for name, ok in results.items():
        if ok is None:
            status = "SKIP"
        elif ok:
            status = "PASS"
        else:
            status = "FAIL"
        print(f"  {name}: {status}")

    # 跳过的测试不算失败
    executed = {k: v for k, v in results.items() if v is not None}
    all_pass = all(executed.values()) if executed else False
    print(f"\n{'全部通过!' if all_pass else '存在失败项，请检查 Cookie 或网络'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
