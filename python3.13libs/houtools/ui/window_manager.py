"""Registry for top-level tool windows.

Tools must create their windows through open_window() so the hot
reloader can close everything before reloading modules; otherwise open
windows would keep running pre-reload class code against stale state.
"""

import logging
from functools import partial

from houtools.core.log import get_logger

log = get_logger("ui.window_manager")

# tool_id -> QWidget
_windows = {}
# closeEvent 被 ignore（延迟关闭，如 videoseq 转换进行中）的窗口。必须
# 持有引用：registry 已清空后若无人引用，Python 侧回收会连带析构仍在
# 运行的 QThread —— Qt6 下直接 qFatal 崩溃。窗口在延迟关闭收尾时自行
# deleteLater，destroyed 信号再把条目摘掉。
_closing = set()


def open_window(tool_id, factory):
    """Show the window registered for tool_id, building it via factory()
    when absent. Repeated calls raise the existing window instead of
    duplicating it.
    """
    window = _windows.get(tool_id)
    if window is not None:
        try:
            _show(window)
            return window
        except RuntimeError:
            # The underlying C++ widget was already deleted.
            log.debug("window %r was deleted, rebuilding", tool_id)

    window = factory()
    _windows[tool_id] = window
    _show(window)
    return window


def close_all():
    """Close and delete every registered window (used before hot reload).

    close() 返回 False = closeEvent 被 ignore。仅当窗口显式声明
    ``is_close_deferred()``（自己安排了延迟关闭，如 videoseq 转换进行中
    先隐藏再轮询线程退出）时才跳过 deleteLater——直接删会穿透延迟关闭、
    析构运行中的 QThread（Qt6 下 qFatal 崩溃）。此类窗口移入 _closing
    持引用，由其延迟关闭收尾时自行 deleteLater。
    """
    for tool_id, window in list(_windows.items()):
        try:
            if window.close():
                window.deleteLater()
            else:
                deferred = getattr(window, "is_close_deferred", None)
                if callable(deferred) and deferred():
                    _closing.add(window)
                    window.destroyed.connect(
                        partial(_closing.discard, window))
                else:
                    window.deleteLater()
        except RuntimeError:
            _closing.discard(window)
        except Exception as exc:
            log.warning("cannot close window %r: %s", tool_id, exc)
    _windows.clear()


def _show(window):
    window.show()
    window.raise_()
    window.activateWindow()
