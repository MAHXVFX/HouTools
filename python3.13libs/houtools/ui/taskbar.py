"""Windows 任务栏常驻：让独立工具窗口出现在任务栏，与 Houdini 主窗并列。

只做窗口级的 WS_EX_APPWINDOW（Get 取原扩展样式再 OR，不整盖，
保留其他扩展样式位）。不做 SetCurrentProcessExplicitAppUserModelID——
那是进程级设置，会连 Houdini 主窗口的任务栏分组一起改掉。
失败走 debug 日志（无 GUI / 非 Windows 环境静默跳过）。
"""

from houtools.core.log import get_logger

log = get_logger("ui.taskbar")

_GWL_EXSTYLE = -20
_WS_EX_APPWINDOW = 0x00040000


def apply_appwindow_flags(window):
    """给顶层窗口加 WS_EX_APPWINDOW；首次调用 winId() 会创建原生句柄。"""
    import os
    if os.name != "nt":
        return
    try:
        import ctypes
        hwnd = int(window.winId())
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style | _WS_EX_APPWINDOW)
    except Exception as exc:
        log.debug("apply_appwindow_flags failed: %s", exc)
