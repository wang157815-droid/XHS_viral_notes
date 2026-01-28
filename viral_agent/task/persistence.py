"""
任务持久化服务

使用 SQLite 存储任务元数据，支持服务重启后恢复
"""
import sqlite3
import json
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime
from loguru import logger

from .models import TaskModel, TaskState, TaskCheckpoint


class TaskPersistence:
    """
    SQLite 任务持久化服务

    功能：
    - 保存/加载任务状态
    - 服务重启时恢复未完成任务
    - 任务历史查询
    """

    DB_PATH = Path("datas/tasks.db")

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or self.DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """初始化数据库表"""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    keywords TEXT NOT NULL,
                    target_count INTEGER DEFAULT 200,
                    viral_ratio REAL DEFAULT 0.5,
                    note_type INTEGER DEFAULT 0,
                    time_range INTEGER DEFAULT 0,
                    min_sample_count INTEGER DEFAULT 60,
                    progress INTEGER DEFAULT 0,
                    analysis_progress INTEGER DEFAULT 0,
                    collected INTEGER DEFAULT 0,
                    message TEXT,
                    current_keyword TEXT,
                    checkpoint TEXT,
                    data_file TEXT,
                    analysis_file TEXT,
                    statistics TEXT,
                    sample_warning TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    paused_at TEXT,
                    completed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_username
                    ON tasks(username);
                CREATE INDEX IF NOT EXISTS idx_tasks_status
                    ON tasks(status);
                CREATE INDEX IF NOT EXISTS idx_tasks_created_at
                    ON tasks(created_at DESC);
            """)

    def save_task(self, task: TaskModel) -> bool:
        """保存或更新任务"""
        try:
            checkpoint_json = None
            if task.checkpoint:
                checkpoint_json = json.dumps(task.checkpoint.to_dict())

            statistics_json = None
            if task.statistics:
                statistics_json = json.dumps(task.statistics)

            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO tasks
                    (id, username, status, keywords, target_count, viral_ratio,
                     note_type, time_range, min_sample_count, progress,
                     analysis_progress, collected, message, current_keyword,
                     checkpoint, data_file, analysis_file, statistics,
                     sample_warning, error, created_at, started_at,
                     paused_at, completed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    task.task_id,
                    task.username,
                    task.status.value,
                    json.dumps(task.keywords, ensure_ascii=False),
                    task.target,
                    task.viral_ratio,
                    task.note_type,
                    task.time_range,
                    task.min_sample_count,
                    task.progress,
                    task.analysis_progress,
                    task.collected,
                    task.message,
                    task.current_keyword,
                    checkpoint_json,
                    task.data_file,
                    task.analysis_file,
                    statistics_json,
                    task.sample_warning,
                    task.error,
                    task.created_at,
                    task.started_at,
                    task.paused_at,
                    task.completed_at,
                ))
            return True
        except Exception as e:
            logger.error(f"保存任务失败 {task.task_id}: {e}")
            return False

    def load_task(self, task_id: str) -> Optional[TaskModel]:
        """加载单个任务"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM tasks WHERE id = ?",
                    (task_id,)
                )
                row = cursor.fetchone()
                if row:
                    return self._row_to_task(dict(row))
        except Exception as e:
            logger.error(f"加载任务失败 {task_id}: {e}")
        return None

    def load_unfinished_tasks(self) -> List[TaskModel]:
        """
        加载所有未完成的任务（用于服务启动恢复）

        包括：pending, running, paused, analyzing 状态的任务
        """
        unfinished_states = [
            TaskState.PENDING.value,
            TaskState.RUNNING.value,
            TaskState.PAUSING.value,
            TaskState.PAUSED.value,
            TaskState.ANALYZING.value,
            TaskState.CANCELLING.value,  # 加入 CANCELLING，重启后置为已取消
        ]
        placeholders = ",".join("?" * len(unfinished_states))

        tasks = []
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    f"SELECT * FROM tasks WHERE status IN ({placeholders}) "
                    f"ORDER BY created_at DESC",
                    unfinished_states
                )
                for row in cursor.fetchall():
                    task = self._row_to_task(dict(row))
                    if task:
                        tasks.append(task)
        except Exception as e:
            logger.error(f"加载未完成任务失败: {e}")

        return tasks

    def load_tasks_by_user(
        self,
        username: str,
        limit: int = 50,
        status: Optional[str] = None
    ) -> List[TaskModel]:
        """加载用户的任务列表"""
        tasks = []
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row

                if status:
                    cursor = conn.execute(
                        "SELECT * FROM tasks WHERE username = ? AND status = ? "
                        "ORDER BY created_at DESC LIMIT ?",
                        (username, status, limit)
                    )
                else:
                    cursor = conn.execute(
                        "SELECT * FROM tasks WHERE username = ? "
                        "ORDER BY created_at DESC LIMIT ?",
                        (username, limit)
                    )

                for row in cursor.fetchall():
                    task = self._row_to_task(dict(row))
                    if task:
                        tasks.append(task)
        except Exception as e:
            logger.error(f"加载用户任务失败 {username}: {e}")

        return tasks

    def delete_task(self, task_id: str) -> bool:
        """删除任务"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            return True
        except Exception as e:
            logger.error(f"删除任务失败 {task_id}: {e}")
            return False

    def count_active_tasks(self, username: str) -> int:
        """统计用户的活跃任务数"""
        active_states = [
            TaskState.RUNNING.value,
            TaskState.PAUSING.value,
            TaskState.ANALYZING.value,
        ]
        placeholders = ",".join("?" * len(active_states))

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    f"SELECT COUNT(*) FROM tasks "
                    f"WHERE username = ? AND status IN ({placeholders})",
                    [username] + active_states
                )
                return cursor.fetchone()[0]
        except Exception as e:
            logger.error(f"统计活跃任务失败: {e}")
            return 0

    def _row_to_task(self, row: Dict[str, Any]) -> Optional[TaskModel]:
        """将数据库行转换为 TaskModel"""
        try:
            # 解析 JSON 字段
            keywords = json.loads(row.get("keywords", "[]"))
            checkpoint_data = row.get("checkpoint")
            statistics_data = row.get("statistics")

            checkpoint = None
            if checkpoint_data:
                checkpoint = TaskCheckpoint.from_dict(json.loads(checkpoint_data))

            statistics = None
            if statistics_data:
                statistics = json.loads(statistics_data)

            return TaskModel(
                task_id=row["id"],
                username=row["username"],
                status=TaskState(row.get("status", "pending")),
                keywords=keywords,
                target=row.get("target_count", 100),
                viral_ratio=row.get("viral_ratio", 0.5),
                note_type=row.get("note_type", 0),
                time_range=row.get("time_range", 0),
                min_sample_count=row.get("min_sample_count", 50),
                progress=row.get("progress", 0),
                analysis_progress=row.get("analysis_progress", 0),
                collected=row.get("collected", 0),
                message=row.get("message", ""),
                current_keyword=row.get("current_keyword", ""),
                checkpoint=checkpoint,
                data_file=row.get("data_file"),
                analysis_file=row.get("analysis_file"),
                statistics=statistics,
                sample_warning=row.get("sample_warning"),
                error=row.get("error"),
                created_at=row.get("created_at", ""),
                started_at=row.get("started_at"),
                paused_at=row.get("paused_at"),
                completed_at=row.get("completed_at"),
            )
        except Exception as e:
            logger.error(f"转换任务数据失败: {e}")
            return None


# 全局实例
_persistence_instance: Optional[TaskPersistence] = None


def get_persistence() -> TaskPersistence:
    """获取全局持久化服务实例"""
    global _persistence_instance
    if _persistence_instance is None:
        _persistence_instance = TaskPersistence()
    return _persistence_instance
