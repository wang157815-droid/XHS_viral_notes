"""
异步工具函数
处理 nest_asyncio 与 uvloop 的兼容性问题
"""
import asyncio
import concurrent.futures
from loguru import logger


def safe_nest_asyncio_apply() -> bool:
    """
    安全地应用 nest_asyncio（兼容 uvloop）

    uvloop 不支持 nest_asyncio.apply()，会抛出：
    "Can't patch loop of type <class 'uvloop.Loop'>"

    Returns:
        bool: 是否成功应用
    """
    try:
        import nest_asyncio
        nest_asyncio.apply()
        return True
    except ValueError as e:
        # uvloop 不支持 nest_asyncio
        if "uvloop" in str(e).lower() or "can't patch" in str(e).lower():
            logger.debug(f"跳过 nest_asyncio（uvloop 环境）: {e}")
            return False
        raise
    except Exception as e:
        logger.warning(f"nest_asyncio.apply() 失败: {e}")
        return False


def _run_in_new_thread(coro):
    """
    在新线程中运行协程（用于无法嵌套的事件循环）

    Args:
        coro: 异步协程

    Returns:
        协程的返回值
    """
    def run_coro():
        # 在新线程中创建全新的事件循环
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        try:
            return new_loop.run_until_complete(coro)
        finally:
            new_loop.close()

    # 使用线程池执行，避免阻塞主线程
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run_coro)
        return future.result()


def run_async_safely(coro):
    """
    安全地运行异步协程（兼容已存在的事件循环和 uvloop）

    处理策略：
    1. 无运行中循环 → 直接 asyncio.run()
    2. 有运行中循环 + nest_asyncio 可用 → loop.run_until_complete()
    3. 有运行中循环 + uvloop（无法嵌套）→ 在新线程中执行

    Args:
        coro: 异步协程

    Returns:
        协程的返回值
    """
    # 策略1：检查是否有运行中的事件循环
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 没有运行中的循环，可以安全使用 asyncio.run()
        return asyncio.run(coro)

    # 有运行中的循环，尝试 nest_asyncio
    # 策略2：尝试用 nest_asyncio 嵌套运行
    if safe_nest_asyncio_apply():
        # nest_asyncio 应用成功（非 uvloop），可以嵌套
        return loop.run_until_complete(coro)

    # 策略3：uvloop 环境，无法嵌套，使用新线程
    logger.debug("检测到 uvloop 运行中循环，使用线程隔离执行协程")
    return _run_in_new_thread(coro)
