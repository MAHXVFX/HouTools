"""Registry for top-level tool windows.

Tools must create their windows through open_window() so the hot
reloader can close everything before reloading modules; otherwise open
windows would keep running pre-reload class code against stale state.
"""

import logging

from houtools.core.log import get_logger

log = get_logger("ui.window_manager")

# tool_id -> QWidget
_windows = {}


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
    """Close and delete every registered window (used before hot reload)."""
    for tool_id, window in list(_windows.items()):
        try:
            window.close()
            window.deleteLater()
        except RuntimeError:
            pass
        except Exception as exc:
            log.warning("cannot close window %r: %s", tool_id, exc)
    _windows.clear()


def _show(window):
    window.show()
    window.raise_()
    window.activateWindow()
