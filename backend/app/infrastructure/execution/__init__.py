"""执行协调器：任务并发上限、分支并行、取消传播、事件序列。"""

from .coordinator import ExecutionCoordinator, TaskExecutionHandle, execution_coordinator

__all__ = ["ExecutionCoordinator", "TaskExecutionHandle", "execution_coordinator"]
