"""Paste as Object Merge 快捷键设置。

主菜单 HouTools -> Paste Hotkey Settings 打开。捕获用户按键序列,
写入 Houdini 热键系统(会话内即时生效)并持久化到
`settings/hotkeys.json`(uiready 每次启动时应用,跨会话生效)。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QKeySequenceEdit, QLabel, QPushButton, QVBoxLayout,
)

from houtools.core.log import get_logger

logger = get_logger("tools.paste_hotkey")

CONTEXT = "h.pane.wsheet"
SYMBOL = "h.pane.wsheet.houtools_paste_as_object_merge"
DEFAULT_KEY = "Ctrl+Shift+V"
TOOL_ID = "paste_hotkey_settings"


def current_key() -> str:
    """当前生效的键位描述(自定义 > 会话内分配 > 默认)。"""
    from houtools.core import hotkeys

    custom = hotkeys.get_custom_key(SYMBOL)
    if custom:
        return custom
    try:
        import hou

        assigned = hou.hotkeys.assignments(CONTEXT, SYMBOL)
        if assigned:
            return assigned[0]
    except Exception:
        pass
    return DEFAULT_KEY


def apply_key(key: str) -> bool:
    """把键位写入 Houdini 会话并持久化;返回是否成功。"""
    import hou

    from houtools.core import hotkeys

    if not hou.hotkeys.addAssignment(CONTEXT, SYMBOL, key):
        return False
    hotkeys.set_custom_key(SYMBOL, key)
    return True


def run():
    """菜单入口:打开设置对话框(window_manager 单例)。"""
    import hou

    from houtools.ui import window_manager

    parent = None
    try:
        parent = hou.qt.mainWindow()
    except Exception:
        pass
    window_manager.open_window(TOOL_ID, lambda: _PasteHotkeyDialog(parent))


class _PasteHotkeyDialog(QDialog):
    """捕获按键序列并应用到 Paste as Object Merge。"""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("粘贴快捷键设置")
        self.setMinimumWidth(380)
        self.setStyleSheet(
            "QDialog { background-color: #1D1D20; }"
            "QLabel { color: #e0e0e0; background: transparent; }"
            "QKeySequenceEdit { background-color: #2d2d2d; color: #e0e0e0;"
            " border: 1px solid #3d3d3d; border-radius: 4px; padding: 5px 8px; }"
            "QPushButton { background-color: #2d2d2d; color: #e0e0e0;"
            " border: 1px solid #3d3d3d; border-radius: 4px; padding: 6px 16px; }"
            "QPushButton:hover { background-color: #3d3d3d; }"
            "QPushButton#okBtn { background-color: #0d6399; color: white;"
            " font-weight: bold; }"
            "QPushButton#okBtn:hover { background-color: #0e7bc9; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        tip = QLabel("按下 Paste as Object Merge 的新快捷键:")
        layout.addWidget(tip)

        self._editor = QKeySequenceEdit(current_key())
        layout.addWidget(self._editor)

        self._hint = QLabel("")
        self._hint.setStyleSheet("color: #888888; font-size: 11px;")
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("确定")
        ok_btn.setObjectName("okBtn")
        ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def _on_ok(self):
        import hou

        key = self._editor.keySequence().toString()
        if not key:
            self._hint.setText("未捕获到按键。")
            self._hint.setStyleSheet("color: #d1283e; font-size: 11px;")
            return
        if not apply_key(key):
            self._hint.setText(f"Houdini 拒绝了该键位:{key}")
            self._hint.setStyleSheet("color: #d1283e; font-size: 11px;")
            logger.warning("hotkey assignment rejected: %s", key)
            return
        logger.info("paste_as_object_merge hotkey set to %s", key)
        try:
            hou.ui.setStatusMessage(
                f"Paste as Object Merge 快捷键已设为:{key}",
                hou.severityType.ImportantMessage)
        except Exception:
            pass
        self.accept()
