"""
任务管理数据模型

定义任务状态、控制信号、检查点等核心数据结构
"""
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Dict, Any, Optional
from datetime import datetime
from collections import deque
import json
import asyncio


class TaskState(Enum):
    """任务状态枚举"""
    PENDING = "pending"              # 等待执行
    RUNNING = "running"              # 运行中
    PAUSING = "pausing"              # 正在暂停（等待到达检查点）
    PAUSED = "paused"                # 已暂停
    COMPLETED = "completed"          # 采集完成
    ANALYZING = "analyzing"          # 分析中
    ANALYZED = "analyzed"            # 分析完成
    CANCELLING = "cancelling"        # 正在取消（等待到达检查点）
    CANCELLED = "cancelled"          # 已取消
    FAILED = "failed"                # 失败
    ANALYSIS_FAILED = "analysis_failed"  # 分析失败


class TaskCommand(Enum):
    """任务控制命令"""
    NONE = "none"          # 无命令
    PAUSE = "pause"        # 请求暂停
    RESUME = "resume"      # 请求恢复
    CANCEL = "cancel"      # 请求取消


@dataclass
class TaskCheckpoint:
    """
    任务检查点 - 保存采集进度，支持断点续传
    """
    keyword_index: int = 0           # 当前关键词索引
    dimension_index: int = 0         # 当前维度索引 (0=点赞, 1=评论, 2=收藏)
    page_index: int = 1              # 当前分页
    collected_note_ids: List[str] = field(default_factory=list)
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskCheckpoint":
        return cls(**data)


@dataclass
class TaskControlSignal:
    """
    任务控制信号 - 实现协作式暂停/恢复/取消

    使用 asyncio.Event 实现信号传递：
    - 外部调用 request_pause()/request_cancel() 发送信号
    - 任务内部循环调用 check_pause_point() 检测并响应信号
    """
    task_id: str
    command: TaskCommand = TaskCommand.NONE
    _pause_event: asyncio.Event = field(default_factory=asyncio.Event)
    _cancel_requested: bool = False
    pause_requested_at: Optional[str] = None

    def __post_init__(self):
        # 初始化时 Event 设为 set，表示可以继续运行
        self._pause_event.set()

    def request_pause(self) -> bool:
        """请求暂停（外部调用）"""
        if self.command == TaskCommand.NONE:
            self.command = TaskCommand.PAUSE
            self.pause_requested_at = datetime.now().isoformat()
            self._pause_event.clear()  # 阻塞后续检查点
            return True
        return False

    def request_resume(self) -> bool:
        """请求恢复（外部调用）"""
        if self.command == TaskCommand.PAUSE:
            self.command = TaskCommand.NONE
            self._pause_event.set()  # 解除阻塞
            return True
        return False

    def request_cancel(self) -> bool:
        """请求取消（外部调用）"""
        if not self._cancel_requested:
            self._cancel_requested = True
            self.command = TaskCommand.CANCEL
            self._pause_event.set()  # 确保不会卡在暂停状态
            return True
        return False

    async def check_pause_point(self) -> bool:
        """
        检查点函数 - 任务内部循环调用

        采用"退出式暂停"：检测到暂停信号后返回 False，让采集循环退出并保存检查点。
        恢复时需要重新启动后台任务。

        Returns:
            True: 可以继续执行
            False: 收到暂停或取消信号，应该退出循环
        """
        # 检查是否取消
        if self._cancel_requested:
            return False

        # 检查是否暂停（非阻塞，直接返回 False 让循环退出）
        if self.command == TaskCommand.PAUSE:
            return False

        return True

    @property
    def is_paused(self) -> bool:
        return self.command == TaskCommand.PAUSE

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_requested

    @property
    def should_pause(self) -> bool:
        return self.command == TaskCommand.PAUSE

    @property
    def should_cancel(self) -> bool:
        return self._cancel_requested


