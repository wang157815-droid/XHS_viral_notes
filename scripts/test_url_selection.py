#!/usr/bin/env python3
"""
测试压缩版URL优先选择逻辑
验证 get_best_video_url_for_ai 的完整功能：
1. 大小限制 (max_size_mb)
2. 编码偏好 (prefer_h264)
3. 空值安全处理
4. 优先级排序
5. 压缩版URL自动转换（_259 → _130）
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xhs_utils.url_validator import (
    get_best_video_url_for_ai,
    estimate_video_size_from_url,
    try_get_compressed_url,
    clear_compressed_url_cache
)
import xhs_utils.url_validator as validator


def mock_validate(always_valid=True):
    """Mock validate_video_url 函数"""
    original = validator.validate_video_url
    validator.validate_video_url = lambda url, timeout=3: always_valid
    return original


def restore_validate(original):
    """恢复原始 validate_video_url"""
    validator.validate_video_url = original


def test_size_constraint():
    """测试 max_size_mb 大小限制"""
    print("=" * 60)
    print("测试1: max_size_mb 大小限制")
    print("=" * 60)

    test_urls = [
        {"url": "https://example.com/video_259.mp4"},  # ~8MB
        {"url": "https://example.com/video_130.mp4"},  # ~4MB
    ]

    original = mock_validate(True)
    try:
        # max_size_mb=7MB 应该跳过 _259.mp4 (~8MB)
        result = get_best_video_url_for_ai(test_urls, max_size_mb=7.0)
        if result and "_130.mp4" in result:
            print("   ✅ max_size_mb=7: 正确跳过 _259(8MB)，选择 _130(4MB)")
        else:
            print(f"   ❌ max_size_mb=7: 错误选择了 {result}")

        # max_size_mb=10MB 应该可以选择 _259.mp4
        result = get_best_video_url_for_ai(test_urls, max_size_mb=10.0)
        if result and "_130.mp4" in result:
            print("   ✅ max_size_mb=10: 仍优先选择更小的 _130(4MB)")
        else:
            print(f"   ⚠️ max_size_mb=10: 选择了 {result}")

        # max_size_mb=3MB 应该都不符合，回退
        result = get_best_video_url_for_ai(test_urls, max_size_mb=3.0)
        print(f"   ℹ️ max_size_mb=3: 回退选择 = {result.split('/')[-1] if result else 'None'}")

    finally:
        restore_validate(original)


def test_prefer_h264():
    """测试 prefer_h264 编码偏好"""
    print("\n" + "=" * 60)
    print("测试2: prefer_h264 编码偏好")
    print("=" * 60)

    # 只有 H.265 版本且都在大小限制内的情况
    test_urls = [
        {"url": "https://example.com/video_114.mp4"},  # H.265 720p
        {"url": "https://example.com/video_115.mp4"},  # H.265 1080p
    ]

    original = mock_validate(True)
    try:
        # prefer_h264=True 但只有 H.265 可选
        result = get_best_video_url_for_ai(test_urls, max_size_mb=30.0, prefer_h264=True)
        if result:
            print(f"   ✅ prefer_h264=True: 在 H.265 中选择了 {result.split('/')[-1]}")
        else:
            print("   ❌ prefer_h264=True: 返回 None")

        # prefer_h264=False
        result = get_best_video_url_for_ai(test_urls, max_size_mb=30.0, prefer_h264=False)
        if result:
            print(f"   ✅ prefer_h264=False: 选择了 {result.split('/')[-1]}")
        else:
            print("   ❌ prefer_h264=False: 返回 None")

    finally:
        restore_validate(original)


def test_null_safety():
    """测试空值安全处理"""
    print("\n" + "=" * 60)
    print("测试3: 空值安全处理")
    print("=" * 60)

    # 包含无效项的 URL 列表
    test_urls = [
        {"url": None},                                # url 为 None
        {},                                           # 缺少 url 键
        {"url": "https://example.com/video_130.mp4"}, # 正常
        None,                                         # 整个项为 None
    ]

    original = mock_validate(True)
    try:
        result = get_best_video_url_for_ai(test_urls, max_size_mb=7.0)
        if result and "_130.mp4" in result:
            print("   ✅ 正确跳过无效项，选择了有效的 _130.mp4")
        elif result is None:
            print("   ⚠️ 返回 None（可能所有项都被跳过）")
        else:
            print(f"   ❌ 意外结果: {result}")
    except Exception as e:
        print(f"   ❌ 抛出异常: {type(e).__name__}: {e}")
    finally:
        restore_validate(original)


def test_priority_sorting():
    """测试优先级排序（高清版在前的情况）"""
    print("\n" + "=" * 60)
    print("测试4: 优先级排序（高清版在前）")
    print("=" * 60)

    # 模拟真实小红书 API 返回顺序（高清版在前）
    test_urls = [
        {"url": "https://example.com/video_115.mp4"},  # H.265 1080p ~25MB
        {"url": "https://example.com/video_114.mp4"},  # H.265 720p ~25MB
        {"url": "https://example.com/video_259.mp4"},  # H.264 720p ~8MB
        {"url": "https://example.com/video_130.mp4"},  # H.264 压缩版 ~4MB
    ]

    print("   输入顺序: _115 → _114 → _259 → _130")

    original = mock_validate(True)
    try:
        result = get_best_video_url_for_ai(test_urls, max_size_mb=7.0)
        if result and "_130.mp4" in result:
            print("   ✅ 正确重排序，选择了 _130(4MB) 而非高清版")
        else:
            print(f"   ❌ 选择了: {result}")
    finally:
        restore_validate(original)


def test_empty_list():
    """测试空列表"""
    print("\n" + "=" * 60)
    print("测试5: 空列表处理")
    print("=" * 60)

    result = get_best_video_url_for_ai([], max_size_mb=7.0)
    if result is None:
        print("   ✅ 空列表正确返回 None")
    else:
        print(f"   ❌ 空列表返回: {result}")

    result = get_best_video_url_for_ai(None, max_size_mb=7.0)
    if result is None:
        print("   ✅ None 输入正确返回 None")
    else:
        print(f"   ❌ None 输入返回: {result}")


def test_compressed_url_transform():
    """测试压缩版URL转换逻辑"""
    print("\n" + "=" * 60)
    print("测试6: 压缩版URL转换（_259 → _130）")
    print("=" * 60)

    # 清空缓存，确保测试环境干净
    clear_compressed_url_cache()

    # 测试URL转换逻辑（不需要网络验证）
    test_cases = [
        # (输入URL, 预期转换结果)
        (
            "http://sns-video-hw.xhscdn.com/stream/79/110/259/abc_259.mp4",
            "http://sns-video-hw.xhscdn.com/stream/79/110/130/abc_130.mp4"
        ),
        (
            "https://sns-video-bd.xhscdn.com/stream/00/123/259/xyz_259.mp4?token=abc",
            "https://sns-video-bd.xhscdn.com/stream/00/123/130/xyz_130.mp4?token=abc"
        ),
    ]

    print("   URL 转换规则测试:")
    for original, expected in test_cases:
        # 手动测试转换逻辑（不验证URL可访问性）
        transformed = original.replace('/259/', '/130/').replace('_259.mp4', '_130.mp4')
        if transformed == expected:
            print(f"   ✅ 转换正确: ...{original[-30:]} → ...{transformed[-30:]}")
        else:
            print(f"   ❌ 转换错误: 期望 {expected[-30:]}，得到 {transformed[-30:]}")

    # 测试非 _259 URL 不应触发转换
    non_259_urls = [
        "http://example.com/video_130.mp4",  # 已经是压缩版
        "http://example.com/video_114.mp4",  # H.265
        "http://example.com/video_115.mp4",  # H.265
        "http://example.com/video.mp4",      # 无后缀
    ]

    print("\n   非 _259 URL 跳过测试:")
    for url in non_259_urls:
        result = try_get_compressed_url(url)
        if result is None:
            print(f"   ✅ 正确跳过: {url.split('/')[-1]}")
        else:
            print(f"   ❌ 不应转换: {url} → {result}")


def test_try_compressed_parameter():
    """测试 try_compressed 参数控制"""
    print("\n" + "=" * 60)
    print("测试7: try_compressed 参数控制")
    print("=" * 60)

    test_urls = [
        {"url": "https://example.com/video_259.mp4"},
    ]

    original = mock_validate(True)
    try:
        # try_compressed=True（默认）应尝试获取压缩版
        # 注：因为 mock 了 validate_video_url，压缩版会被认为有效
        result = get_best_video_url_for_ai(test_urls, max_size_mb=10.0, try_compressed=True)
        if result and "_130.mp4" in result:
            print("   ✅ try_compressed=True: 返回压缩版 _130.mp4")
        else:
            print(f"   ⚠️ try_compressed=True: 返回 {result.split('/')[-1] if result else 'None'}")

        # try_compressed=False 应跳过压缩版尝试
        clear_compressed_url_cache()
        result = get_best_video_url_for_ai(test_urls, max_size_mb=10.0, try_compressed=False)
        if result and "_259.mp4" in result:
            print("   ✅ try_compressed=False: 返回原版 _259.mp4")
        else:
            print(f"   ⚠️ try_compressed=False: 返回 {result.split('/')[-1] if result else 'None'}")

    finally:
        restore_validate(original)
        clear_compressed_url_cache()


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("   压缩版URL优先选择逻辑 - 完整测试套件")
    print("=" * 60 + "\n")

    test_size_constraint()
    test_prefer_h264()
    test_null_safety()
    test_priority_sorting()
    test_empty_list()
    test_compressed_url_transform()
    test_try_compressed_parameter()

    print("\n" + "=" * 60)
    print("所有测试完成！")
    print("=" * 60 + "\n")
