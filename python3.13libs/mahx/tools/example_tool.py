"""Example tool - proves the hot-reload loop.

Edit WINDOW_TITLE below (or anything else in this file), save, then run
MAHX Tools -> Reload Modules (Dev) and reopen this tool: the change
appears without restarting Houdini. If the window was open during the
reload it is closed automatically and rebuilt with the new code.
"""

from PySide6 import QtWidgets

from mahx.core.log import get_logger
from mahx.ui import window_manager

log = get_logger("tools.example_tool")

WINDOW_TITLE = "MAHX Example Tool v1"  # <- 改这里试试热加载


def _build():
    window = QtWidgets.QWidget()
    window.setWindowTitle(WINDOW_TITLE)

    layout = QtWidgets.QVBoxLayout(window)

    label = QtWidgets.QLabel(
        "这是热加载示例工具。\n\n"
        "修改本文件的 WINDOW_TITLE 或任意界面代码，保存后执行\n"
        "菜单 MAHX Tools -> Reload Modules (Dev)，\n"
        "再重新打开本工具即可看到改动，无需重启 Houdini。"
    )
    layout.addWidget(label)

    close_button = QtWidgets.QPushButton("Close")
    close_button.clicked.connect(window.close)
    layout.addWidget(close_button)

    return window


def run():
    log.info("example tool opened")
    window_manager.open_window("example_tool", _build)
