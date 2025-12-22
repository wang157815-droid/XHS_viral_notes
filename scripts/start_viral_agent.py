"""
爆文Agent启动脚本
快速启动爆文分析Web服务
"""
import os
import sys
from loguru import logger
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()


def check_environment():
    """检查环境配置"""
    issues = []

    # 检查Cookie配置
    if not os.getenv("COOKIES"):
        issues.append("未配置COOKIES，请在.env文件中设置小红书Cookie")

    # 检查OpenAI API（可选）
    if not os.getenv("OPENAI_API_KEY"):
        logger.warning("未配置OPENAI_API_KEY，将无法使用AI深度分析功能")

    # 检查必要的目录
    dirs = ["web/static", "web/templates", "datas/viral_analysis"]
    for dir_path in dirs:
        if not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
            logger.info(f"创建目录: {dir_path}")

    if issues:
        logger.error("环境检查失败:")
        for issue in issues:
            logger.error(f"  - {issue}")
        return False

    return True


def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("小红书爆文笔记生成Agent")
    logger.info("=" * 60)

    # 检查环境
    if not check_environment():
        logger.error("请修复上述问题后重试")
        sys.exit(1)

    # 启动服务
    logger.info("正在启动Web服务...")
    logger.info("访问地址: http://localhost:8000")
    logger.info("按 Ctrl+C 停止服务")
    logger.info("-" * 60)

    try:
        import uvicorn
        uvicorn.run(
            "viral_app:app",
            host="0.0.0.0",
            port=8000,
            reload=True,
            log_level="info"
        )
    except ModuleNotFoundError as e:
        logger.error(f"缺少依赖包: {e}")
        logger.error("请运行: pip install -r requirements.txt")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("\n服务已停止")
    except Exception as e:
        logger.error(f"启动失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()