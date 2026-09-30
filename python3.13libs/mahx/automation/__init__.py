from .task_types import TaskType, ButtonClickParams, FlipbookParams, HomeAssistantParams, TaskItem, TaskParams
from .execution_engine import ExecutionEngine

__all__ = [
    "TaskType", "ButtonClickParams", "FlipbookParams",
    "HomeAssistantParams", "TaskItem", "TaskParams",
    "ExecutionEngine",
    "show_automation_window",
]


def __getattr__(name):
    """Lazy import for ``show_automation_window``（需要 PySide6）。"""
    if name == "show_automation_window":
        from .window import show_automation_window as func
        return func
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
