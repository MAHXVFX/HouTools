"""对话框按钮中文化 helper。

Qt 标准按钮文字跟平台主题走（英文系统显示 OK/Cancel/Yes/No），本插件
UI 一律中文，按 QDialogButtonBox 的 role 改写。无 hou 依赖（smoke test
可直接 import）。

注意：QColorDialog / QFileDialog 在 Windows 上默认用原生对话框，按钮
不受此控制——需要改按钮文字的对话框须实例化调用并加
DontUseNativeDialog 选项（静态便利函数拿不到内部按钮对象）。
"""

from PySide6 import QtWidgets

_LABELS = None


def localize_buttons(dialog):
    """把 dialog 内所有 QDialogButtonBox 的标准按钮换成中文。"""
    global _LABELS
    if _LABELS is None:
        bb = QtWidgets.QDialogButtonBox
        _LABELS = {
            bb.Ok: "确认",
            bb.Yes: "确认",
            bb.Save: "确认",
            bb.Cancel: "取消",
            bb.No: "取消",
            bb.Close: "关闭",
        }
    for bbox in dialog.findChildren(QtWidgets.QDialogButtonBox):
        for role, text in _LABELS.items():
            btn = bbox.button(role)
            if btn is not None:
                btn.setText(text)


def warn(parent, title, text):
    """QMessageBox.warning 的中文化版本（单按钮「确认」）。"""
    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QtWidgets.QMessageBox.Ok)
    localize_buttons(box)
    box.exec_()
