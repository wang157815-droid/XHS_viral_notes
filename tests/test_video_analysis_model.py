"""
测试 video_analysis_model.py 的实用性
对比有数据模型 vs 没有数据模型的差异
"""
import json
from datetime import datetime
from viral_agent.models.video_analysis_model import (
    VideoAnalysisResult,
    VideoCoverAnalysis,
    VideoTitleAnalysis,
    VideoTimelineAnalysis,
    VideoAnalysisBatch
)


def test_without_model():
    """测试没有数据模型的情况（使用原始字典）"""
    print("\n" + "="*60)
    print("测试1：没有数据模型（原始字典方式）")
    print("="*60)

    # 模拟分析结果（使用字典）
    result = {
        'note_id': '12345',
        'video_url': 'https://example.com/video.mp4',
        'title': '防脱精华测评',
        'cover_analysis': {
            'main_category': '产品展示类',
            'sub_category': '人物与产品互动',
            'image_type': '单图',
            'raw_result': '产品展示类-人物与产品互动-单图',
            'confidence': 0.85
        },
        'title_analysis': {
            'main_category': '问题解决类',
            'sub_category': '眼部问题+干货手法',
            'keywords': ['防脱', '精华', '测评'],
            'length': 6,
            'has_emoji': False,
            'has_number': False
        },
        'timeline_analysis': {
            'product_appear_time': '30s',
            'product_use_time': '60s',
            'content_start_time': '45s',
            'content_type': '单品推荐-单品推荐',
            'entry_point': '熬夜垮脸',
            'product_intro_way': '自用分享',
            'product_embed_way': '手持口播'
        },
        'analysis_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'analysis_status': 'success'
    }

    print("\n原始数据结构：")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 问题1：字段访问容易出错
    print("\n❌ 问题1：字段拼写错误不会报错（运行时才发现）")
    try:
        # 错误的字段名
        wrong_field = result.get('titel')  # 拼写错误：titel vs title
        print(f"   访问错误字段 'titel': {wrong_field}")  # 返回 None，不会报错！
        print("   ⚠️ 这是bug！但Python不会提示")
    except Exception as e:
        print(f"   错误: {e}")

    # 问题2：类型不安全
    print("\n❌ 问题2：类型不安全，可以随意修改")
    result['confidence'] = "很高"  # 应该是float，但可以改成字符串
    print(f"   confidence字段被改成字符串: {result.get('confidence')}")
    print("   ⚠️ 后续代码可能崩溃")

    # 问题3：没有方法封装
    print("\n❌ 问题3：没有便捷方法，需要手动处理")
    print("   想获取摘要？需要手动拼接：")
    summary = {
        'note_id': result['note_id'],
        'title': result['title'],
        'status': result['analysis_status'],
        'cover': f"{result['cover_analysis']['main_category']}-{result['cover_analysis']['sub_category']}"
    }
    print(f"   手动拼接: {summary}")
    print("   ⚠️ 代码冗长，容易出错")

    # 问题4：嵌套字典难以维护
    print("\n❌ 问题4：深层嵌套访问麻烦")
    try:
        # 多层访问
        sub_cat = result['cover_analysis']['sub_category']
        print(f"   访问子分类需要: result['cover_analysis']['sub_category']")
        print("   ⚠️ 层级太深，代码难读")
    except KeyError as e:
        print(f"   KeyError: {e}")


def test_with_model():
    """测试使用数据模型的情况"""
    print("\n" + "="*60)
    print("测试2：使用数据模型（结构化类）")
    print("="*60)

    # 使用数据模型
    result = VideoAnalysisResult(
        note_id='12345',
        video_url='https://example.com/video.mp4',
        title='防脱精华测评',
        analysis_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        analysis_status='success'
    )

    # 填充封面分析
    result.cover_analysis = VideoCoverAnalysis(
        main_category='产品展示类',
        sub_category='人物与产品互动',
        image_type='单图',
        raw_result='产品展示类-人物与产品互动-单图',
        confidence=0.85
    )

    # 填充标题分析
    result.title_analysis = VideoTitleAnalysis(
        main_category='问题解决类',
        sub_category='眼部问题+干货手法',
        keywords=['防脱', '精华', '测评'],
        length=6,
        has_emoji=False,
        has_number=False
    )

    # 填充时间轴分析
    result.timeline_analysis = VideoTimelineAnalysis(
        product_appear_time='30s',
        product_use_time='60s',
        content_start_time='45s',
        content_type='单品推荐-单品推荐',
        entry_point='熬夜垮脸',
        product_intro_way='自用分享',
        product_embed_way='手持口播'
    )

    print("\n数据模型结构：")
    print(result.to_json())

    # 优势1：字段访问有IDE提示
    print("\n✅ 优势1：字段访问有自动补全和类型检查")
    print(f"   result.title = '{result.title}'")
    print(f"   result.cover_analysis.main_category = '{result.cover_analysis.main_category}'")
    print("   💡 IDE会提示所有可用字段，不会拼错")

    # 优势2：类型安全
    print("\n✅ 优势2：类型安全（创建时就会检查）")
    try:
        # 尝试创建错误类型的数据
        wrong = VideoCoverAnalysis(
            main_category='产品展示类',
            sub_category='人物互动',
            image_type='单图',
            raw_result='xxx',
            confidence="很高"  # 错误类型：应该是float
        )
        print("   ⚠️ 注意：dataclass默认不强制类型检查")
        print(f"   但可以通过type hints在开发时发现: {type(wrong.confidence)}")
    except Exception as e:
        print(f"   类型错误被捕获: {e}")

    # 优势3：内置便捷方法
    print("\n✅ 优势3：内置便捷方法，代码简洁")
    summary = result.get_summary()
    print(f"   result.get_summary(): {json.dumps(summary, ensure_ascii=False, indent=2)}")
    print("   💡 一行代码获取摘要，无需手动拼接")

    # 优势4：清晰的数据结构
    print("\n✅ 优势4：清晰的数据结构，易于理解")
    print(f"   result.cover_analysis.sub_category = '{result.cover_analysis.sub_category}'")
    print("   💡 访问路径清晰，代码可读性高")

    # 优势5：序列化方便
    print("\n✅ 优势5：内置序列化方法")
    print("   result.to_json() - 转为JSON字符串")
    print("   result.to_dict() - 转为字典")
    print("   💡 导出数据非常方便")


