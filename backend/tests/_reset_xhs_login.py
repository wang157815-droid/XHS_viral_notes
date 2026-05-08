"""一键重置 XHS 登录状态（阶段 4.1 hotfix 2）。

使用场景：升级了浏览器 UA 版本后,必须清理旧登录数据,
因为旧 cookies 里的 a1 是旧 UA 版本浏览器产生的指纹,
和新 UA 头不匹配会被小红书软反爬识别返回空结果。

跑法：

    .venv\\Scripts\\python -m backend.tests._reset_xhs_login

    # 或指定 username
    .venv\\Scripts\\python -m backend.tests._reset_xhs_login --user admin

会做的事：
1. 停止 cookie_health 缓存
2. 删除 browser_data/xhs_<username>/  （Playwright user_data_dir）
3. 删除 datas/users/<username>/cookies.json

之后请：
- 重启后端: .venv\\Scripts\\python backend\\run.py
- 打开前端 /login 扫码(这次 Playwright 会用 Chrome 137 启动并生成匹配的 a1)
- 用诊断脚本验证: .venv\\Scripts\\python -m backend.tests._diagnose_xhs_cookie
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _remove_if_exists(path: Path, label: str) -> bool:
    if not path.exists():
        print(f"  - {label:<40} (不存在,跳过): {path}")
        return False
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        print(f"  [OK] 已删除 {label}: {path}")
        return True
    except Exception as exc:
        print(f"  [FAIL] 删除失败 {label}: {exc}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", "-u", default="admin", help="username")
    args = parser.parse_args()

    username = args.user
    print(f"=== 重置 XHS 登录状态 (user={username}) ===\n")

    removed = 0
    if _remove_if_exists(ROOT / "browser_data" / f"xhs_{username}", "Playwright user_data_dir"):
        removed += 1
    if _remove_if_exists(ROOT / "datas" / "users" / username / "cookies.json", "cookies.json"):
        removed += 1
    if _remove_if_exists(ROOT / "datas" / "auth" / "cookie_health_cache.json", "cookie 健康缓存"):
        removed += 1

    print(f"\n清理完成,共删除 {removed} 项。\n")
    print("下一步:")
    print("  1. 停掉当前后端 (Ctrl+C)")
    print("  2. 重启后端: .venv\\Scripts\\python backend\\run.py")
    print("  3. 前端打开 /login 扫码（这次是全新浏览器,UA=Chrome 137）")
    print("  4. 扫完后立即诊断 cookie 是否恢复:")
    print("     .venv\\Scripts\\python -m backend.tests._diagnose_xhs_cookie")


if __name__ == "__main__":
    main()
