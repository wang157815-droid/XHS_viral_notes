"""百炼 API 连通性测试（最小化）

三项能力各发一次最小请求：
  1) 文本对话      qwen-max
  2) 多模态视觉    qwen3-vl-plus
  3) Embedding    text-embedding-v4

用法：确保环境变量 BAILIAN_API_KEY 已设置，然后：
  python scripts/test_bailian_api.py
"""

from __future__ import annotations

import os
import sys
import time
from typing import Callable

try:
    from openai import OpenAI
except ImportError:
    print("[FAIL] 缺少 openai 包，请先 pip install openai")
    sys.exit(1)


BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
API_KEY = os.environ.get("BAILIAN_API_KEY")

# 简单的公开图片 URL（阿里云 OSS 官方示例，百炼自家的，绝不会 403）
SAMPLE_IMAGE_URL = (
    "https://dashscope.oss-cn-beijing.aliyuncs.com/images/dog_and_girl.jpeg"
)


def _header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)


def _run(label: str, fn: Callable[[OpenAI], str], client: OpenAI) -> bool:
    print(f"\n[{label}] 发起请求 ...")
    t0 = time.time()
    try:
        preview = fn(client)
        dt = time.time() - t0
        print(f"[{label}] OK ({dt:.2f}s)")
        print(f"  返回预览: {preview}")
        return True
    except Exception as exc:  # noqa: BLE001
        dt = time.time() - t0
        print(f"[{label}] FAIL ({dt:.2f}s)")
        print(f"  {type(exc).__name__}: {exc}")
        return False


def test_chat(client: OpenAI) -> str:
    resp = client.chat.completions.create(
        model="qwen-max",
        messages=[
            {"role": "system", "content": "你是一个测试助手，回复不超过 10 个字。"},
            {"role": "user", "content": "请回复：连通成功"},
        ],
        max_tokens=32,
        temperature=0,
    )
    return (resp.choices[0].message.content or "").strip()


def test_vision(client: OpenAI) -> str:
    resp = client.chat.completions.create(
        model="qwen3-vl-plus",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": SAMPLE_IMAGE_URL}},
                    {"type": "text", "text": "用一句话描述这张图（不超过20字）。"},
                ],
            }
        ],
        max_tokens=64,
        temperature=0,
    )
    return (resp.choices[0].message.content or "").strip()


def test_embedding(client: OpenAI) -> str:
    resp = client.embeddings.create(
        model="text-embedding-v4",
        input="百炼 API 连通性测试",
    )
    vec = resp.data[0].embedding
    return f"维度={len(vec)}, 首 3 位=[{vec[0]:.4f}, {vec[1]:.4f}, {vec[2]:.4f}]"


def main() -> int:
    _header("百炼 API 连通性测试")

    if not API_KEY:
        print("[FAIL] 环境变量 BAILIAN_API_KEY 未设置")
        return 2

    masked = f"{API_KEY[:6]}***{API_KEY[-4:]}" if len(API_KEY) > 10 else "***"
    print(f"API Key: {masked}")
    print(f"Base URL: {BASE_URL}")

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=30)

    results = {
        "文本对话 qwen-max":      _run("文本对话 qwen-max",      test_chat,      client),
        "多模态 qwen3-vl-plus":   _run("多模态 qwen3-vl-plus",   test_vision,    client),
        "Embedding v4":           _run("Embedding v4",           test_embedding, client),
    }

    _header("结果汇总")
    for name, ok in results.items():
        print(f"  [{'OK  ' if ok else 'FAIL'}] {name}")

    all_ok = all(results.values())
    print(f"\n{'全部通过' if all_ok else '存在失败项'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
