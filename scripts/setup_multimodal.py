"""
多模态分析配置向导
帮助用户快速配置GLM-4V多模态分析
"""
from loguru import logger
import os
from dotenv import load_dotenv, set_key

def setup_multimodal():
    """配置多模态分析"""

    logger.info("=" * 60)
    logger.info("多模态分析配置向导")
    logger.info("=" * 60)

    print("\n🎯 多模态分析可以做什么？")
    print("   - 理解图片内容（视觉焦点、构图、色调）")
    print("   - 分析图文配合关系（图片与标题/正文的呼应）")
    print("   - 提取视觉呈现技巧（封面吸引力、排版设计）")
    print("   - 生成图文创作建议\n")

    print("💰 推荐方案：智谱GLM-4V（完全免费！）\n")

    # 检查当前配置
    load_dotenv()
    current_key = os.getenv('MULTIMODAL_API_KEY', '')

    if current_key and current_key != "请在 https://open.bigmodel.cn/ 申请后填入":
        print(f"✅ 检测到已配置的API密钥: {current_key[:20]}...")
        choice = input("\n是否重新配置？(y/n): ")
        if choice.lower() != 'y':
            logger.info("保持现有配置")
            return

    print("\n📝 配置步骤：")
    print("\n【步骤1】申请API密钥")
    print("   1. 访问：https://open.bigmodel.cn/")
    print("   2. 注册/登录账号")
    print("   3. 进入控制台 -> API Keys")
    print("   4. 创建新的API密钥")
    print("   5. 复制API密钥（只显示一次，请妥善保存）")

    input("\n按回车键继续...")

    print("\n【步骤2】输入API密钥")
    api_key = input("请粘贴你的智谱API密钥: ").strip()

    if not api_key:
        logger.error("未输入API密钥，配置取消")
        return

    # 验证API密钥格式
    if len(api_key) < 20:
        logger.warning("API密钥长度似乎不正确，但仍将保存")

    # 保存到.env文件
    env_path = '.env'
    if not os.path.exists(env_path):
        logger.error(".env文件不存在，请先创建")
        return

    try:
        set_key(env_path, 'MULTIMODAL_API_KEY', api_key)
        set_key(env_path, 'MULTIMODAL_API_BASE', 'https://open.bigmodel.cn/api/paas/v4')
        set_key(env_path, 'MULTIMODAL_MODEL_NAME', 'glm-4v')

        logger.success("✅ 配置保存成功！")

        print("\n" + "=" * 60)
        print("🎉 多模态分析配置完成！")
        print("=" * 60)

        print("\n✅ 已配置：")
        print(f"   API基址: https://open.bigmodel.cn/api/paas/v4")
        print(f"   模型: glm-4v (智谱GLM-4V)")
        print(f"   API密钥: {api_key[:20]}...")

        print("\n🚀 下一步：")
        print("   1. 运行测试：python test_multimodal_analysis.py")
        print("   2. 或启动Web界面：python viral_app.py")

        print("\n💡 提示：")
        print("   - 多模态分析会自动集成到爆文分析流程")
        print("   - 如果没有配置，系统会跳过多模态分析，不影响其他功能")
        print("   - GLM-4V完全免费，可放心使用")

    except Exception as e:
        logger.error(f"保存配置失败: {e}")
        print("\n手动配置方法：")
        print(f"在 .env 文件中添加：")
        print(f'MULTIMODAL_API_KEY="{api_key}"')
        print(f'MULTIMODAL_API_BASE="https://open.bigmodel.cn/api/paas/v4"')
        print(f'MULTIMODAL_MODEL_NAME="glm-4v"')

if __name__ == "__main__":
    setup_multimodal()
