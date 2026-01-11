"""
测试爆文Agent Web应用功能

注意：现在所有 API 都需要 JWT Token 认证
测试前需要先登录获取 token
"""
import requests
import json
import os
from loguru import logger

BASE_URL = "http://localhost:8000"

# 全局 token 存储
auth_token = None


def login(username: str = "admin", password: str = None) -> bool:
    """
    登录获取 JWT Token

    Args:
        username: 用户名，默认 admin
        password: 密码，如果为 None 则从环境变量 TEST_PASSWORD 读取

    Returns:
        bool: 登录是否成功
    """
    global auth_token

    if password is None:
        password = os.getenv("TEST_PASSWORD")
        if not password:
            logger.error("请设置环境变量 TEST_PASSWORD 或在代码中传入密码")
            logger.info("首次启动时，密码会显示在服务器日志中")
            return False

    try:
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"username": username, "password": password}
        )

        if response.status_code == 200:
            data = response.json()
            auth_token = data.get("token")
            must_change = data.get("must_change_password", False)

            if must_change:
                logger.warning("⚠️ 需要修改密码才能测试其他功能")
                logger.info("请先通过 Web 界面修改密码")
                return False

            logger.success(f"✓ 登录成功，token: {auth_token[:20]}...")
            return True
        else:
            logger.error(f"登录失败: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"登录异常: {e}")
        return False


def get_auth_headers() -> dict:
    """获取带认证的请求头"""
    if not auth_token:
        raise RuntimeError("未登录，请先调用 login()")
    return {"Authorization": f"Bearer {auth_token}"}


def test_cookie_management():
    """测试Cookie管理功能"""
    logger.info("测试Cookie管理功能...")

    headers = get_auth_headers()

    # 1. 检查Cookie状态
    response = requests.get(f"{BASE_URL}/api/viral/cookie/status", headers=headers)
    if response.status_code == 200:
        data = response.json()
        logger.info(f"Cookie状态: {data}")
        if data['has_cookie']:
            logger.success(f"✓ Cookie已配置（{data['cookie_length']}字符）")
        else:
            logger.warning("✗ Cookie未配置")
    elif response.status_code == 401:
        logger.error("认证失败，请检查 token")
    elif response.status_code == 403:
        logger.error("需要先修改密码")
    else:
        logger.error(f"获取Cookie状态失败: {response.status_code}")

    # 2. 测试保存Cookie（使用示例Cookie）
    test_cookie = "a1=test123456; web_session=test789"

    response = requests.post(
        f"{BASE_URL}/api/viral/cookie",
        json={"cookie": test_cookie},
        headers=headers
    )

    if response.status_code == 200:
        data = response.json()
        if data['status'] == 'success':
            logger.success(f"✓ Cookie保存成功: {data['message']}")
        else:
            logger.error(f"✗ Cookie保存失败: {data['message']}")
    elif response.status_code == 401:
        logger.error("认证失败，请检查 token")
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

        # 登录获取 token
        logger.info("尝试登录...")
        logger.info("提示：设置环境变量 TEST_PASSWORD=<密码> 或修改代码传入密码")

        if not login():
            logger.error("登录失败，无法继续测试")
            logger.info("请确保：")
            logger.info("  1. 已在 .env 中配置 JWT_SECRET")
            logger.info("  2. 已修改初始密码（首次登录后强制修改）")
            logger.info("  3. 设置了 TEST_PASSWORD 环境变量")
            return

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