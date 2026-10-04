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


# QColorDialog（DontUseNativeDialog）内部的英文部件文本 → 中文。
# 按原文匹配改写（去助记符 & 后精确匹配，冒号保留），找不到的部件
# 原样保留（Qt 版本间文案可能变化）。
_COLOR_TEXTS = {
    "Pick Screen Color": "选取屏幕颜色",
    "Basic colors": "基本颜色",
    "Custom colors": "自定义颜色",
    "Add to Custom Colors": "添加到自定义颜色",
    "Hue:": "色调:",
    "Sat:": "饱和:",
    "Val:": "明度:",
    "Red:": "红:",
    "Green:": "绿:",
    "Blue:": "蓝:",
    "Alpha channel:": "透明通道:",
}


def localize_color_dialog(dlg):
    """把非原生 QColorDialog 里的英文部件标签/按钮改成中文。"""
    for w in dlg.findChildren(QtWidgets.QWidget):
        if not isinstance(w, (QtWidgets.QLabel, QtWidgets.QPushButton,
                              QtWidgets.QToolButton)):
            continue
        text = w.text().replace("&", "")
        cn = _COLOR_TEXTS.get(text)
        if cn:
            w.setText(cn)
