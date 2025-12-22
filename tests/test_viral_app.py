"""
测试爆文Agent Web应用功能
"""
import requests
import json
from loguru import logger

BASE_URL = "http://localhost:8000"


def test_cookie_management():
    """测试Cookie管理功能"""
    logger.info("测试Cookie管理功能...")

    # 1. 检查Cookie状态
    response = requests.get(f"{BASE_URL}/api/viral/cookie/status")
    if response.status_code == 200:
        data = response.json()
        logger.info(f"Cookie状态: {data}")
        if data['has_cookie']:
            logger.success(f"✓ Cookie已配置（{data['cookie_length']}字符）")
        else:
            logger.warning("✗ Cookie未配置")
    else:
        logger.error(f"获取Cookie状态失败: {response.status_code}")

    # 2. 测试保存Cookie（使用示例Cookie）
    test_cookie = "a1=test123456; web_session=test789"

    response = requests.post(
        f"{BASE_URL}/api/viral/cookie",
        json={"cookie": test_cookie}
    )

    if response.status_code == 200:
        data = response.json()
        if data['status'] == 'success':
            logger.success(f"✓ Cookie保存成功: {data['message']}")
        else:
            logger.error(f"✗ Cookie保存失败: {data['message']}")
    else:
        logger.error(f"保存Cookie失败: {response.status_code}")


def test_excel_export():
    """测试Excel导出功能（需要有已完成的任务）"""
    logger.info("测试Excel导出功能...")

    # 这里需要一个实际的task_id，可以从界面获取
    # 示例：task_id = "viral_20251108182709"

    logger.info("请在Web界面完成一次采集任务后，将task_id替换到代码中进行测试")
    logger.info("Excel导出URL格式: /api/viral/export/{task_id}?format=excel")


def main():
    """主测试函数"""
    logger.info("=" * 50)
    logger.info("开始测试爆文Agent Web应用")
    logger.info("=" * 50)

    try:
        # 检查服务是否运行
        response = requests.get(f"{BASE_URL}/health")
        if response.status_code != 200:
            logger.error("服务未运行，请先启动: python viral_app.py")
            return

        health = response.json()
        logger.success(f"✓ 服务运行正常: {health}")

        # 测试各项功能
        test_cookie_management()
        test_excel_export()

        logger.info("=" * 50)
        logger.success("测试完成！")
        logger.info("现在可以访问 http://localhost:8000 使用Web界面")

    except requests.exceptions.ConnectionError:
        logger.error("无法连接到服务，请先启动: python viral_app.py")
    except Exception as e:
        logger.error(f"测试失败: {e}")


if __name__ == "__main__":
    main()