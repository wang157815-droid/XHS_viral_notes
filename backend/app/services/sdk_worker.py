"""
Finance SDK 子进程入口脚本

由 safe_fetch_and_decrypt 通过 asyncio.create_subprocess_exec 调用。
在独立子进程中加载 WeWork Finance SDK，崩溃（SIGSEGV）只影响子进程，
不会杀死主应用进程。

Usage:
    python -m backend.app.services.sdk_worker <seq> <limit>

Output:
    JSON 写入 stdout：
      {"ok": true,  "messages": [...], "max_seq": 123}
      {"ok": false, "error": "错误描述"}
"""
import json
import sys


def main() -> None:
    seq = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 100

    try:
        from backend.app.services.wxwork_session_archive import fetch_and_decrypt

        messages, max_seq = fetch_and_decrypt(seq=seq, limit=limit)
        output = {"ok": True, "messages": messages, "max_seq": max_seq}
    except Exception as exc:
        output = {"ok": False, "error": str(exc)}

    sys.stdout.write(json.dumps(output, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
