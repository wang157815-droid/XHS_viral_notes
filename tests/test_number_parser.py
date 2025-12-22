"""
测试中文数字解析功能
"""
from viral_agent.utils import parse_chinese_number


def test_parse_chinese_number():
    """测试各种数字格式的解析"""

    test_cases = [
        # (输入, 期望输出)
        ("2.3万", 23000),
        ("5.6千", 5600),
        ("1234", 1234),
        ("1.5万", 15000),
        ("10万", 100000),
        ("3千", 3000),
        ("500", 500),
        (1234, 1234),
        (1234.5, 1234),
        ("0", 0),
        ("", 0),
        (None, 0),
        ("1.2w", 12000),
        ("3.5k", 3500),
    ]

    print("=" * 60)
    print("中文数字解析测试")
    print("=" * 60)

    passed = 0
    failed = 0

    for input_value, expected_output in test_cases:
        try:
            result = parse_chinese_number(input_value)
            if result == expected_output:
                print(f"✅ PASS: {repr(input_value)} → {result}")
                passed += 1
            else:
                print(f"❌ FAIL: {repr(input_value)} → {result} (期望: {expected_output})")
                failed += 1
        except Exception as e:
            print(f"❌ ERROR: {repr(input_value)} → {type(e).__name__}: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"测试结果: {passed} 通过, {failed} 失败")
    print("=" * 60)

    return failed == 0


def test_viral_note_creation():
    """测试ViralNote创建"""
    from viral_agent.models.viral_note import ViralNote

    print("\n" + "=" * 60)
    print("ViralNote 数据转换测试")
    print("=" * 60)

    # 模拟小红书API返回的数据（包含中文格式的数字）
    test_data = {
        'note_id': 'test123',
        'note_url': 'https://www.xiaohongshu.com/explore/test123',
        'note_type': '图集',
        'user_id': 'user123',
        'nickname': '测试用户',
        'avatar': 'https://example.com/avatar.jpg',
        'home_url': 'https://www.xiaohongshu.com/user/profile/user123',
        'title': '测试笔记',
        'desc': '这是一个测试笔记',
        'tags': ['测试', '爆款'],
        'interact_info': {
            'liked_count': '2.3万',      # 中文格式
            'collected_count': '5600',   # 纯数字
            'comment_count': '1.2千',    # 中文格式
            'shared_count': '800'        # 纯数字
        },
        'upload_time': '2024-01-01 12:00:00',
        'ip_location': '北京'
    }

    try:
        viral_note = ViralNote.from_spider_data(test_data)

        print(f"✅ ViralNote 创建成功")
        print(f"   笔记标题: {viral_note.title}")
        print(f"   点赞数: {viral_note.liked_count} (原始: {test_data['interact_info']['liked_count']})")
        print(f"   收藏数: {viral_note.collected_count} (原始: {test_data['interact_info']['collected_count']})")
        print(f"   评论数: {viral_note.comment_count} (原始: {test_data['interact_info']['comment_count']})")
        print(f"   分享数: {viral_note.share_count} (原始: {test_data['interact_info']['shared_count']})")
        print(f"   互动分数: {viral_note.interaction_score}")

        # 验证数值是否正确
        assert viral_note.liked_count == 23000, f"点赞数错误: {viral_note.liked_count}"
        assert viral_note.collected_count == 5600, f"收藏数错误: {viral_note.collected_count}"
        assert viral_note.comment_count == 1200, f"评论数错误: {viral_note.comment_count}"
        assert viral_note.share_count == 800, f"分享数错误: {viral_note.share_count}"

        print("\n✅ 所有数值验证通过！")
        return True

    except Exception as e:
        print(f"\n❌ ViralNote 创建失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("\n🧪 开始测试中文数字解析和ViralNote转换...\n")

    # 测试1: 数字解析函数
    test1_pass = test_parse_chinese_number()

    # 测试2: ViralNote创建
    test2_pass = test_viral_note_creation()

    # 总结
    print("\n" + "=" * 60)
    if test1_pass and test2_pass:
        print("🎉 所有测试通过！修复成功！")
    else:
        print("⚠️  部分测试失败，请检查错误信息")
    print("=" * 60)
