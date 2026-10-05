"""HouTools 工具窗口统一显示字体（阿里妈妈数黑体 Bold，用户指定）。

经 QFontDatabase.addApplicationFont 加载私有字体（不装系统）；apply()
把字族刷到窗口整棵控件树。只允许在 HouTools 工具窗口上调用——不动
QApplication 全局字体，Houdini 自身 UI 不受影响。

⚠ 会话实测（fxhoudini 探针）：Houdini 在应用层给控件类设了自家字体
（SideFX Source Sans Pro），优先级压过 Qt 的父子字体继承——只 setFont
窗口时子控件仍是 SideFX 字体。所以必须逐控件显式 setFont（实测显式
设置能压过应用级类字体），动态创建的子控件（右键菜单、对话框、
Automation 运行时加的任务槽）经应用级 ChildAdded 过滤器补刷：新控件
只要挂在工具窗口子树内，整枝刷工具字体。

reload 安全：字体族名与过滤器安装标记挂 QApplication 动态属性（模块
重载不丢）；过滤器实例 parent 到 QApplication，随会话存活，旧实例
跑旧类代码但行为一致。
"""

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from houtools.core.log import get_logger

log = get_logger("ui.fonts")

_FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"
# 同族多字重放这里（Regular + Bold），全部登记后取公共字族——
# 字体本身有 Bold 字面，setBold 才真正可见
_FONT_FILES = ("AlimamaShuHeiTi-Bold.ttf",)
_FAMILY_PROP = "_houtools_tool_font_family"
_ENFORCER_PROP = "_houtools_font_enforcer_installed"
_WINDOW_PROP = "_houtools_tool_window"   # 工具窗口标记（enforcer 判定用）


def _tool_family():
    """返回工具字体族名；文件缺失/加载失败返回 None（调用方静默跳过）。"""
    app = QtWidgets.QApplication.instance()
    if app is not None:
        cached = app.property(_FAMILY_PROP)
        if cached:
            return cached
    family = None
    for name in _FONT_FILES:
        path = _FONTS_DIR / name
        if not path.is_file():
            log.warning("tool font missing: %s", path)
            continue
        font_id = QtGui.QFontDatabase.addApplicationFont(str(path))
        if font_id < 0:
            log.warning("tool font load failed: %s", path)
            continue
        families = QtGui.QFontDatabase.applicationFontFamilies(font_id)
        if not families:
            log.warning("tool font has no family: %s", path)
            continue
        if family is None:
            family = families[0]
        elif family not in families:
            log.warning("font family mismatch in %s: %s (expect %s)",
                        name, families, family)
    if family and app is not None:
        app.setProperty(_FAMILY_PROP, family)
    return family


def _stamp(widget, font):
    """widget 及其全部后代显式设字体（显式设置才能压过 Houdini 的
    应用级类字体；靠继承会被压回 SideFX 字体）。"""
    widget.setFont(font)
    for child in widget.findChildren(QtWidgets.QWidget):
        child.setFont(font)


def _tool_root_of(widget):
    """沿父链找标记过的工具窗口根；不在工具窗口子树内返回 None。"""
    w = widget
    while w is not None:
        if w.property(_WINDOW_PROP):
            return w
        w = w.parent()
    return None


class _ToolFontEnforcer(QtCore.QObject):
    """应用级 ChildAdded 监听：新控件挂在工具窗口子树内就整枝刷字体。

    装在 QApplication 上（一次装机全会话有效），以极低的每事件成本
    （一次 type 比较）兜底所有动态创建的控件。
    """

    def eventFilter(self, obj, event):
        if event.type() != QtCore.QEvent.ChildAdded:
            return False
        child = event.child()
        if not isinstance(child, QtWidgets.QWidget):
            return False
        root = _tool_root_of(obj)
        if root is not None:
            _stamp(child, root.font())
        return False


def _ensure_enforcer():
    """全局 ChildAdded 过滤器只装一次（标记挂 app，reload 不重复装）。"""
    app = QtWidgets.QApplication.instance()
    if app is None or app.property(_ENFORCER_PROP):
        return
    app.installEventFilter(_ToolFontEnforcer(app))
    app.setProperty(_ENFORCER_PROP, True)


def apply(widget):
    """把工具字体应用到 widget 子树；字体不可用时保持原字体。

    拷贝窗口当前字体只改字族——字号等沿用原设置（应用默认字号不变，
    只换脸）。子树逐控件显式设置 + 全局过滤器兜底后续动态控件。
    """
    family = _tool_family()
    if not family:
        return
    font = QtGui.QFont(widget.font())
    font.setFamily(family)
    widget.setProperty(_WINDOW_PROP, True)
    _stamp(widget, font)
    _ensure_enforcer()
