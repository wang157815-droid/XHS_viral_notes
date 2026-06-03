"""
企业微信定时推送任务

用法（在任意地方 import 并调用）：
    from backend.app.services.wxwork_scheduler import scheduler
    scheduler.start()   # 应用启动时调用
    scheduler.stop()    # 应用关闭时调用

或者直接用 APScheduler 独立运行：
    python -m backend.app.services.wxwork_scheduler

依赖：pip install apscheduler
"""
import asyncio
from datetime import datetime, timezone

from loguru import logger


# ------------------------------------------------------------------ #
# 定时任务执行器（延迟导入避免循环依赖）
# ------------------------------------------------------------------ #

async def _push_to_users(to_users: list[str], content: str = "", use_ai: bool = False, ai_prompt: str = "") -> None:
    """复用 webhook 路由里的推送逻辑"""
    from ..api.routes.wxwork_webhook import _do_push
    await _do_push(to_users, content, use_ai, ai_prompt)


# ------------------------------------------------------------------ #
# 内置定时任务示例（按需开启 / 修改）
# ------------------------------------------------------------------ #

async def job_morning_tips(to_users: list[str]) -> None:
    """每日早报：让 AI 生成今日小红书运营技巧推送给所有用户"""
    logger.info("[wxwork-scheduler] 执行早报推送，目标用户数={}", len(to_users))
    await _push_to_users(
        to_users=to_users,
        use_ai=True,
        ai_prompt="今天是{}，请生成一条今日小红书内容运营小技巧，100字以内，简洁有用。".format(
            datetime.now(timezone.utc).strftime("%Y-%m-%d %A")
        ),
    )


async def job_inactive_reminder(to_users: list[str]) -> None:
    """对超过 N 天未发言的用户发送激活消息"""
    logger.info("[wxwork-scheduler] 执行沉默用户唤醒推送")
    await _push_to_users(
        to_users=to_users,
        content="最近有什么内容创作上的问题吗？随时找我聊 😊",
    )


# ------------------------------------------------------------------ #
# APScheduler 封装（可选，需 pip install apscheduler）
# ------------------------------------------------------------------ #

def build_scheduler(to_users: list[str]):
    """
    构建并返回已配置好任务的 APScheduler 实例。

    参数：
        to_users: 要推送的用户ID列表，从数据库/配置中传入

    示例：
        scheduler = build_scheduler(["zhangsan", "lisi"])
        scheduler.start()
    """
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        raise ImportError("请先安装: pip install apscheduler")

    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    # 每天早上 8:30 发早报（按需开启）
    # scheduler.add_job(
    #     job_morning_tips,
    #     CronTrigger(hour=8, minute=30),
    #     args=[to_users],
    #     id="morning_tips",
    #     name="每日早报",
    #     replace_existing=True,
    # )

    # 每周一早上 9:00 发周报提醒（按需开启）
    # scheduler.add_job(
    #     job_inactive_reminder,
    #     CronTrigger(day_of_week="mon", hour=9, minute=0),
    #     args=[to_users],
    #     id="weekly_reminder",
    #     replace_existing=True,
    # )

    return scheduler


# ------------------------------------------------------------------ #
# 独立运行入口（python -m backend.app.services.wxwork_scheduler）
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    load_dotenv()

    # 从环境变量读取推送目标（逗号分隔的用户ID）
    users_env = os.getenv("WXWORK_PUSH_USERS", "")
    users = [u.strip() for u in users_env.split(",") if u.strip()]

    if not users:
        print("请在 .env 中设置 WXWORK_PUSH_USERS=zhangsan,lisi")
        exit(1)

    sched = build_scheduler(users)
    sched.start()
    print(f"定时任务已启动，推送目标: {users}")
    print("按 Ctrl+C 停止")

    try:
        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        sched.shutdown()
        print("已停止")
