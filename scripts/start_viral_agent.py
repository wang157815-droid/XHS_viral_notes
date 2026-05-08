"""
RedMuse 后端启动脚本（兼容旧文件名）。

阶段 4.5 起 `viral_app.py` 只保留维护 stub，真实 Web 后端请启动
`backend.app.main`。
"""
import os
import sys
from loguru import logger
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()


def check_environment():
    """检查新后端运行所需的基本环境。"""
    if not os.getenv("OPENAI_API_KEY"):
        logger.warning("未配置OPENAI_API_KEY，模型相关能力会在运行时降级或报错")

    dirs = ["datas", "logs", "web/templates"]
    for dir_path in dirs:
        if not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
            logger.info(f"创建目录: {dir_path}")

    return True


def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("RedMuse Backend")
    logger.info("=" * 60)

    # 检查环境
    if not check_environment():
        logger.error("请修复上述问题后重试")
        sys.exit(1)

    logger.info("正在启动新版后端服务...")
    logger.info("访问地址: http://localhost:8100/api/v1/health")
    logger.info("按 Ctrl+C 停止服务")
    logger.info("-" * 60)

    try:
        import uvicorn
        uvicorn.run(
            "backend.app.main:app",
            host="0.0.0.0",
            port=8100,
            reload=False,
            log_level="info",
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