@dataclass
class TaskModel:
    """
    任务数据模型 - 存储任务的完整状态
    """
    task_id: str
    username: str
    keywords: List[str]
    status: TaskState = TaskState.PENDING

    # 进度信息
    progress: int = 0
    analysis_progress: int = 0
    collected: int = 0
    target: int = 100
    message: str = ""
    current_keyword: str = ""

    # 配置参数
    viral_ratio: float = 0.5
    note_type: int = 0
    time_range: int = 0
    min_sample_count: int = 50

    # 搜索模式参数（新增）
    search_mode: str = "ratio"           # ratio=比例筛选 threshold=阈值筛选
    min_interaction: Optional[int] = None  # 最低互动阈值（阈值模式）
    max_collect_count: int = 500         # 最大采集数量（阈值模式）
    enable_ai_expansion: bool = True     # 是否启用AI关键词扩展

    # 检查点（用于暂停恢复）
    checkpoint: Optional[TaskCheckpoint] = None

    # 时间戳
    created_at: str = ""
    started_at: Optional[str] = None
    paused_at: Optional[str] = None
    completed_at: Optional[str] = None

    # 结果文件
    data_file: Optional[str] = None
    analysis_file: Optional[str] = None

    # 统计和错误信息
    statistics: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    sample_warning: Optional[str] = None

    # 日志（不持久化）
    logs: deque = field(default_factory=lambda: deque(maxlen=50))
    last_log_id: int = 0

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化的字典（用于持久化和 API 响应）"""
        data = {
            "task_id": self.task_id,
            "username": self.username,
            "keywords": self.keywords,
            "status": self.status.value,
            "progress": self.progress,
            "analysis_progress": self.analysis_progress,
            "collected": self.collected,
            "target": self.target,
            "message": self.message,
            "current_keyword": self.current_keyword,
            "viral_ratio": self.viral_ratio,
            "note_type": self.note_type,
            "time_range": self.time_range,
            "min_sample_count": self.min_sample_count,
            # 搜索模式参数
            "search_mode": self.search_mode,
            "min_interaction": self.min_interaction,
            "max_collect_count": self.max_collect_count,
            "enable_ai_expansion": self.enable_ai_expansion,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "paused_at": self.paused_at,
            "completed_at": self.completed_at,
            "data_file": self.data_file,
            "analysis_file": self.analysis_file,
            "statistics": self.statistics,
            "error": self.error,
            "sample_warning": self.sample_warning,
            "last_log_id": self.last_log_id,
        }
        if self.checkpoint:
            data["checkpoint"] = self.checkpoint.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskModel":
        """从字典创建实例"""
        checkpoint_data = data.pop("checkpoint", None)
        status_value = data.pop("status", "pending")

        task = cls(
            task_id=data["task_id"],
            username=data["username"],
            keywords=data.get("keywords", []),
            status=TaskState(status_value),
            progress=data.get("progress", 0),
            analysis_progress=data.get("analysis_progress", 0),
            collected=data.get("collected", 0),
            target=data.get("target", 100),
            message=data.get("message", ""),
            current_keyword=data.get("current_keyword", ""),
            viral_ratio=data.get("viral_ratio", 0.5),
            note_type=data.get("note_type", 0),
            time_range=data.get("time_range", 0),
            min_sample_count=data.get("min_sample_count", 50),
            # 搜索模式参数
            search_mode=data.get("search_mode", "ratio"),
            min_interaction=data.get("min_interaction"),
            max_collect_count=data.get("max_collect_count", 500),
            enable_ai_expansion=data.get("enable_ai_expansion", True),
            created_at=data.get("created_at", ""),
            started_at=data.get("started_at"),
            paused_at=data.get("paused_at"),
            completed_at=data.get("completed_at"),
            data_file=data.get("data_file"),
            analysis_file=data.get("analysis_file"),
            statistics=data.get("statistics"),
            error=data.get("error"),
            sample_warning=data.get("sample_warning"),
            last_log_id=data.get("last_log_id", 0),
        )

        if checkpoint_data:
            task.checkpoint = TaskCheckpoint.from_dict(checkpoint_data)

        return task
