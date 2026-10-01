from .task_types import TaskType, ButtonClickParams, FlipbookParams, HomeAssistantParams, TaskItem, TaskParams
from .execution_engine import ExecutionEngine

__all__ = [
    "TaskType", "ButtonClickParams", "FlipbookParams",
    "HomeAssistantParams", "TaskItem", "TaskParams",
    "ExecutionEngine",
    "open_floating_panel",
]


def __getattr__(name):
    """Lazy import for ``open_floating_panel``（需要 PySide6）。"""
    if name == "open_floating_panel":
        from .window import open_floating_panel as func
        return func
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
