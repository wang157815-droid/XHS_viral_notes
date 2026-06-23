import json, sys
sys.stdout.reconfigure(encoding="utf-8")

d = json.load(open(r"E:\redMuse\XHS_viral_notes\docs\评论结果.txt", encoding="utf-8"))
data = d["data"]["data"]
cs = data["comments"]

print("一级评论数(本页):", len(cs))
print("comment_count:", data.get("comment_count"), " comment_count_l1:", data.get("comment_count_l1"))
print("has_more:", data.get("has_more"), " cursor:", repr(data.get("cursor", ""))[:80])
print()

for i, c in enumerate(cs):
    sub_count = c.get("sub_comment_count", 0)
    subs_returned = len(c.get("sub_comments") or [])
    sub_cursor = c.get("sub_comment_cursor")
    cid = c.get("id", "")
    print(f"comment[{i}] id={cid}")
    print(f"  sub_comment_count={sub_count}  已返回子评论={subs_returned}")
    if sub_cursor:
        print(f"  sub_comment_cursor={repr(sub_cursor)[:120]}")
    # 打印子评论内容预览
    for j, sub in enumerate(c.get("sub_comments") or []):
        print(f"    sub[{j}] id={sub.get('id')} content={repr(sub.get('content',''))[:40]}")
