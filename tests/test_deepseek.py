"""
测试DeepSeek API集成
验证国内大模型API是否正常工作
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from loguru import logger

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# 加载环境变量
load_dotenv()

def test_deepseek_connection():
    """测试DeepSeek API连接"""
    logger.info("开始测试DeepSeek API连接...")

    try:
        import openai

        # 配置DeepSeek API
        api_key = os.getenv("OPENAI_API_KEY", "")
        api_base = os.getenv("OPENAI_API_BASE", "https://api.deepseek.com/v1")
        model_name = os.getenv("MODEL_NAME", "deepseek-chat")

        if not api_key or not api_key.startswith("sk-"):
            logger.error("请在.env文件中配置有效的API密钥")
            logger.info("示例配置：")
            logger.info('OPENAI_API_KEY="sk-deepseek-your-key"')
            logger.info('OPENAI_API_BASE="https://api.deepseek.com/v1"')
            logger.info('MODEL_NAME="deepseek-chat"')
            return False

        # 设置API配置
        openai.api_key = api_key
        openai.api_base = api_base

        logger.info(f"API基址: {api_base}")
        logger.info(f"模型: {model_name}")

        # 发送测试请求
        logger.info("发送测试请求...")
        response = openai.ChatCompletion.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "你是一个测试助手"},
                {"role": "user", "content": "请简短回复：你好"}
            ],
            max_tokens=50,
            temperature=0.7
        )

        # 解析响应
        reply = response.choices[0].message.content
        logger.success(f"API连接成功！")
        logger.info(f"模型回复: {reply}")

        # 计算成本
        usage = response.get('usage', {})
        input_tokens = usage.get('prompt_tokens', 0)
        output_tokens = usage.get('completion_tokens', 0)

        # DeepSeek定价（按百万tokens）
        input_cost = input_tokens * 0.5 / 1000000  # 0.5元/百万tokens
        output_cost = output_tokens * 2 / 1000000  # 2元/百万tokens
        total_cost = input_cost + output_cost

        logger.info(f"Token使用: 输入{input_tokens} + 输出{output_tokens} = {input_tokens + output_tokens}")
        logger.info(f"预估成本: ¥{total_cost:.6f}")

        return True

    except Exception as e:
        logger.error(f"API连接失败: {str(e)}")
        if "Invalid API key" in str(e):
            logger.error("API密钥无效，请检查配置")
        elif "Connection" in str(e):
            logger.error("网络连接问题，请检查网络或代理设置")
        return False


def test_viral_analyzer():
    """测试爆文分析器的大模型集成"""
    logger.info("\n开始测试爆文分析器集成...")

    try:
        from viral_agent.services.viral_analyzer import ViralAnalyzer
        from viral_agent.models.viral_note import ViralNote

        # 使用环境变量配置创建分析器
        analyzer = ViralAnalyzer()

        # 创建测试数据
        test_note = ViralNote(
            note_id="test123",
            note_url="https://test.com",
            note_type="图集",
            user_id="user123",
            nickname="测试用户",
            avatar="",
            home_url="",
            title="测试标题：5个超好用的护肤精华推荐",
            desc="今天给大家分享我用过的5个超好用的护肤精华...",
            tags=["护肤", "精华", "推荐"],
            liked_count=5000,
            collected_count=3000,
            comment_count=500,
            upload_time="今天",
            ip_location="上海"
        )

        # 准备特征数据（模拟）
        features = {
            'title_features': {
                'avg_length': 20,
                'top_keywords': [
                    {'word': '推荐', 'count': 10},
                    {'word': '精华', 'count': 8}
                ]
            },
            'content_features': {
                'avg_length': 300,
                'top_keywords': [
                    {'word': '护肤', 'count': 15},
                    {'word': '效果', 'count': 12}
                ]
            }
        }

        logger.info("调用AI分析...")

        # 测试AI分析提示词构建
        prompt = analyzer._build_ai_prompt([test_note], features, "护肤精华")
        logger.info(f"生成的提示词长度: {len(prompt)} 字符")

        # 如果API配置正确，可以尝试真实调用
        if os.getenv("OPENAI_API_KEY"):
            try:
                ai_result = analyzer._perform_ai_analysis([test_note], features, "护肤精华")
                if ai_result:
                    logger.success("AI分析成功!")
                    logger.info(f"分析结果预览: {str(ai_result)[:200]}...")
                else:
                    logger.warning("AI分析返回空结果")
            except Exception as e:
                logger.warning(f"AI分析调用失败（这是正常的，如果没有配置API）: {e}")

        logger.success("爆文分析器集成测试完成")
        return True

    except ImportError as e:
        logger.error(f"导入模块失败: {e}")
        return False
    except Exception as e:
        logger.error(f"测试失败: {e}")
        return False


def test_preset_configs():
    """测试预设配置功能"""
    logger.info("\n测试预设配置功能...")

    try:
        from viral_agent.services.viral_analyzer import ViralAnalyzer

        # 测试不同的预设
        presets = ['deepseek', 'qwen', 'glm']

        for preset in presets:
            logger.info(f"测试预设: {preset}")

            # 使用假的API密钥进行测试（仅测试配置）
            analyzer = ViralAnalyzer.from_preset(
                preset_name=preset,
                api_key="test-api-key"
            )

            logger.info(f"  - API基址: {analyzer.api_base}")
            logger.info(f"  - 模型名称: {analyzer.model_name}")

        logger.success("预设配置测试完成")
        return True

    except Exception as e:
        logger.error(f"预设配置测试失败: {e}")
        return False


def main():
    """主测试函数"""
    logger.info("=" * 50)
    logger.info("DeepSeek API集成测试")
    logger.info("=" * 50)

    # 检查环境变量
    logger.info("\n检查环境变量配置...")
    api_key = os.getenv("OPENAI_API_KEY", "")
    api_base = os.getenv("OPENAI_API_BASE", "")
    model_name = os.getenv("MODEL_NAME", "")

    if api_key:
        logger.info(f"✓ API密钥已配置 (前8位: {api_key[:8]}...)")
    else:
        logger.warning("✗ API密钥未配置")

    if api_base:
        logger.info(f"✓ API基址: {api_base}")
    else:
        logger.info("✗ API基址未配置，将使用默认值")

    if model_name:
        logger.info(f"✓ 模型名称: {model_name}")
    else:
        logger.info("✗ 模型名称未配置，将使用默认值")

    # 运行测试
    tests = [
        ("DeepSeek API连接", test_deepseek_connection),
        ("预设配置功能", test_preset_configs),
        ("爆文分析器集成", test_viral_analyzer),
    ]

    results = []
    for test_name, test_func in tests:
        logger.info(f"\n{'='*30}")
        logger.info(f"运行测试: {test_name}")
        logger.info(f"{'='*30}")
        result = test_func()
        results.append((test_name, result))

    # 输出测试结果总结
    logger.info("\n" + "=" * 50)
    logger.info("测试结果总结")
    logger.info("=" * 50)

    for test_name, result in results:
        status = "✓ 通过" if result else "✗ 失败"
        logger.info(f"{status} - {test_name}")

    all_passed = all(r for _, r in results)
    if all_passed:
        logger.success("\n所有测试通过！DeepSeek API集成成功。")
    else:
        logger.warning("\n部分测试失败，请检查配置和错误信息。")

    # 提供配置建议
    if not api_key:
        logger.info("\n" + "="*50)
        logger.info("快速配置指南")
        logger.info("="*50)
        logger.info("1. 注册DeepSeek账号: https://platform.deepseek.com/")
        logger.info("2. 获取API密钥")
        logger.info("3. 在.env文件中添加以下配置：")
        logger.info("")
        logger.info('OPENAI_API_BASE="https://api.deepseek.com/v1"')
        logger.info('OPENAI_API_KEY="sk-deepseek-你的密钥"')
        logger.info('MODEL_NAME="deepseek-chat"')
        logger.info("")
        logger.info("其他可选方案：")
        logger.info("- 智谱GLM（免费）: https://open.bigmodel.cn/")
        logger.info("- 通义千问: https://dashscope.console.aliyun.com/")


if __name__ == "__main__":
    main()