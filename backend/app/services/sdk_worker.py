"""
Finance SDK 子进程入口脚本

由 safe_fetch_and_decrypt 通过 asyncio.create_subprocess_exec 调用。
在独立子进程中加载 WeWork Finance SDK，崩溃（SIGSEGV）只影响子进程，
不会杀死主应用进程。

Usage:
    python <此文件路径> <seq> <limit>

Output:
    JSON 写入 stdout：
      {"ok": true,  "messages": [...], "max_seq": 123}
      {"ok": false, "error": "错误描述"}
"""
import json
import os
import sys
from pathlib import Path

# 把项目根目录加入 sys.path，确保 `from backend.app...` 可以找到
# 本文件位于 <root>/backend/app/services/sdk_worker.py
# parents: [0]=services, [1]=app, [2]=backend, [3]=<root>
_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def main() -> None:
    seq = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 100

    # 分步诊断：每一步完成都写 stderr，方便定位崩溃发生在哪步
    def _step(msg: str) -> None:
        sys.stderr.write(f"[sdk-worker] {msg}\n")
        sys.stderr.flush()

    try:
        _step("step1: importing wxwork_session_archive")
        from backend.app.services.wxwork_session_archive import (
            FinanceSDKError,
            _get_sdk,
            _rsa_decrypt_random_key,
        )
        _step("step2: calling _get_sdk() → NewSdk + Init")
        sdk = _get_sdk()
        _step("step3: SDK initialized OK, calling GetChatData")
        raw = sdk.get_chat_data(seq=seq, limit=limit)
        _step(f"step4: GetChatData returned errcode={raw.get('errcode', 0)}, count={len(raw.get('chatdata', []))}")

        if raw.get("errcode", 0) != 0:
            raise FinanceSDKError(
                f"GetChatData errcode={raw['errcode']} errmsg={raw.get('errmsg')}"
            )

        messages = []
        max_seq = seq
        for item in raw.get("chatdata", []):
            item_seq = item.get("seq", 0)
            if item_seq > max_seq:
                max_seq = item_seq
            try:
                aes_key_b64 = _rsa_decrypt_random_key(item["encrypt_random_key"])
                msg = sdk.decrypt_msg(aes_key_b64, item["encrypt_chat_msg"])
                msg["_seq"] = item_seq
                msg["_msgid"] = item.get("msgid", "")
                messages.append(msg)
            except Exception as exc:
                _step(f"  decrypt failed seq={item_seq}: {exc}")

        _step(f"step5: done, messages={len(messages)} max_seq={max_seq}")
        output = {"ok": True, "messages": messages, "max_seq": max_seq}
    except Exception as exc:
        _step(f"EXCEPTION: {exc}")
        output = {"ok": False, "error": str(exc)}

    sys.stdout.write(json.dumps(output, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
