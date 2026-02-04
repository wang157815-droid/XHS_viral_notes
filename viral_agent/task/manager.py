"""
任务管理器

核心组件，负责任务的生命周期管理、控制信号分发、状态查询
"""
from typing import Dict, List, Optional, Callable
from datetime import datetime, timezone
from collections import deque
from loguru import logger

from .models import (
    TaskModel, TaskState, TaskCommand,
    TaskControlSignal, TaskCheckpoint
)
from .persistence import TaskPersistence, get_persistence


# 日志缓冲区大小
LOG_BUFFER_SIZE = 50


class TaskManager:
    """
    任务管理器 - 核心组件

    职责：
    1. 任务生命周期管理（创建、启动、暂停、恢复、取消）
    2. 控制信号分发（通过 TaskControlSignal）
    3. 检查点机制（支持断点续传）
    4. 状态查询和更新
    5. 持久化协调
    """

    def __init__(self, persistence: Optional[TaskPersistence] = None):
        self._tasks: Dict[str, TaskModel] = {}
        self._signals: Dict[str, TaskControlSignal] = {}
        self._persistence = persistence or get_persistence()
        self._log_id_counter = 0

        # 启动时加载未完成的任务
        self._load_unfinished_tasks()

    def _load_unfinished_tasks(self) -> None:
        """加载未完成的任务（用于服务重启恢复）"""
        tasks = self._persistence.load_unfinished_tasks()
        for task in tasks:
            # 运行中的任务标记为暂停（需要用户手动恢复）
            if task.status in (TaskState.RUNNING, TaskState.PAUSING):
                task.status = TaskState.PAUSED
                task.message = "服务重启，任务已暂停，请手动恢复"
                task.paused_at = datetime.now().isoformat()
                self._persistence.save_task(task)

            # 分析中的任务标记为分析失败（分析无法恢复，需重新触发）
            elif task.status == TaskState.ANALYZING:
                task.status = TaskState.ANALYSIS_FAILED
                task.error = "服务重启，分析中断"
                task.message = "服务重启，分析中断，请重新启动分析"
                self._persistence.save_task(task)

            # 取消中的任务直接标记为已取消
            elif task.status == TaskState.CANCELLING:
                task.status = TaskState.CANCELLED
                task.message = "服务重启，任务已取消"
                self._persistence.save_task(task)

            self._tasks[task.task_id] = task
            logger.info(f"恢复任务: {task.task_id}, 状态: {task.status.value}")

    # ==================== 任务创建 ====================

    def create_task(
        self,
        task_id: str,
        username: str,
        keywords: List[str],
        target_count: int = 100,
        viral_ratio: float = 0.5,
        note_type: int = 0,
        time_range: int = 0,
        min_sample_count: int = 50,
        # 新增搜索模式参数
        search_mode: str = "ratio",
        min_interaction: Optional[int] = None,
        max_collect_count: int = 500,
        enable_ai_expansion: bool = True,
    ) -> TaskModel:
        """创建新任务"""
        task = TaskModel(
            task_id=task_id,
            username=username,
            keywords=keywords,
            target=target_count,
            viral_ratio=viral_ratio,
            note_type=note_type,
            time_range=time_range,
            min_sample_count=min_sample_count,
            # 搜索模式参数
            search_mode=search_mode,
            min_interaction=min_interaction,
            max_collect_count=max_collect_count,
            enable_ai_expansion=enable_ai_expansion,
            message="任务已创建，等待执行",
        )

        self._tasks[task_id] = task
        self._persistence.save_task(task)

        logger.info(f"创建任务: {task_id}, 关键词: {keywords}")
        return task

    # ==================== 任务控制 ====================

    def start_task(self, task_id: str) -> bool:
        """启动任务"""
        task = self._tasks.get(task_id)
        if not task:
            return False

        if task.status not in (TaskState.PENDING, TaskState.PAUSED):
            logger.warning(f"任务 {task_id} 状态 {task.status.value} 无法启动")
            return False

        # 创建控制信号
        signal = TaskControlSignal(task_id=task_id)
        self._signals[task_id] = signal

        task.status = TaskState.RUNNING
        task.started_at = task.started_at or datetime.now().isoformat()
        task.paused_at = None
        task.message = "任务运行中"

        self._persistence.save_task(task)
        logger.info(f"启动任务: {task_id}")
        return True

    def pause_task(self, task_id: str) -> bool:
        """
        请求暂停任务

        注意：这只是发送暂停信号，实际暂停需要采集循环在检查点检测到信号
        """
        task = self._tasks.get(task_id)
        signal = self._signals.get(task_id)

        if not task or task.status != TaskState.RUNNING:
            return False

        if signal and signal.request_pause():
            task.status = TaskState.PAUSING
            task.message = "正在暂停，等待到达检查点..."
            self._persistence.save_task(task)
            logger.info(f"任务 {task_id} 收到暂停信号")
            return True

        return False

    def resume_task(self, task_id: str) -> bool:
        """恢复暂停的任务"""
        task = self._tasks.get(task_id)
        signal = self._signals.get(task_id)

        if not task or task.status != TaskState.PAUSED:
            return False

        # 如果信号存在，恢复它
        if signal:
            signal.request_resume()
        else:
            # 创建新的信号
            signal = TaskControlSignal(task_id=task_id)
            self._signals[task_id] = signal

        task.status = TaskState.RUNNING
        task.paused_at = None
        task.message = "任务已恢复"

        self._persistence.save_task(task)
        logger.info(f"任务 {task_id} 已恢复")
        return True

    def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        task = self._tasks.get(task_id)
        signal = self._signals.get(task_id)

        if not task:
            return False

        # 已完成或已取消的任务不能取消
        terminal_states = {
            TaskState.COMPLETED,
            TaskState.ANALYZED,
            TaskState.CANCELLED,
        }
        if task.status in terminal_states:
            return False

        # 发送取消信号
        if signal:
            signal.request_cancel()

        task.status = TaskState.CANCELLING
        task.message = "正在取消，等待到达检查点..."
        self._persistence.save_task(task)

        logger.info(f"任务 {task_id} 收到取消信号")
        return True

    # ==================== 检查点机制 ====================

    def get_signal(self, task_id: str) -> Optional[TaskControlSignal]:
        """获取任务的控制信号（采集器使用）"""
        return self._signals.get(task_id)

    def confirm_paused(self, task_id: str, checkpoint: TaskCheckpoint) -> None:
        """确认任务已暂停并保存检查点"""
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskState.PAUSED
            task.checkpoint = checkpoint
            task.paused_at = datetime.now().isoformat()
            task.message = (
                f"任务已暂停 (关键词 {checkpoint.keyword_index + 1}/"
                f"{len(task.keywords)}, 第 {checkpoint.page_index} 页)"
            )
            self._persistence.save_task(task)
            self.add_log(task_id, "⏸️ 任务已暂停", "warning")
            logger.info(f"任务 {task_id} 已暂停，检查点已保存")

    def confirm_cancelled(self, task_id: str) -> None:
        """确认任务已取消"""
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskState.CANCELLED
            task.completed_at = datetime.now().isoformat()
            task.message = "任务已取消"
            self._persistence.save_task(task)
            self._cleanup_signal(task_id)
            self.add_log(task_id, "🛑 任务已取消", "warning")
            logger.info(f"任务 {task_id} 已取消")

    def get_checkpoint(self, task_id: str) -> Optional[TaskCheckpoint]:
        """获取任务检查点（用于恢复采集）"""
        task = self._tasks.get(task_id)
        return task.checkpoint if task else None

    # ==================== 状态查询 ====================

    def get_task(self, task_id: str) -> Optional[TaskModel]:
        """获取单个任务"""
        return self._tasks.get(task_id)

    def list_tasks(self, username: str) -> List[TaskModel]:
        """列出用户的所有任务"""
        return [t for t in self._tasks.values() if t.username == username]

    def list_active_tasks(self, username: str) -> List[TaskModel]:
        """列出用户的活跃任务（运行中、暂停中、分析中）"""
        active_states = {
            TaskState.PENDING,
            TaskState.RUNNING,
            TaskState.PAUSING,
            TaskState.PAUSED,
            TaskState.ANALYZING,
            TaskState.CANCELLING,
        }
        return [
            t for t in self._tasks.values()
            if t.username == username and t.status in active_states
        ]

    def get_running_count(self, username: str) -> int:
        """获取用户运行中的任务数"""
        running_states = {TaskState.RUNNING, TaskState.ANALYZING}
        return sum(
            1 for t in self._tasks.values()
            if t.username == username and t.status in running_states
        )

    # ==================== 状态更新 ====================

    def update_progress(
        self,
        task_id: str,
        progress: int,
        collected: int,
        message: str,
        current_keyword: str = ""
    ) -> None:
        """更新任务进度"""
        task = self._tasks.get(task_id)
        if task:
            task.progress = progress
            task.collected = collected
            task.message = message
            if current_keyword:
                task.current_keyword = current_keyword

    def update_analysis_progress(
        self,
        task_id: str,
        progress: int,
        message: str
    ) -> None:
        """更新分析进度"""
        task = self._tasks.get(task_id)
        if task:
            task.analysis_progress = progress
            task.message = message

    def add_log(self, task_id: str, message: str, level: str = "info") -> None:
        """添加任务日志"""
        task = self._tasks.get(task_id)
        if task:
            self._log_id_counter += 1
            now = datetime.now(timezone.utc)
            log_entry = {
                "id": self._log_id_counter,
                "time": now.isoformat(),
                "message": message,
                "level": level
            }
            task.logs.append(log_entry)
            task.last_log_id = self._log_id_counter

    def mark_completed(
        self,
        task_id: str,
        data_file: str,
        statistics: Optional[Dict] = None
    ) -> None:
        """标记采集完成"""
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskState.COMPLETED
            task.progress = 100
            task.data_file = data_file
            task.completed_at = datetime.now().isoformat()
            task.message = f"采集完成，共 {task.collected} 篇笔记"
            if statistics:
                task.statistics = statistics
            self._persistence.save_task(task)
            self.add_log(task_id, "✅ 采集完成", "success")

    def mark_analyzing(self, task_id: str) -> None:
        """标记开始分析"""
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskState.ANALYZING
            task.analysis_progress = 0
            task.message = "正在分析笔记..."
            self._persistence.save_task(task)
            self.add_log(task_id, "🔍 开始 AI 分析", "info")

    def mark_analyzed(
        self,
        task_id: str,
        analysis_file: str
    ) -> None:
        """标记分析完成"""
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskState.ANALYZED
            task.analysis_progress = 100
            task.analysis_file = analysis_file
            task.message = "分析完成"
            self._persistence.save_task(task)
            self._cleanup_signal(task_id)
            self.add_log(task_id, "✅ 分析完成", "success")

    def mark_failed(self, task_id: str, error: str) -> None:
        """标记任务失败"""
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskState.FAILED
            task.error = error
            task.message = f"任务失败: {error}"
            task.completed_at = datetime.now().isoformat()
            self._persistence.save_task(task)
            self._cleanup_signal(task_id)
            self.add_log(task_id, f"❌ 任务失败: {error}", "error")

    def mark_analysis_failed(self, task_id: str, error: str) -> None:
        """标记分析失败"""
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskState.ANALYSIS_FAILED
            task.error = error
            task.message = f"分析失败: {error}"
            self._persistence.save_task(task)
            self._cleanup_signal(task_id)
            self.add_log(task_id, f"❌ 分析失败: {error}", "error")

    def set_sample_warning(self, task_id: str, warning: str) -> None:
        """设置样本量警告"""
        task = self._tasks.get(task_id)
        if task:
            task.sample_warning = warning

    def _cleanup_signal(self, task_id: str) -> None:
        """清理控制信号"""
        self._signals.pop(task_id, None)


# 全局实例
_manager_instance: Optional[TaskManager] = None


def get_task_manager() -> TaskManager:
    """获取全局任务管理器实例"""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = TaskManager()
    return _manager_instance