def test_batch_analysis():
    """测试批量分析的场景"""
    print("\n" + "="*60)
    print("测试3：批量分析场景（数据模型的真正价值）")
    print("="*60)

    # 创建批次
    batch = VideoAnalysisBatch(
        batch_id='batch_20250112',
        total_videos=3
    )

    # 模拟分析3个视频
    for i in range(1, 4):
        result = VideoAnalysisResult(
            note_id=f'note_{i}',
            video_url=f'https://example.com/video{i}.mp4',
            title=f'视频{i}标题',
            analysis_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            analysis_status='success' if i != 2 else 'failed',
            error_message='分析失败' if i == 2 else None
        )

        if i != 2:  # 第2个失败
            result.cover_analysis = VideoCoverAnalysis(
                main_category='产品展示类',
                sub_category='人物互动',
                image_type='单图',
                raw_result='产品展示类-人物互动-单图'
            )

        batch.add_result(result)

    print(f"\n批次分析结果：")
    print(f"总计: {batch.total_videos} 个视频")
    print(f"成功: {batch.success_count} 个")
    print(f"失败: {batch.failed_count} 个")

    print("\n✅ 数据模型的价值：")
    print("1. 统一的数据结构，便于批量处理")
    print("2. 自动统计成功/失败数量")
    print("3. 可扩展性强（可添加更多统计字段）")
    print("4. 便于生成报告和导出")


def test_real_world_usage():
    """测试真实场景中的使用情况"""
    print("\n" + "="*60)
    print("测试4：检查项目中的实际使用情况")
    print("="*60)

    import os
    from pathlib import Path

    project_root = Path(__file__).parent

    # 搜索引用
    print("\n🔍 搜索项目中对 VideoAnalysisResult 的引用...")

    usage_count = 0
    files_checked = 0

    for py_file in project_root.rglob('*.py'):
        if 'video_analysis_model.py' in str(py_file):
            continue
        if '__pycache__' in str(py_file):
            continue

        files_checked += 1
        try:
            with open(py_file, 'r', encoding='utf-8') as f:
                content = f.read()
                if 'VideoAnalysisResult' in content or 'from viral_agent.models.video_analysis_model' in content:
                    usage_count += 1
                    print(f"   ✅ 找到引用: {py_file.relative_to(project_root)}")
        except:
            pass

    print(f"\n统计结果：")
    print(f"   检查文件数: {files_checked}")
    print(f"   使用文件数: {usage_count}")

    if usage_count == 0:
        print("\n⚠️ 警告：video_analysis_model.py 基本未被使用！")
        print("   可能原因：")
        print("   1. 功能还在开发中，未集成到主流程")
        print("   2. 是遗留代码，可以考虑清理")
        print("   3. 或者项目选择了其他数据结构")
    else:
        print(f"\n✅ 数据模型在 {usage_count} 个文件中被使用")


def main():
    """主函数"""
    print("\n" + "🎯"*30)
    print("video_analysis_model.py 实用性测试")
    print("🎯"*30)

    # 运行所有测试
    test_without_model()
    test_with_model()
    test_batch_analysis()
    test_real_world_usage()

    # 总结
    print("\n" + "="*60)
    print("📊 总结：数据模型的价值")
    print("="*60)
    print("""
✅ 数据模型的优势：
1. 类型安全：IDE会提示字段类型，减少运行时错误
2. 代码补全：编程时有自动补全，避免拼写错误
3. 可维护性：结构清晰，易于理解和修改
4. 内置方法：to_json()、get_summary() 等便捷方法
5. 文档作用：dataclass本身就是最好的文档

❌ 但项目中的问题：
1. video_analysis_model.py 基本未被使用
2. VideoEnhancedAnalyzer 也没有被集成到主流程
3. 这意味着整个视频分析模块可能是"孤立的代码"

💡 建议：
1. 如果视频分析功能是计划中的，应该尽快集成到 viral_app.py
2. 如果不需要这个功能，可以考虑删除这些文件
3. 或者，保留数据模型但简化实现（去掉未使用的分析器）
""")


if __name__ == '__main__':
    main()
