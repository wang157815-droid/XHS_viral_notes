"""
异步工具函数
处理 nest_asyncio 与 uvloop 的兼容性问题
"""
import asyncio
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


def run_async_safely(coro):
    """
    安全地运行异步协程（兼容已存在的事件循环）

    在 FastAPI/uvicorn 等异步框架中，事件循环已经在运行，
    直接调用 asyncio.run() 会失败。此函数处理这种情况。

    Args:
        coro: 异步协程

    Returns:
        协程的返回值
    """
    try:
        # 检查是否已有运行中的事件循环
        loop = asyncio.get_running_loop()
        # 如果有运行中的循环，尝试用 nest_asyncio
        safe_nest_asyncio_apply()
        return loop.run_until_complete(coro)
    except RuntimeError:
        # 没有运行中的循环，可以安全创建新的
        pass

    # 尝试获取或创建事件循环
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(coro)
