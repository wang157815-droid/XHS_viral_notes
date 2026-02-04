"""
异步工具函数
处理 nest_asyncio 与 uvloop 的兼容性问题，
以及线程池中安全调度协程到主事件循环。
"""
import asyncio
import concurrent.futures
from typing import Optional
from loguru import logger


# 主事件循环引用（由 FastAPI 启动时注册）
_main_loop: Optional[asyncio.AbstractEventLoop] = None


def register_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """注册主事件循环引用，供线程池中的代码使用"""
    global _main_loop
    _main_loop = loop
    logger.debug(f"主事件循环已注册: {type(loop).__name__}")


def get_main_loop() -> Optional[asyncio.AbstractEventLoop]:
    """获取主事件循环（如果已注册且仍在运行）"""
    if _main_loop is not None and _main_loop.is_running():
        return _main_loop
    return None


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


def run_async_safely(coro, timeout: float = 300):
    """
    安全地运行异步协程（兼容已存在的事件循环和 uvloop）

    处理策略（按优先级）：
    0. 已注册主事件循环 + 当前不在主循环线程中
       → run_coroutine_threadsafe 调度到主循环（解决跨循环 Semaphore 问题）
    1. 无运行中循环 → 直接 asyncio.run()
    2. 有运行中循环 + nest_asyncio 可用 → loop.run_until_complete()
    3. 有运行中循环 + uvloop（无法嵌套）→ 在新线程中执行

    Args:
        coro: 异步协程
        timeout: 超时时间（秒），仅用于策略0

    Returns:
        协程的返回值
    """
    # 策略0：如果有主事件循环且当前线程不在主循环中，调度到主循环执行
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    main_loop = get_main_loop()
    if main_loop is not None and running is None:
        # 当前线程无运行循环（典型：asyncio.to_thread 的工作线程）
        # 调度到主循环执行，确保 asyncio 对象在正确的循环中使用
        logger.debug("线程池中检测到主事件循环，使用 run_coroutine_threadsafe")
        future = asyncio.run_coroutine_threadsafe(coro, main_loop)
        try:
            return future.result(timeout=timeout)
        except (concurrent.futures.TimeoutError, TimeoutError):
            future.cancel()
            raise RuntimeError(
                f"run_async_safely: 协程在 {timeout}s 内未完成，已取消"
            )

    if running is None:
        # 没有任何循环，安全使用 asyncio.run()
        return asyncio.run(coro)

    # 有运行中的循环，尝试 nest_asyncio
    if safe_nest_asyncio_apply():
        return running.run_until_complete(coro)

    # uvloop 环境，无法嵌套，使用新线程
    logger.debug("检测到 uvloop 运行中循环，使用线程隔离执行协程")
    return _run_in_new_thread(coro)
