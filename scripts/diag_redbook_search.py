"""临时诊断：对比第三方 Redbook search 在不同 sort 下的原始响应。

用法：python scripts/diag_redbook_search.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from backend.app.infrastructure.crawlers.redbook_api_client import RedbookApiClient


def probe(client: RedbookApiClient, keyword: str, sort: str) -> None:
    print(f"\n===== keyword={keyword!r} sort={sort!r} =====")
    try:
        resp = client.search(keyword, 1, sort, _max_retries=1)
    except Exception as exc:
        print(f"  [EXCEPTION] {type(exc).__name__}: {exc}")
        return
    top_keys = list(resp.keys()) if isinstance(resp, dict) else type(resp).__name__
    print(f"  top-level keys: {top_keys}")
    print(f"  err_no={resp.get('err_no')!r}  message={resp.get('message')!r}  count={resp.get('count')!r}")
    notes = RedbookApiClient.parse_search_notes(resp)
    print(f"  parse_search_notes -> {len(notes)} 条")
    # 打印 data 层结构（截断），便于看错误体
    data = resp.get("data")
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, dict):
            print(f"  data.data keys: {list(inner.keys())}  items={len(inner.get('items') or [])}")
        else:
            print(f"  data.data = {json.dumps(inner, ensure_ascii=False)[:300]}")
    else:
        print(f"  data = {json.dumps(data, ensure_ascii=False)[:300]}")


def main() -> None:
    client = RedbookApiClient()
    print(f"base={client._base!r}  key_set={bool(client._key)}")
    probe(client, "巧克力", "general")
    probe(client, "巧克力", "time_descending")


if __name__ == "__main__":
    main()
