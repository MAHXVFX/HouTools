"""
Automation — 主界面（Python Panel 部件）
==========================================
以 .pypanel 载入 Houdini 面板体系（默认由菜单在浮动面板中打开）。
网络编辑器的节点拖放可直落入参数路径框——Houdini 面板体系内走原生
投递，不会像独立 QDialog 那样把待定工具快捷方式泄漏到 3D 视窗。
提供任务槽列表编辑、持久化保存、ExecutionEngine 集成。
"""

import functools
import logging
import os
import re
import subprocess
import sys
import weakref
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QComboBox,
    QLineEdit, QStackedWidget, QCheckBox, QWidget, QScrollArea, QSizePolicy,
    QGraphicsDropShadowEffect, QApplication, QMenu,
)
from PySide6.QtCore import Qt, Signal, QPoint, QSize, QRect, QRectF, QEvent, QTimer, QObject
from PySide6.QtGui import QColor, QIcon, QKeySequence, QShortcut, QPainter, QPixmap

try:
    from PySide6 import QtSvg
except ImportError:  # QtSvg 缺失时跳转按钮回退文字箭头（正常环境都有）
    QtSvg = None

from houtools.automation.data_manager import AutomationDataManager
from houtools.automation.task_types import (
    TaskItem,
    TaskType,
    ButtonClickParams,
    FlipbookParams,
    HomeAssistantParams,
    OpenDWParams,
)
from houtools.automation.execution_engine import ExecutionEngine
from houtools.automation.styles import STYLE_SHEET

from houtools.core.constants import PROJECT_ROOT
from houtools.core.log import get_logger
from houtools.ui import dialogs
from houtools.ui import fonts as tool_fonts
logger = get_logger("automation.window")


# ── 配置下拉图标(SVG,绝对路径避开 Houdini CWD 不可靠) ──────────
# 蓝色圆 + 下箭头 SVG,放在 ``python3.13libs/houtools/icons/``。
# 用 ``__file__`` 解析绝对路径后注入到 combo-level stylesheet,
# 不在 styles.py 写死(Houdini 启动 CWD 不固定,相对路径会失效)。
_MA_ICONS_DIR = Path(__file__).resolve().parent.parent / "icons"
_ICON_DROP_DOWN = _MA_ICONS_DIR / "drop_down_button.svg"
_CONFIG_COMBO_ICON_STYLE = f"""
QComboBox#configCombo::down-arrow {{
    image: url("{_ICON_DROP_DOWN.as_posix()}");
    width: 16px; height: 16px;
    margin-right: 4px;
}}
"""

_TOOLBAR_BTN_STYLE = (
    "QPushButton { background-color: #3a3a3e; color: #e0e0e0;"
    " border: 1px solid #55555a; padding: 6px 16px;"
    " border-radius: 4px; font-size: 13px; }"
    "QPushButton:hover { background-color: #46464b; }"
    "QPushButton:pressed { background-color: #0d6399; }"
)

# 数量行的 + / - 按钮:同工具栏按钮底色但更紧凑(固定 32x26,内容居中)
_SLOT_OP_BTN_STYLE = (
    "QPushButton { background-color: #3a3a3e; color: #e0e0e0;"
    " border: 1px solid #55555a; padding: 0;"
    " border-radius: 4px; font-size: 15px; font-weight: bold; }"
    "QPushButton:hover { background-color: #46464b; }"
    "QPushButton:pressed { background-color: #0d6399; }"
)


# ── Windows 文件名保留名(用于 _get_save_target_name 拒绝) ─────────
# 含 ``CON`` / ``PRN`` / ``AUX`` / ``NUL`` / ``COM1-9`` / ``LPT1-9``,
# 大小写不敏感(``text.upper() in _WINDOWS_RESERVED`` 比较)。
_WINDOWS_RESERVED = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
})


def _sanitize_config_name(text) -> str | None:
    """净化配置文件名 basename;非法返回 ``None``。

    规则(供"新建配置"对话框与 ``_get_save_target_name`` 共用):
      - 空 / 全空白 → ``None``
      - 末尾 ``.json`` → 剥后缀(容错用户带后缀输入)
      - 含 ``/`` 或 ``\\`` → ``None``(拒绝路径分隔符,避免破坏目录结构)
      - Windows 保留名(``CON``/``PRN``/``AUX``/``NUL``/``COM1-9``/``LPT1-9``,
        大小写不敏感)→ ``None``
      - NUL 字节 ``\x00`` → ``None``
      - 纯点号(全部由 ``.`` 组成)→ ``None``
    """
    text = (text or "").strip()
    if not text:
        return None
    if text.endswith(".json"):
        text = text[:-5].strip()
    if not text:
        return None
    # 安全检查:拒绝路径分隔符(防 ``../`` 或 ``C:\evil`` 等)
    if "/" in text or "\\" in text:
        return None
    # 拒绝 Windows 保留名(大小写不敏感)
    if text.upper() in _WINDOWS_RESERVED:
        return None
    # 拒绝 NUL 字节
    if "\x00" in text:
        return None
    # 拒绝纯点号(全部由 ``.`` 组成)
    if text.replace(".", "") == "":
        return None
    return text


# ── 日志 Tee 流 ─────────────────────────────────────────────

class _LogTee:
    """同时写入原始 stdout/stderr 和日志文件的 tee 流。

    用于执行期间捕获所有 print() 输出到日志文件。
    """

    def __init__(self, original, log_fn):
        self._original = original
        self._log_fn = log_fn

    def write(self, text):
        self._original.write(text)
        if text.strip():
            self._log_fn(text.rstrip("\n"))

    def flush(self):
        self._original.flush()


# ── 运行中引擎留痕 ───────────────────────────────────────────

# 执行中的引擎统一在此持有引用，线程 run() 返回(finished 信号)后才释放。
# 引擎无 Qt parent，若窗口销毁/字段置空后成为唯一引用之外的孤儿，GC 可能
# 销毁一个仍在运行的 QThread（"QThread: Destroyed while thread is still
# running"，可致崩溃）。取消后引擎会一直挂在 _run_deferred 的等待里直到
# 主线程空闲，这段时间必须留引用。
_LIVE_ENGINES: list["ExecutionEngine"] = []


def _release_engine(engine: "ExecutionEngine"):
    """线程真正结束后移除留痕。挂在 QThread.finished 上，不依赖窗口存活。"""
    try:
        _LIVE_ENGINES.remove(engine)
    except ValueError:
        pass


def _render_jump_icon() -> QIcon | None:
    """任务槽跳转按钮图标：官方 ``BUTTONS/jump.svg``（弧形箭头）。

    素材取自 Houdini 自带 ``$HFS/houdini/config/Icons/icons.zip``，与参数
    面板官方跳转按钮同源。经 QSvgRenderer 离屏渲染成 16/32 两档位图组装
    QIcon（不依赖 Qt 的 svg icon 插件，32 档供 2x DPR 取用）；QtSvg 缺失
    或 SVG 损坏时返回 None，按钮回退文字箭头。
    """
    if QtSvg is None:
        return None
    svg_path = Path(__file__).resolve().parent.parent / "icons" / "jump.svg"
    renderer = QtSvg.QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        logger.warning("jump.svg 无法渲染: %s", svg_path)
        return None
    icon = QIcon()
    for size in (16, 32):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        icon.addPixmap(pm)
    return icon


# ── Parm Path 编解码 ──────────────────────────────────────────

def _split_parm_path(parm_path: str) -> tuple[str, str]:
    """把 UI 里的参数路径(如 ``/obj/foo/aa/execute``)拆成 ``(node_path, parm_name)``。

    数据模型 ``ButtonClickParams`` 仍是 ``node_path`` + ``parm_name`` 两个
    字段(向后兼容 JSON),UI 层合并显示为单个 parmPath 输入框。本函数是
    合并显示到持久化的拆分半边。

    拆分规则:按最后一个 ``/`` 切,前面是节点路径,后面是参数名。
    边界:
    - 空字符串 → ``("", "")``
    - 无 ``/``(纯参数名)→ ``(原字符串, "")``
    - 末尾 ``/``(如 ``/obj/foo/``)→ ``("/obj/foo", "")``
    """
    # 先剥掉可能粘贴进来的 ``hou.parm('...')`` wrapper(与拖入路径框的
    # 解析同源),否则表达式里的 ``/`` 会把引号碎片当成节点路径
    parm_path = _extract_parm_path(parm_path)
    parm_path = parm_path.strip()
    if not parm_path:
        return "", ""
    if "/" not in parm_path:
        return parm_path, ""
    node_path, parm_name = parm_path.rsplit("/", 1)
    return node_path, parm_name


def _combine_parm_path(node_path: str, parm_name: str) -> str:
    """把 ``(node_path, parm_name)`` 拼回 UI 用的参数路径。逆运算见 ``_split_parm_path``。

    - 两边都非空 → ``f"{node_path}/{parm_name}"``
    - 一边空 → 直接返回另一边(避免多余 ``/``)
    - 都空 → 空串
    """
    if node_path and parm_name:
        return f"{node_path}/{parm_name}"
    return node_path or parm_name


# 匹配 ``hou.parm('...')`` 表达式(拖到 Python shell 的格式),DOTALL 容许多行。
# 单/双引号都接受,前后空白容错。
_PARM_PATH_RE = re.compile(
    r"""^\s*hou\s*\.\s*parm\s*\(\s*['"](.+?)['"]\s*\)\s*$""",
    re.DOTALL,
)


def _extract_parm_path(text: str) -> str:
    """从拖入的 Houdini 表达式文本里提取 parm 路径。

    Houdini 参数面板拖到 Python shell 会产生 ``hou.parm('/obj/foo/parm')``
    表达式;本函数把这种 wrapper 剥掉,只留纯路径。也接受纯路径输入(节点面板
    拖出可能只有 ``/obj/foo``)和空文本。

    返回:
    - ``hou.parm('/obj/foo/parm')`` → ``/obj/foo/parm``
    - ``hou.parm("/obj/foo/parm")`` → ``/obj/foo/parm``(双引号)
    - ``  hou.parm('/obj/foo/parm')  `` → ``/obj/foo/parm``(前后空白)
    - ``/obj/foo``(纯文本)→ 原样返回
    - ``""`` / 纯空白 → ``""``
    - 解析失败(不匹配且非纯路径)→ 原样返回(让 UI 显示,让用户修正)
    """
    text = text.strip()
    if not text:
        return ""
    m = _PARM_PATH_RE.match(text)
    if m:
        return m.group(1)
    return text


# ── Python Panel 接入 ────────────────────────────────────────

INTERFACE_NAME = "Automation"
"""Automation.pypanel 中 <interface name> 的名字。"""


def create_panel_widget(parent=None):
    """创建 Python Panel 界面部件（.pypanel 的 onCreateInterface 入口）。"""
    widget = AutomationWindow(parent)
    # 登记弱引用供单实例复用:弱引用不延长部件生命周期,面板销毁后自动失效
    _PANEL_REFS.append(weakref.ref(widget))
    return widget


_PANEL_REFS: list = []
"""本会话创建过的 AutomationWindow 弱引用(单实例复用注册表)。

热重载会重建本模块 → 注册表清空,因此桌面接口名扫描作为兜底路径。
"""


def _iter_live_panels():
    """返回注册表中仍存活的 AutomationWindow(顺带清理失效弱引用)。"""
    alive = []
    for ref in _PANEL_REFS:
        widget = ref()
        if widget is None:
            continue
        try:
            widget.window()  # C++ 对象已销毁会抛 RuntimeError
        except RuntimeError:
            continue
        alive.append((ref, widget))
    _PANEL_REFS[:] = [ref for ref, _ in alive]
    return [widget for _, widget in alive]


def _activate_existing_panel(desktop) -> bool:
    """前置激活已存在的本工具面板；回收隐藏残留。返回是否发生了激活。

    两条通道:
      1. Qt 注册表 —— 本会话经 create_panel_widget 创建的实例,浮动的
         和停靠的都算,不依赖任何 HOM 枚举;
      2. HOM 接口名扫描 —— 兜底热重载后注册表为空的情况,按浮动面板里
         PythonPanel tab 的 activeInterface 名字认领;Qt 窗口包装失败时
         宁可不动作也绝不误关。隐藏的残留面板(关闭后仍挂桌面)在此回收。
    """
    import hou  # Houdini-only(paneTabType 枚举);按约定放函数内,保证无头可导入

    activated = False

    for widget in _iter_live_panels():
        try:
            if not widget.isVisible():
                continue
            top = widget.window()
            if not activated:
                if top.isMinimized():
                    top.showNormal()
                top.raise_()
                top.activateWindow()
                activated = True
            # 其余可见重复实例不在此关闭:停靠实例的 window() 可能是
            # Houdini 主窗,误关代价太大,交给 HOM 路径/用户手动处理
        except RuntimeError:
            continue

    for fp in desktop.floatingPanels():
        try:
            tab = fp.paneTabOfType(hou.paneTabType.PythonPanel)
            if tab is None:
                continue
            iface = tab.activeInterface()
            if iface is None or iface.name() != INTERFACE_NAME:
                continue
            win = _qt_floating_window(fp)
            if win is None:
                continue
            if win.isVisible():
                if not activated:
                    if win.isMinimized():
                        win.showNormal()
                    win.raise_()
                    win.activateWindow()
                    activated = True
                else:
                    fp.close()  # 重复的浮动实例
            else:
                fp.close()  # 关闭后仍挂桌面的隐藏残留
        except RuntimeError:
            continue

    return activated


def _qt_floating_window(panel):
    """返回浮动面板的原生窗口 QWidget；拿不到返回 None。

    三路尝试(前两路失败会打 debug 日志留痕,便于排查 pre-hide 不生效):
      1. hou.qt.floatingPanelWindow —— 官方桥;
      2. 未公开的 _qtParentWindow 指针手工包装成 QWidget;
      3. 按窗口标题在顶层部件里扫描 —— 面板已在 open_floating_panel
         中 setName(INTERFACE_NAME),Houdini 标题格式 "Houdini FX - <名>"。
    """
    try:
        import hou
        return hou.qt.floatingPanelWindow(panel)
    except Exception as exc:
        logger.debug("hou.qt.floatingPanelWindow failed: %s", exc)

    try:
        import shiboken6
        from PySide6.QtWidgets import QWidget

        ptr = panel._qtParentWindow()
        if ptr:
            return shiboken6.wrapInstance(int(ptr), QWidget)
    except Exception as exc:
        logger.debug("_qtParentWindow fallback failed: %s", exc)

    try:
        from PySide6.QtWidgets import QApplication

        for w in QApplication.topLevelWidgets():
            if w.windowTitle().endswith(INTERFACE_NAME):
                return w
    except Exception as exc:
        logger.debug("top-level title scan failed: %s", exc)
    return None


def _apply_centered_position(panel, main, frame_w, frame_h):
    """把面板写到"主窗口外框正中"。

    setPosition 的坐标语义(H22 实测双轴标定):X 与 Qt 一致(向右);
    **Y 轴向上、原点在屏幕底边** —— 参数是窗口底边距屏幕底部的高度,
    与 Qt 的 y 向下相反。直接传 Qt 的 y 会得到上下镜像的位置
    (历次"偏上 / 右上角"的根因)。frame_w/h 为窗口外框尺寸。
    """
    try:
        screen_h = main.windowHandle().screen().geometry().height()
        c = main.frameGeometry().center()
        panel.setPosition((
            c.x() - frame_w // 2,
            screen_h - c.y() - frame_h // 2,
        ))
        return True
    except Exception:
        return False


class _PanelCenterFilter(QObject):
    """一次性过滤器:本工具浮动面板的顶层窗口首次 Show 时,把面板移到
    主窗口正中。

    背景(MCP 现场实测,H22):createFloatingPanel 返回时原生窗口尚未
    创建、不可见,HOM 的 position()/size() 初始为 (-1,-1);Houdini 在
    自动 show 环节才建窗口并摆到"上次关闭的位置" —— 发生在
    open_floating_panel 返回之后,创建期的 setPosition 会被覆盖。在
    Show 事件阶段(paint 尚未发生)同步 setPosition,窗口第一次上屏
    就在居中位置,消除"先旧位置、再跳中间"的闪烁。
    """

    def __init__(self, panel):
        super().__init__()
        self._panel = panel

    def eventFilter(self, obj, event):  # noqa: N802 — Qt 命名约定
        if event.type() != QEvent.Show:
            return False
        try:
            if not obj.windowTitle().endswith(INTERFACE_NAME):
                return False
        except RuntimeError:
            return False  # C++ 对象已销毁
        try:
            import hou
            main = hou.qt.mainWindow()
            fg = obj.frameGeometry()
            if main is not None and fg.width() > 0 and fg.height() > 0:
                _apply_centered_position(
                    self._panel, main, fg.width(), fg.height())
        except Exception:
            pass  # 定位失败:保持 Houdini 摆位,150ms 兜底校正仍在
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        return False  # 不吞事件,show 流程照常(位置已被改为居中)


def open_floating_panel():
    """在 Houdini 浮动面板中打开 Automation（Python Panel 界面）。

    用 Houdini 管理的浮动面板而非独立 QDialog：网络编辑器的节点拖放
    对 Houdini 面板体系走内部投递，不会像跨入独立原生窗口那样把待定
    的工具快捷方式泄漏到 3D 视窗。

    **单实例**：已存在本工具面板时菜单点击只前置激活，绝不另开新面板
    —— 重复创建会让 panelN 递增，且隐藏面板带着部件树与应用级事件
    过滤器越积越多、打开越来越卡。复用激活时返回 None，新建时返回
    FloatingPanel。新建的面板固定命名为 ``Automation``（显示在窗口
    标题，替代 Houdini 递增的 panelN 编号）。
    """
    import hou

    # .pypanel 定义每个会话只需安装一次(热重载只换 py 模块,不动注册表)
    if hou.pypanel.interfaceByName(INTERFACE_NAME) is None:
        interface_file = PROJECT_ROOT / "python_panels" / "Automation.pypanel"
        hou.pypanel.installFile(str(interface_file))
        if hou.pypanel.interfaceByName(INTERFACE_NAME) is None:
            raise ValueError(
                f"Python Panel 接口注册失败: {INTERFACE_NAME} ({interface_file})"
            )

    desktop = hou.ui.curDesktop()
    if _activate_existing_panel(desktop):
        return None

    panel = desktop.createFloatingPanel(
        hou.paneTabType.PythonPanel,
        size=(620, 540),
        # SWIG 签名为 char const*：这里传接口名而非接口对象
        python_panel_interface=INTERFACE_NAME,
    )
    try:
        panel.setName(INTERFACE_NAME)
    except hou.OperationFailed:
        pass  # 命名失败仅影响标题显示,不阻塞打开

    # 打开位置:主窗口正中(对齐 Video to Sequence 的 QDialog 落点)。
    # 机制见 _PanelCenterFilter docstring(MCP 现场实测):窗口在 Houdini
    # 的 show 环节才创建并摆到上次位置,必须靠 Show 事件拦截定位;
    # 创建后立即写一次状态层(修正 size()=-1 的回落),0ms/150ms 定时器
    # 兜底防 Houdini 更晚的摆位。
    from PySide6.QtCore import QTimer

    def _center_panel():
        """把面板摆到主窗口外框正中;窗口未建时按创建尺寸近似。"""
        try:
            main = hou.qt.mainWindow()
            if main is None:
                return
            win = _qt_floating_window(panel)
            if win is not None:
                fg = win.frameGeometry()
                if fg.width() > 0 and fg.height() > 0:
                    _apply_centered_position(
                        panel, main, fg.width(), fg.height())
                    return
            w, h = panel.size()
            if int(w) <= 0 or int(h) <= 0:
                w, h = 620, 578  # 显示前 size() 无效:创建尺寸+标题栏近似
            _apply_centered_position(panel, main, int(w), int(h))
        except Exception:
            pass  # 面板已销毁或定位失败:保持 Houdini 的摆位

    _center_panel()  # 状态层先写好,供 Houdini show 时采用

    app = QApplication.instance()
    if app is not None:
        app.installEventFilter(_PanelCenterFilter(panel))

    QTimer.singleShot(0, _center_panel)
    QTimer.singleShot(150, _center_panel)
    return panel


# ── 任务槽手柄 ────────────────────────────────────────────────

class _SlotHandle(QLabel):
    """任务槽"手柄"标签,即序号所在区域。

    设计意图:
    - 序号区不只是显示数字,还是一个**可交互的拖动手柄 + 选中触发器**
    - 左键单击 → 选中该槽(高亮)
    - 左键按住 + 拖动 → 重排任务顺序
    - 拖动时 cursor 切换 OpenHand → ClosedHand

    通过 3 个 Signal 把事件转发给 ``AutomationWindow`` 处理,
    避免在 widget 内部维护复杂状态。

    Signals:
        handlePressed(slot_widget, global_pos): 左键按下
        handleMoved(slot_widget, global_pos): 鼠标移动(无论是否按下都发,接收方按需过滤)
        handleReleased(slot_widget, global_pos): 左键松开
    """

    handlePressed = Signal(object, object)  # slot_widget, QPoint
    handleMoved = Signal(object, object)
    handleReleased = Signal(object, object)

    def __init__(self, slot_widget: QWidget) -> None:
        """``slot_widget`` 同时也是 Qt parent(单参避免调用方传错)。

        设计:手柄的 Qt parent == 任务槽卡片自身,故 ``self.parentWidget()`` 即槽。
        唯一参数 ``slot_widget`` 显式声明这个意图,杜绝把"序号文字"误传成槽引用。
        """
        super().__init__(slot_widget)
        self._slot = slot_widget
        self.setObjectName("taskSlotHandle")
        self.setCursor(Qt.OpenHandCursor)
        self.setMouseTracking(True)
        # 手柄可聚焦：点击选中后 Delete 直达本部件的 keyPressEvent
        self.setFocusPolicy(Qt.ClickFocus)

    def event(self, e) -> bool:  # noqa: N802
        # 焦点部件接受 ShortcutOverride 后，按键会绕过所有全局快捷键
        # （包括 Houdini 的 Delete 拦截）直达 keyPressEvent
        if e.type() == QEvent.ShortcutOverride and e.key() == Qt.Key_Delete:
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, event) -> None:  # noqa: N802 — Qt 命名约定
        window = getattr(self, "_owner_window", None)
        if event.key() == Qt.Key_Delete and window is not None \
                and window._selected_index is not None:
            window._remove_slot(window._selected_index)
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.grabMouse()  # 捕获所有鼠标事件,确保 drag 过程不出丢 release
            self.setCursor(Qt.ClosedHandCursor)
            self.handlePressed.emit(self._slot, event.globalPos())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        # 注意:无论是否按下都发,接收方按 _drag_active 过滤
        self.handleMoved.emit(self._slot, event.globalPos())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            if self.mouseGrabber() == self:
                self.releaseMouse()
            self.setCursor(Qt.OpenHandCursor)
            self.handleReleased.emit(self._slot, event.globalPos())
        super().mouseReleaseEvent(event)


class _NoWheelComboBox(QComboBox):
    """QComboBox 子类:屏蔽 hover 滚轮改值,让事件穿透到父滚动区。

    默认 QComboBox.wheelEvent 会循环选项,误触率高 —— 用户想滚动
    任务列表时一滚就改了任务类型。直接 ``event.ignore()``(不调 super)
    让 Qt 把事件回传给父 widget,QScrollArea 自然接管滚动。
    """

    def wheelEvent(self, event) -> None:  # noqa: N802 — Qt 命名约定
        event.ignore()


class _ParmPathLineEdit(QLineEdit):
    """QLineEdit 子类:接受 Houdini 参数拖入,自动填充 parm 路径。

    行为对齐 Houdini Python shell:从参数面板拖按钮到本控件,等价于
    ``hou.parm('/obj/foo/parm')`` 表达式,本控件识别后只填纯路径
    ``/obj/foo/parm``(剥 wrapper)。也接受普通文本拖入(节点面板拖出
    可能只有 ``/obj/foo``,原样填入让用户补 parm)。

    关键:走 ``setAcceptDrops(True)`` + override ``dragEnterEvent`` /
    ``dragMoveEvent`` / ``dropEvent``。文本提取用 ``_extract_parm_path``
    module-level helper(单/双引号 + 空白容错)。

    **dragEnter 全接受策略**:Houdini 参数拖动用自定义 MIME(类似
    ``application/x-houdini-parm``),``hasText()`` / ``hasUrls()`` 都 False,
    严格检查会让鼠标显示禁止图标。改为 dragEnter 一律 acceptProposedAction,
    文本提取下沉到 dropEvent,失败才 ignore —— 用户体验更顺(光标始终是
    "可放下"图标,即使最终 drop 没改文本也不报错)。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 — Qt 命名约定
        # 全接受:具体能否提取出 parm 路径交给 dropEvent 判断
        event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 — Qt 命名约定
        # dragEnter 接受后,dragMove 也得 accept,否则 drop 不会触发
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 — Qt 命名约定
        mime = event.mimeData()
        text = _extract_drag_text(mime)
        path = _extract_parm_path(text) if text else ""
        if path:
            self.setText(path)
        else:
            # 提取失败不再静默:记下格式名清单,便于排查 Houdini 自定义 MIME 变化
            logger.info("参数拖放未提取到路径, MIME formats: %s",
                        [str(f) for f in mime.formats()])
        # 无论是否命中路径，都向拖放源上报"取消"而非"成功落地"：实测
        # Houdini 收到"节点拖放成功落入外部窗口"时，会把视窗待定的工具
        # 快捷方式泄漏到 3D 视窗（Houdini 内部面板、拒绝拖放的外部程序
        # 如记事本均无此问题）。数据已读取完毕，上报取消不影响填值。
        event.ignore()


def _plausible_text(raw: bytes) -> bool:
    """判断 MIME 原始字节是否"像文本"（按内容过滤，不看格式名）。

    Windows 上 Houdini 的自定义剪贴板格式经 Qt 暴露为
    ``application/x-qt-windows-mime;value="<原名>"``，原名不可预知，
    无法按前缀白名单筛选；二进制载荷（图片/颜色等）靠内容特征排除：
    含 NUL、过长或可打印率低于 90% 都不算文本。
    """
    if not raw or len(raw) > 65536 or b"\x00" in raw:
        return False
    text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        return False
    sample = text[:512]
    printable = sum(1 for ch in sample if ch.isprintable() or ch in "\t\r\n")
    return printable / len(sample) >= 0.9


def _extract_drag_text(mime) -> str:
    """从 ``QMimeData`` 抽取可读文本,兼容 Houdini 自定义 MIME。

    优先级:
    1. ``text/plain``(普通文本 / 外部文本拖入)
    2. ``text/uri-list``(文件 URL)
    3. 其余任意格式按内容判断(``_plausible_text``):Houdini 拖参数用
       自定义 MIME(格式名经 Qt 的 windows-mime 映射不可预知),内容是
       ``hou.parm('...')`` 这样的 UTF-8 文本 —— 曾按
       ``application/x-houdini-`` 前缀白名单筛选,但 Qt 暴露的格式名
       不带该前缀,参数拖放因此静默失效(d4d8e1e 引入的回归)。
    """
    if mime.hasText():
        return mime.text()
    if mime.hasUrls():
        urls = mime.urls()
        if urls:
            return urls[0].toString()
    for fmt in mime.formats():
        try:
            raw = bytes(mime.data(fmt))
        except Exception:  # noqa: BLE001 — 单格式失败不阻塞其余格式
            continue
        if not _plausible_text(raw):
            continue
        data = raw.decode("utf-8", errors="ignore").strip()
        if data:
            return data
    return ""


def _frame_node_in_editor(editor, node):
    """在 network editor 中取景单个节点（等价按 F 的效果）。

    H22 运行时没有 frameSelection/homeToSelection（文档列出但存根缺失），
    且 setCurrentNode 的 pick 机制会异步清空选择，因此不依赖选择状态：
    用 itemRect 取节点在该编辑器中的精确布局矩形（自适应节点大小，
    之前固定边距版对大节点会裁出视野导致居中时好时坏），四周外扩约
    35% 后交给 setVisibleBounds。

    set_center_when_scale_rejected 必须为 True：max_scale 默认上限 100，
    当前视图缩放状态使目标缩放超限时，False（默认）会让整个调用被
    拒绝、什么都不发生——这正是"视图移远后无法居中"的原因；True 则
    在缩放被拒时仍把视图中心平移到节点上，保证每次点击都居中。
    """
    try:
        import hou
        rect = editor.itemRect(node)
        mn = rect.min()
        sz = rect.size()
        # 边距取节点尺寸的 1 倍（节点约占画面 1/3），视野留白更宽松；
        # 矩形越大所需放大倍率越小，也更不容易触发 max_scale 缩放上限
        pad_x = max(sz[0] * 1.0, 2.0)
        pad_y = max(sz[1] * 1.0, 1.5)
        editor.setVisibleBounds(
            hou.BoundingRect(
                mn[0] - pad_x, mn[1] - pad_y,
                mn[0] + sz[0] + pad_x, mn[1] + sz[1] + pad_y,
            ),
            set_center_when_scale_rejected=True,
        )
    except Exception as exc:  # noqa: BLE001 - 取景失败不影响跳转与选中
        logger.warning("frame node %s failed: %s", node, exc)


class AutomationWindow(QWidget):
    """Automation 主界面（Python Panel 部件）。

    包含可动态增删的任务槽列表、工具栏（Start / Auto Fill / Clear / + / -）、
    数据持久化加载/保存以及 ExecutionEngine 后台执行集成。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Automation")
        self.setMinimumSize(600, 450)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet(STYLE_SHEET)
        tool_fonts.apply(self)   # 工具统一字体（子树继承，Panel/浮窗共用本类）

        # Delete 用 QShortcut 认领（WidgetWithChildren 上下文优先于
        # Houdini 的全局快捷键）；焦点在输入框内时 QLineEdit 自己会
        # 接受 ShortcutOverride，快捷键自动让路给文本编辑
        delete_sc = QShortcut(QKeySequence.StandardKey.Delete, self)
        delete_sc.setContext(Qt.WidgetWithChildrenShortcut)
        delete_sc.activated.connect(self._on_delete_shortcut)

        # ── 状态 ──
        self._slot_widgets: list[QWidget] = []
        # 平行于 _slot_widgets 的 handle 引用列表,在 _create_slot_widget 时
        # 一次性存到 slot._handle,之后 _add_slot 同步 append。替掉原来 3 处
        # (renumber / index_at_global_y) 的 findChild 热路径,O(N×M) → O(N)
        self._slot_handles: list[QLabel] = []
        self._running = False
        self._cancel_requested = False  # 已请求停止、引擎尚未真正退出
        self._engine: ExecutionEngine | None = None

        # 选中 + 拖动状态
        self._selected_index: int | None = None  # 单选,None=无选中
        self._last_selected_index: int | None = None  # 差量更新 _update_selection_style 用
        self._drag_active: bool = False  # 拖动是否已激活(超过阈值)
        self._drag_source_index: int | None = None  # 拖动起点槽索引
        self._drag_press_pos = None  # type: QPoint | None  # 拖动按下时的全局坐标
        self._drag_threshold: int = 5  # 像素,超过才认作拖动

        # 配置下拉相关状态:当前加载的配置文件 basename(无 .json 后缀)。
        # 初始默认 ``Automation``(保留向后兼容)。用户从下拉选其它
        # 配置后会被 ``_on_config_changed`` 更新;Start 保存后会被
        # ``_save_data`` 更新(用当前 combo 文本)。
        self._current_config_name: str = "Automation"

        # 设置项:日志输出到磁盘
        self._log_to_disk_enabled: bool = False

        self._build_ui()
        self._load_data()
        self._load_settings()
        self._install_app_event_filter()

        # 打开DW 的应用级配置(settings/Automation_Config.json)——
        # gitignored 运行时数据,意外缺失时在此按默认值兜底补建
        AutomationDataManager.ensure_dw_config()

    def _install_app_event_filter(self):
        """把本窗口挂为 QApplication 级事件过滤器（Delete 修复的实现）。

        过滤器用于消费"Houdini 清空部件焦点后直接投递到原生 QWindow 的
        Delete 键"；装卸随 show/hide 生命周期绑定（见 showEvent/hideEvent），
        隐藏/残留面板不参与全应用事件过滤。
        """
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def showEvent(self, event):
        # 过滤器生命周期绑定到可见性:隐藏/残留面板(关闭后仍挂桌面的
        # 浮动面板)绝不参与全应用事件过滤,避免逐次累积拖慢整个界面
        super().showEvent(event)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def hideEvent(self, event):
        super().hideEvent(event)
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    def _is_own_native_window(self, obj) -> bool:
        """判断事件源 ``obj``(QWindow) 是否为本面板专属的原生窗口。

        旧实现 ``obj is self.window().windowHandle()`` 在停靠形态下失效:
        ``self.window()`` 是 Houdini 主窗,导致主窗收到的 Delete(部件焦点
        被 Houdini 清空时)被误认领成"删除本面板选中任务"。现改为在顶层
        部件里找 windowHandle 与 obj 相同者,要求:
          - 其部件树包含本面板(``isAncestorOf``,跨窗口返回 False);
          - 窗口标题以本工具接口名结尾(浮动面板标题格式
            "Houdini FX - <接口名>",与 _PanelCenterFilter 同款判定)——
            主窗等其他共享窗口一律不认领(Delete 归属不明,宁可不动作)。
        """
        app = QApplication.instance()
        if app is None:
            return False
        for w in app.topLevelWidgets():
            if w.windowHandle() is not obj or not w.isAncestorOf(self):
                continue
            try:
                return w.windowTitle().endswith(INTERFACE_NAME)
            except RuntimeError:
                continue
        return False

    def eventFilter(self, obj, event):
        # 过滤器挂在 QApplication 上会收到所有对象的事件；面板开关过程
        # 中部分 QWindow 的 C++ 对象已销毁，透传时 PySide 抛 RuntimeError
        # ——过滤器自身绝不能向外抛异常
        try:
            etype = event.type()
            # 非 Delete 相关事件早退:过滤器挂在 QApplication 上,全应用
            # 每个事件都会进来,别为无关事件创建 lambda / 走后续判断
            if etype not in (QEvent.KeyPress, QEvent.ShortcutOverride):
                return super().eventFilter(obj, event)
            if getattr(event, "key", lambda: None)() == Qt.Key_Delete \
                    and self._selected_index is not None \
                    and self._is_own_native_window(obj):
                # Houdini 浮动面板会在鼠标释放后清空部件焦点（focus=None），
                # 此时 Delete 直接投递给原生 QWindow，任何部件处理器都收
                # 不到。若该原生窗口是本面板专属的浮动窗口，视为"删除选定
                # 任务"并在源头消费；停靠形态下事件源是 Houdini 主窗，
                # 归属不明，不认领（见 _is_own_native_window）。
                if etype == QEvent.KeyPress:
                    self._remove_slot(self._selected_index)
                    return True
                if etype == QEvent.ShortcutOverride:
                    return True  # 阻止 Houdini 全局快捷键先吃掉 Delete
            return super().eventFilter(obj, event)
        except RuntimeError:
            return False

    # ── UI 构建 ────────────────────────────────────────────

    def _build_ui(self):
        """构建完整 UI：工具栏 + 滚动槽列表。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── 工具栏第一行：配置、Start、Auto Fill、Clear ──
        toolbar1 = QHBoxLayout()
        toolbar1.setSpacing(4)

        # 配置下拉:放在 start 按钮**前方**,对齐用户"先选配置
        # 再点 Start"的工作流。下拉**只读**列出配置目录下所有现存
        # .json basename(无后缀) —— 不支持在框内键入:Houdini 浮动面板
        # 会在鼠标释放后清空部件焦点,内联编辑不可靠;新建配置统一走
        # 旁边"新建"按钮的输入对话框(独立模态,键盘行为可靠)。
        #
        # 前缀标签 "配置:" 显式标识控件用途(暗色主题下避免与裸 QComboBox
        # 混淆),标签和 combo 配套使用,不可拆分。
        self._config_label = QLabel("配置:")
        self._config_label.setObjectName("configLabel")
        self._config_label.setStyleSheet(
            "color: #0d6399; font-weight: bold; background: transparent;"
        )

        self._config_combo = QComboBox()
        self._config_combo.setObjectName("configCombo")
        self._config_combo.setMinimumWidth(160)
        self._config_combo.setToolTip(
            "选择配置;新建配置用旁边的\"新建\"按钮"
        )
        self._config_combo.currentIndexChanged.connect(self._on_config_changed)
        # 注入 SVG 下拉图标(combo-level stylesheet 覆盖全局,
        # 路径用绝对 URL 避开 Houdini CWD 不可靠)
        self._config_combo.setStyleSheet(_CONFIG_COMBO_ICON_STYLE)

        # "新建"配置:输入名字即切换为当前配置,点"执行"时落盘创建
        new_config_btn = QPushButton("新建")
        new_config_btn.setObjectName("configNewBtn")
        new_config_btn.setToolTip(
            "新建配置:输入名称并确认后,当前配置切换为新名,\n"
            "点\"执行\"时任务保存到该新配置(文件不存在则创建)"
        )
        new_config_btn.setStyleSheet(_TOOLBAR_BTN_STYLE)
        new_config_btn.clicked.connect(lambda: self._on_new_config())

        self._start_btn = QPushButton("执行")
        self._start_btn.setObjectName("startBtn")
        # 关闭 autoDefault / default:避免按 Enter 被转成 Start 触发,
        # 要求 Start 只能**手动鼠标点击**
        self._start_btn.setAutoDefault(False)
        self._start_btn.setDefault(False)
        # 用 lambda 包装避免 Qt clicked(bool) 信号把 False 当作 data 参数传入
        self._start_btn.clicked.connect(lambda: self._on_start())

        auto_fill_btn = QPushButton("自动填充")
        auto_fill_btn.setObjectName("autoFillBtn")
        auto_fill_btn.clicked.connect(lambda: self._on_auto_fill())

        clear_btn = QPushButton("清空")
        clear_btn.setObjectName("clearBtn")
        clear_btn.clicked.connect(lambda: self._on_clear())

        settings_btn = QPushButton("设置")
        settings_btn.setObjectName("settingsBtn")
        settings_btn.clicked.connect(lambda: self._open_settings())
        # pane 内 Houdini 全局样式表会压过窗口级类型选择器，这三个按钮
        # 用部件级内联样式（Qt 中优先级最高）保证底色可见
        for btn in (auto_fill_btn, clear_btn, settings_btn):
            btn.setStyleSheet(_TOOLBAR_BTN_STYLE)

        # 顺序:配置 → 新建 → start → auto fill → clear   <stretch>   设置
        toolbar1.addWidget(self._config_label)
        toolbar1.addWidget(self._config_combo)
        toolbar1.addWidget(new_config_btn)
        toolbar1.addWidget(self._start_btn)
        toolbar1.addWidget(auto_fill_btn)
        toolbar1.addWidget(clear_btn)
        toolbar1.addStretch()
        toolbar1.addWidget(settings_btn)

        layout.addLayout(toolbar1)

        # ── 工具栏第二行：任务数量、+、- ──
        toolbar2 = QHBoxLayout()
        toolbar2.setSpacing(4)

        # 任务数量输入框
        self._task_count_label = QLabel("数量:")
        self._task_count_label.setStyleSheet(
            "color: #cccccc; background: transparent;"
        )
        self._task_count_input = QLineEdit()
        self._task_count_input.setObjectName("taskCountInput")
        self._task_count_input.setFixedWidth(50)
        self._task_count_input.setAlignment(Qt.AlignCenter)
        self._task_count_input.setToolTip("输入任务数量后按 Enter 或点击空白处确认")
        self._task_count_input.returnPressed.connect(self._on_task_count_changed)
        self._task_count_input.editingFinished.connect(self._on_task_count_changed)
        # 让内部编辑器只有点击时才激活，防止自动聚焦导致误输入
        self._task_count_input.setFocusPolicy(Qt.ClickFocus)

        # pane 内 Houdini 全局样式表会压过窗口级 #addBtn/#removeBtn 规则,
        # 同样用部件级内联样式保证底色可见
        add_btn = QPushButton("+")
        add_btn.setObjectName("addBtn")
        add_btn.setFixedSize(32, 26)
        add_btn.setStyleSheet(_SLOT_OP_BTN_STYLE)
        add_btn.clicked.connect(lambda: self._add_slot())

        remove_btn = QPushButton("-")
        remove_btn.setObjectName("removeBtn")
        remove_btn.setFixedSize(32, 26)
        remove_btn.setStyleSheet(_SLOT_OP_BTN_STYLE)
        remove_btn.clicked.connect(lambda: self._remove_slot())

        # 顺序:数量标签 → 数量输入框 → + → -   <stretch>
        toolbar2.addWidget(self._task_count_label)
        toolbar2.addWidget(self._task_count_input)
        toolbar2.addWidget(add_btn)
        toolbar2.addWidget(remove_btn)
        toolbar2.addStretch()

        layout.addLayout(toolbar2)

        # ── 槽列表滚动区域 ──
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._slot_container = QWidget()
        self._slot_layout = QVBoxLayout(self._slot_container)
        self._slot_layout.setContentsMargins(0, 0, 0, 0)
        self._slot_layout.setSpacing(6)
        self._slot_layout.addStretch()  # 将槽推至顶部

        self._scroll_area.setWidget(self._slot_container)
        layout.addWidget(self._scroll_area)

    def _create_slot_widget(self, index: int, data: dict | None = None) -> QWidget:
        """创建一个任务槽控件。"""
        slot = QWidget()
        slot.setObjectName("taskSlot")
        slot.setAutoFillBackground(True)
        # 锁高：滚动区 setWidgetResizable(True) 会把容器拉到视口大小，
        # 默认 Preferred 会让 slot 在槽数少时撑满空间。Fixed 强制 sizeHint (~42px)
        slot.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        hbox = QHBoxLayout(slot)
        hbox.setContentsMargins(8, 6, 8, 6)
        hbox.setSpacing(8)

        # ── 序号手柄(可点击选中 + 拖动重排)──
        # 关键:第一个位置参数是"任务槽"本身(同时也是 Qt parent),不是序号文字。
        # 之前误传 str(index+1) 导致 handlePressed 发出的 slot 是字符串,
        # handler 里 list.index(str) 抛 ValueError 提前返回,选中/拖动全失效。
        idx_label = _SlotHandle(slot)
        idx_label._owner_window = self
        idx_label.setText(str(index + 1))
        idx_label.setFixedWidth(32)
        idx_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        idx_label.handlePressed.connect(self._on_handle_pressed)
        idx_label.handleMoved.connect(self._on_handle_moved)
        idx_label.handleReleased.connect(self._on_handle_released)
        # 把 handle 引用存到 slot 上,让 _add_slot 能 append 到 _slot_handles
        # 替掉 findChild 热路径;handle 生命周期 = slot 生命周期,无泄漏
        slot._handle = idx_label

        # ── 类型下拉框 ──
        # 收短(160→120),让 node_path 获得更多横向空间
        # 用 _NoWheelComboBox 替 QComboBox,屏蔽 hover 滚轮循环选项
        combo = _NoWheelComboBox()
        combo.setObjectName("taskType")
        # 顺序即任务类型索引: 0按钮点击 1Flipbook 2Webhook 3打开DW,
        # 与 stacked 页序号一一对应(Flipbook 除外,见下方占位页)
        combo.addItems(["按钮点击", "Flipbook", "Webhook", "打开DW"])
        combo.setFixedWidth(120)

        # ── 参数区域（QStackedWidget） ──
        stacked = QStackedWidget()
        stacked.setObjectName("paramsStacked")

        # Page 0: 按钮点击
        page0 = QWidget()
        p0_layout = QHBoxLayout(page0)
        p0_layout.setContentsMargins(0, 0, 0, 0)
        p0_layout.setSpacing(4)
        parm_path_le = _ParmPathLineEdit()
        parm_path_le.setObjectName("parmPath")
        parm_path_le.setPlaceholderText("参数路径")
        p0_layout.addWidget(parm_path_le)
        stacked.addWidget(page0)

        # Page 1: 占位页 —— Flipbook 的参数控件在 stacked 之外
        # (flipbook_widget),占位保证 stacked 页序号与任务类型 combo
        # 索引对齐,_on_type_changed 才能直接 setCurrentIndex(idx)
        stacked.addWidget(QWidget())

        # Page 2: HomeAssistant Webhook（放入 stacked，与按钮点击宽度相近）
        page2 = QWidget()
        p2_layout = QHBoxLayout(page2)
        p2_layout.setContentsMargins(0, 0, 0, 0)
        p2_layout.setSpacing(4)
        webhook_url_le = QLineEdit()
        webhook_url_le.setObjectName("webhookUrl")
        webhook_url_le.setPlaceholderText("Webhook URL")
        p2_layout.addWidget(webhook_url_le)
        stacked.addWidget(page2)

        # Page 3: 打开DW —— 无任务参数,软件路径在应用级配置文件中设置
        page3 = QWidget()
        p3_layout = QHBoxLayout(page3)
        p3_layout.setContentsMargins(0, 0, 0, 0)
        p3_layout.setSpacing(4)
        dw_hint = QLabel("启动 Deadline Worker")
        dw_hint.setObjectName("openDWHint")
        dw_hint.setStyleSheet("color: #999999; background: transparent;")
        dw_hint.setToolTip(
            "软件路径在配置文件中设置:\n"
            + AutomationDataManager.get_app_config_path()
        )
        p3_layout.addWidget(dw_hint)
        stacked.addWidget(page3)

        # Flipbook — 独立 widget，不放入 stacked（避免宽度被拉大）
        flipbook_widget = QWidget()
        p1_main_layout = QVBoxLayout(flipbook_widget)
        p1_main_layout.setContentsMargins(0, 0, 0, 0)
        p1_main_layout.setSpacing(2)

        # 第一行：帧范围
        fr_row = QHBoxLayout()
        fr_row.setSpacing(4)
        fr_label = QLabel("帧:")
        fr_label.setStyleSheet("color: #cccccc; background: transparent;")
        start_frame_le = QLineEdit("$RFSTART")
        start_frame_le.setObjectName("flipbookStartFrame")
        start_frame_le.setPlaceholderText("起始帧")
        start_frame_le.setFixedWidth(80)
        tilde_label = QLabel("~")
        tilde_label.setStyleSheet("color: #cccccc; background: transparent;")
        end_frame_le = QLineEdit("$RFEND")
        end_frame_le.setObjectName("flipbookEndFrame")
        end_frame_le.setPlaceholderText("结束帧")
        end_frame_le.setFixedWidth(80)
        save_to_disk_cb = QCheckBox("保存到磁盘")
        save_to_disk_cb.setObjectName("flipbookSaveToDisk")
        save_to_disk_cb.setChecked(True)
        fr_row.addWidget(fr_label)
        fr_row.addWidget(start_frame_le)
        fr_row.addWidget(tilde_label)
        fr_row.addWidget(end_frame_le)
        fr_row.addWidget(save_to_disk_cb)
        fr_row.addStretch()

        # 第二行：输出路径
        out_row = QHBoxLayout()
        out_row.setSpacing(4)
        out_label = QLabel("输出:")
        out_label.setStyleSheet("color: #cccccc; background: transparent;")
        output_path_le = QLineEdit("$HIP/FlipBook/$HIPNAME/$HIPNAME.$F4.jpg")
        output_path_le.setObjectName("flipbookOutputPath")
        output_path_le.setPlaceholderText("输出路径")
        open_folder_btn = QPushButton()
        open_folder_btn.setObjectName("flipbookOpenFolder")
        open_folder_btn.setFixedSize(22, 22)
        open_folder_btn.setToolTip("打开输出路径文件夹")
        _folder_icon = str(Path(__file__).resolve().parent.parent / "icons" / "folder.svg")
        open_folder_btn.setIcon(QIcon(_folder_icon))
        open_folder_btn.setIconSize(QSize(18, 18))
        open_folder_btn.setStyleSheet(
            "QPushButton { border: none; padding: 0px; background: transparent; }"
            "QPushButton:hover { background: rgba(255,255,255,0.15); border-radius: 3px; }"
        )
        out_row.addWidget(out_label)
        out_row.addWidget(output_path_le, 1)
        out_row.addWidget(open_folder_btn)

        p1_main_layout.addLayout(fr_row)
        p1_main_layout.addLayout(out_row)
        flipbook_widget.hide()  # 默认隐藏

        # ── 跳转按钮（仅按钮点击任务显示）──
        def _on_jump():
            node_path = _split_parm_path(parm_path_le.text())[0]
            self._jump_to_node(node_path)

        jump_btn = QPushButton()
        jump_btn.setObjectName("slotJumpBtn")
        jump_btn.setFixedSize(22, 22)
        jump_btn.setToolTip("在网络编辑器中跳转到该任务的目标节点")
        jump_icon = _render_jump_icon()
        if jump_icon is not None:
            jump_btn.setIcon(jump_icon)
            jump_btn.setIconSize(QSize(16, 16))
        else:  # QtSvg 缺失/渲染失败：回退旧文字箭头
            jump_btn.setText("→")
        jump_btn.setStyleSheet(
            "QPushButton { border: none; padding: 0px; background: transparent; }"
            "QPushButton:hover { background: rgba(255,255,255,0.15); border-radius: 3px; }"
        )
        jump_btn.clicked.connect(lambda: _on_jump())

        # ── 启用勾选框 ──
        enabled_cb = QCheckBox("启用")
        enabled_cb.setObjectName("slotEnabled")
        enabled_cb.setChecked(True)

        # ── 组装 ──
        hbox.addWidget(idx_label)
        hbox.addWidget(combo)
        hbox.addWidget(stacked)
        hbox.addWidget(flipbook_widget)
        hbox.addWidget(jump_btn)
        hbox.addWidget(enabled_cb)

        # ── 信号：切换类型时 show/hide ──
        def _on_type_changed(idx):
            jump_btn.setVisible(idx == 0)  # 仅按钮点击任务可跳转
            if idx == 1:  # Flipbook
                stacked.hide()
                flipbook_widget.show()
            else:  # 按钮点击 / Webhook / 打开DW
                flipbook_widget.hide()
                stacked.show()
                stacked.setCurrentIndex(idx)

        combo.currentIndexChanged.connect(_on_type_changed)

        # ── 信号：打开输出路径文件夹 ──
        def _on_open_folder():
            raw_path = output_path_le.text().strip()
            if not raw_path:
                return
            # 展开 $HIP 等表达式
            resolved = raw_path
            try:
                import hou
                resolved = hou.text.expandString(raw_path)
            except Exception:
                pass
            # 取父目录（文件路径 → 所在文件夹）
            if os.path.splitext(resolved)[1]:
                folder = os.path.dirname(resolved)
            else:
                folder = resolved
            if not folder or not os.path.isdir(folder):
                dialogs.warn(
                    slot, "路径不存在",
                    f"输出路径的文件夹不存在：\n{folder}"
                )
                return
            subprocess.Popen(["explorer", os.path.normpath(folder)])

        open_folder_btn.clicked.connect(lambda: _on_open_folder())

        # ── 填充数据（加载时）──
        if data is not None:
            self._populate_slot_from_data(
                slot, data,
                combo, stacked, flipbook_widget,
                parm_path_le,
                start_frame_le, end_frame_le, output_path_le, save_to_disk_cb,
                webhook_url_le,
                enabled_cb,
            )

        return slot

    @staticmethod
    def _populate_slot_from_data(
        slot, data, combo, stacked, flipbook_widget,
        parm_path_le,
        start_frame_le, end_frame_le, output_path_le, save_to_disk_cb,
        webhook_url_le, enabled_cb,
    ):
        """根据 dict 数据填充一个已创建的槽控件。"""
        type_str = data.get("type", "BUTTON_CLICK")
        params = data.get("params", {})
        enabled = data.get("enabled", True)

        # setCurrentIndex 会触发 _on_type_changed 信号，自动处理 show/hide
        if type_str == "BUTTON_CLICK":
            combo.setCurrentIndex(0)
            parm_path_le.setText(_combine_parm_path(
                params.get("node_path", ""),
                params.get("parm_name", ""),
            ))
        elif type_str == "FLIPBOOK":
            combo.setCurrentIndex(1)
            start_frame_le.setText(params.get("start_frame", "$RFSTART"))
            end_frame_le.setText(params.get("end_frame", "$RFEND"))
            output_path_le.setText(params.get("output_path", "$HIP/FlipBook/$HIPNAME/$HIPNAME.$F4.jpg"))
            save_to_disk_cb.setChecked(params.get("save_to_disk", True))
        elif type_str == "HOME_ASSISTANT":
            combo.setCurrentIndex(2)
            webhook_url_le.setText(params.get("webhook_url", ""))
        elif type_str == "OPEN_DW":
            combo.setCurrentIndex(3)  # 无参数页,占位提示见 page3
        else:
            # 未知任务类型(新版数据被旧版面板打开/手工编辑等):绝不静默
            # 落到默认 BUTTON_CLICK —— 那会把原始数据悄悄改坏。渲染为
            # 禁用占位,原始 dict 挂在槽上由 _collect_data 原样透传,
            # Start 落盘不破坏未知类型数据。
            logger.warning("未知任务类型: %s(原始数据已保留,该槽只读)", type_str)
            slot._raw_data = dict(data)
            # 本槽的 combo 独立追加占位项并切过去:setCurrentIndex 触发
            # _on_type_changed(4) 自动隐藏跳转按钮、切到占位页,随后禁用
            # 下拉,杜绝用户改类型把原始数据弄丢
            combo.addItem("未知类型")
            hint_page = QWidget()
            hint_layout = QHBoxLayout(hint_page)
            hint_layout.setContentsMargins(0, 0, 0, 0)
            hint_label = QLabel("未知类型（原始数据已保留）")
            hint_label.setStyleSheet("color: #999999; background: transparent;")
            hint_layout.addWidget(hint_label)
            stacked.addWidget(hint_page)
            combo.setCurrentIndex(combo.count() - 1)
            combo.setEnabled(False)

        enabled_cb.setChecked(enabled)

    # ── 槽管理 ─────────────────────────────────────────────

    def _on_task_count_changed(self):
        """用户在数量输入框输入新数值后按 Enter，调整任务槽数量。"""
        text = self._task_count_input.text().strip()
        if not text:
            return
        try:
            new_count = int(text)
        except ValueError:
            # 输入无效，恢复为当前数量
            self._update_task_count_input()
            return
        # 夹到 1..500:界面语义上始终保底 1 个空槽(0 个槽无法操作),
        # 上限防手滑输入超大数一次性创建海量控件把界面卡死
        new_count = max(1, min(500, new_count))
        current_count = len(self._slot_widgets)
        if new_count == current_count:
            return
        # 批量操作时跳过 _add_slot/_remove_slot 中的单次更新，最后统一刷新
        self._skip_count_update = True
        try:
            if new_count > current_count:
                for _ in range(new_count - current_count):
                    self._add_slot()
            else:
                for _ in range(current_count - new_count):
                    self._remove_slot()
        finally:
            self._skip_count_update = False
        self._update_task_count_input()

    def _update_task_count_input(self):
        """更新数量输入框显示当前任务槽数量。"""
        self._task_count_input.setText(str(len(self._slot_widgets)))

    def _add_slot(self, data: dict | None = None):
        """追加一个新槽。"""
        index = len(self._slot_widgets)
        slot = self._create_slot_widget(index, data)
        self._slot_widgets.append(slot)
        # 同步 append handle(平行列表,_renumber / _index_at_global_y 用)
        self._slot_handles.append(slot._handle)
        # 插入到 stretch 之前
        self._slot_layout.insertWidget(self._slot_layout.count() - 1, slot)
        self._renumber_slots()
        # 批量操作时跳过，最后由调用方统一刷新
        if not getattr(self, '_skip_count_update', False):
            self._update_task_count_input()

    def _remove_slot(self, index: int | None = None) -> None:
        """移除指定索引的槽(默认末尾,兼容工具栏 - 按钮)。

        Args:
            index: 要移除的槽索引;``None`` 表示末尾(工具栏 - 按钮的行为)。

        边界:
        - 列表为空:no-op
        - 索引越界:no-op
        - 删除后自动调整 ``_selected_index``(被删则清空,大于被删索引则 -1)
        """
        if not self._slot_widgets:
            return
        if index is None:
            index = len(self._slot_widgets) - 1
        if not (0 <= index < len(self._slot_widgets)):
            return

        # 拖动中删除:先复位拖拽状态(等效 _on_handle_released 的清理段),
        # 否则手柄上的鼠标捕获和悬空的 _drag_source_index 会在删除后继续
        # 响应 move/release,造成幽灵重排。删的是拖动源槽时整体复位;
        # 删的是源槽之前的槽时源索引前移一格,后续 move 才不会错位。
        if self._drag_source_index is not None:
            if index == self._drag_source_index:
                handle = self._slot_handles[index]
                try:
                    if QApplication.mouseGrabber() is handle:
                        handle.releaseMouse()
                except RuntimeError:
                    pass  # C++ 对象已销毁,捕获随析构自动释放
                handle.setCursor(Qt.OpenHandCursor)
                self._drag_source_index = None
                self._drag_press_pos = None
                self._drag_active = False
            elif index < self._drag_source_index:
                self._drag_source_index -= 1

        slot = self._slot_widgets.pop(index)
        self._slot_handles.pop(index)  # 同步 pop handle
        self._slot_layout.removeWidget(slot)
        slot.deleteLater()

        # 调整选中索引
        if self._selected_index is not None:
            if self._selected_index == index:
                self._selected_index = None
            elif self._selected_index > index:
                self._selected_index -= 1

        # _last_selected_index 也要跟着挪(否则下次更新会用错槽)
        if self._last_selected_index is not None:
            if self._last_selected_index == index:
                self._last_selected_index = None
            elif self._last_selected_index > index:
                self._last_selected_index -= 1

        self._renumber_slots()
        self._update_selection_style()
        # 批量操作时跳过，最后由调用方统一刷新
        if not getattr(self, '_skip_count_update', False):
            self._update_task_count_input()

    def _insert_slot_at(self, index: int) -> None:
        """在指定位置插入一个空任务槽（右键菜单"上方/下方插入"用）。"""
        index = max(0, min(index, len(self._slot_widgets)))
        slot = self._create_slot_widget(index)
        self._slot_widgets.insert(index, slot)
        self._slot_handles.insert(index, slot._handle)
        self._slot_layout.insertWidget(index, slot)
        self._renumber_slots()
        self._select_slot(index)
        self._update_task_count_input()

    def _renumber_slots(self):
        """更新所有槽的序号(序号手柄的文字)。

        用 ``_slot_handles`` 平行列表替原来 ``findChild`` 树走 ——
        ``_renumber_slots`` 会在增/删/拖动结束时调,O(N×M) → O(N)。
        """
        for i, handle in enumerate(self._slot_handles):
            handle.setText(str(i + 1))

    # ── 节点跳转 ───────────────────────────────────────────

    def _jump_to_node(self, node_path: str) -> None:
        """在网络编辑器中跳转到节点（与 Houdini 自带跳转行为一致）。

        目标编辑器优先取最近聚焦的 Houdini 面板（hou.ui.currentPaneTabs），
        聚焦面板不是 NetworkEditor 时退回全局第一个；随后切 pwd、选中
        目标节点并取景居中（同官方路径栏点击行为）。
        """
        try:
            import hou
        except ImportError:
            return
        node_path = (node_path or "").strip()
        if not node_path:
            hou.ui.setStatusMessage(
                "Automation: 该任务未填写参数路径",
                severity=hou.severityType.Warning,
            )
            return
        node = hou.node(node_path)
        if node is None:
            hou.ui.setStatusMessage(
                f"Automation: 节点不存在 {node_path}",
                severity=hou.severityType.Warning,
            )
            return
        editor = next(
            (p for p in hou.ui.currentPaneTabs() if isinstance(p, hou.NetworkEditor)),
            None,
        )
        if editor is None:
            editor = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
        if editor is None:
            hou.ui.setStatusMessage(
                "Automation: 未找到网络编辑器",
                severity=hou.severityType.Warning,
            )
            return
        if node.parent() != editor.pwd():
            editor.setPwd(node.parent())
        node.setSelected(True, clear_all_selected=True)
        _frame_node_in_editor(editor, node)

    # ── 选中(单击手柄) ─────────────────────────────────────

    def _select_slot(self, index: int) -> None:
        """选中指定索引的槽(单选)。

        已选中同一索引则 no-op。索引越界忽略。选中后通过
        ``_update_selection_style`` 刷新视觉。
        """
        if not (0 <= index < len(self._slot_widgets)):
            return
        if self._selected_index == index:
            return
        self._selected_index = index
        self._update_selection_style()

    def _update_selection_style(self) -> None:
        """根据 ``_selected_index`` **差量更新**槽的 ``selected`` 动态属性。

        拖动期 N 次 unpolish/polish → 2 次:仅重绘 prev 槽(False) + curr 槽(True)。
        prev/curr 索引相同时完全 no-op(连 setProperty 都不调)。

        配合 ``styles.py`` 的 ``QWidget#taskSlot[selected="true"]`` 选择器
        实现选中视觉。Qt 不会自动检测动态属性变化,所以需要 unpolish + polish
        强制重评估。
        """
        prev = self._last_selected_index
        curr = self._selected_index
        if prev == curr:
            return
        if prev is not None and 0 <= prev < len(self._slot_widgets):
            s = self._slot_widgets[prev]
            s.setProperty("selected", False)
            s.style().unpolish(s)
            s.style().polish(s)
        if curr is not None and 0 <= curr < len(self._slot_widgets):
            s = self._slot_widgets[curr]
            s.setProperty("selected", True)
            s.style().unpolish(s)
            s.style().polish(s)
        self._last_selected_index = curr

    # ── 拖动重排(按住手柄拖动) ──────────────────────────────

    def _on_handle_pressed(self, slot: QWidget, global_pos: QPoint) -> None:
        """手柄被按下:选中该槽 + 准备拖动(尚未激活,等超过阈值) + 应用"抬起"样式。"""
        # 用 ``is`` 身份比较(不依赖 QWidget.__eq__,QWidget 的 __eq__ 语义不一定身份比较)
        index = None
        for i, s in enumerate(self._slot_widgets):
            if s is slot:
                index = i
                break
        if index is None:
            return
        self._select_slot(index)
        self._drag_source_index = index
        self._drag_press_pos = global_pos
        self._drag_active = False
        # 加阴影 + CSS dragging 状态,视觉上"浮起来"
        self._apply_drag_effect(slot, True)

    def _on_handle_moved(self, slot: QWidget, global_pos: QPoint) -> None:
        """手柄被拖动:超过阈值后实时换位(序号在 release 时统一刷新)。"""
        if self._drag_source_index is None or self._drag_press_pos is None:
            return
        if not self._drag_active:
            # 距离按下点 < 阈值 → 仍认作点击,不算拖动
            if (global_pos - self._drag_press_pos).manhattanLength() < self._drag_threshold:
                return
            self._drag_active = True
        # 计算目标索引
        target = self._index_at_global_y(global_pos.y())
        if target is None or target == self._drag_source_index:
            return
        # 换位后,从新位置继续跟踪(否则下一次 move 会基于旧索引)
        # 注:_move_slot 不再调 _renumber_slots,序号保持"过期"直到 release
        self._move_slot(self._drag_source_index, target)
        self._drag_source_index = target

    def _on_handle_released(self, slot: QWidget, global_pos: QPoint) -> None:
        """手柄松开:移除抬起样式 + 统一刷新序号。"""
        if self._drag_source_index is None:
            return
        # 移除阴影 + dragging 状态
        self._apply_drag_effect(slot, False)
        # 拖动结束后统一刷一次序号(配合 _move_slot 不再自动刷新,实现
        # "拖动期序号不刷新、松开统一更新"的交互)
        self._renumber_slots()
        # 重置 drag 状态
        self._drag_source_index = None
        self._drag_press_pos = None
        self._drag_active = False

    def _apply_drag_effect(self, slot: QWidget, enabled: bool) -> None:
        """应用 / 移除"抬起"拖动视觉效果。

        视觉组合:
        - ``QGraphicsDropShadowEffect``:真实阴影,槽在视觉上"浮"在布局上方
        - ``setProperty("dragging", ...)`` + CSS:让 Qt 样式表可以单独定制
          dragging 状态(目前用于蓝色边框 + 更亮的背景)
        """
        if enabled:
            effect = QGraphicsDropShadowEffect(slot)
            effect.setBlurRadius(24)
            effect.setColor(QColor(0, 0, 0, 200))
            effect.setOffset(0, 6)
            slot.setGraphicsEffect(effect)
            slot.setProperty("dragging", True)
        else:
            slot.setGraphicsEffect(None)
            slot.setProperty("dragging", False)
        # Qt 不会自动检测动态属性变化 → 强制重评估
        slot.style().unpolish(slot)
        slot.style().polish(slot)

    def _move_slot(self, from_index: int, to_index: int) -> None:
        """把槽从 ``from_index`` 移到 ``to_index``(同时更新 list + 布局 + 选中索引)。

        ``_slot_layout.insertWidget(to_index, slot)`` 会把 slot 插到布局的
        ``to_index`` 位置(布局末尾的 stretch 自动推后,不需要手动 +1)。

        **不在此调** ``_renumber_slots()`` **:拖动期序号保持"过期",只在 release 时
        统一刷新,符合"按下拖动时序号不变"的交互预期(与 Finder / Explorer
        拖动行为一致)。调用方负责 release 时刷新。
        """
        if not (0 <= from_index < len(self._slot_widgets)):
            return
        if not (0 <= to_index < len(self._slot_widgets)):
            return
        if from_index == to_index:
            return

        slot = self._slot_widgets.pop(from_index)
        handle = self._slot_handles.pop(from_index)  # 同步 pop handle
        self._slot_widgets.insert(to_index, slot)
        self._slot_handles.insert(to_index, handle)  # 同步 insert handle
        self._slot_layout.removeWidget(slot)
        self._slot_layout.insertWidget(to_index, slot)

        # 调整 _selected_index(槽在 list 中的位置变了,选中指针要跟着挪)
        if self._selected_index is not None:
            if from_index < to_index:
                # 向下挪:[from, to] 之间的索引都 -1
                if from_index < self._selected_index <= to_index:
                    self._selected_index -= 1
            else:  # from_index > to_index
                # 向上挪:[to, from) 之间的索引都 +1
                if to_index <= self._selected_index < from_index:
                    self._selected_index += 1

        # _last_selected_index 同理挪(否则 _update_selection_style 会用错槽)
        if self._last_selected_index is not None:
            if from_index < to_index:
                if from_index < self._last_selected_index <= to_index:
                    self._last_selected_index -= 1
            else:
                if to_index <= self._last_selected_index < from_index:
                    self._last_selected_index += 1

        self._update_selection_style()

    def _index_at_global_y(self, global_y: int) -> int:
        """根据全局 Y 坐标返回对应的目标槽索引(中心锚定算法)。

        算法:取每个 handle 的**中心 Y** 作为"分隔线"。
        - cursor Y < handle[0] 中心 → 返回 0(最前)
        - handle[i] 中心 <= cursor Y < handle[i+1] 中心 → 返回 i+1
        - cursor Y >= 末位 handle 中心 → 返回 N-1(末尾)

        旧实现用 ``top <= y < bottom``(handle 的精确边界),但 handle 只 32px 宽,
        槽间间隙 (8-16px 间距) cursor 完全不命中 handle,fallback 落到 ``return N-1``
        导致"拖到间隙就瞬移末尾"。中心锚定后,间隙也被正确归到相邻槽。

        用 ``_slot_handles`` 平行列表替 findChild,拖动期每次 move 调,
        O(N×M) → O(N)。
        """
        for i, handle in enumerate(self._slot_handles):
            top_y = handle.mapToGlobal(QPoint(0, 0)).y()
            center_y = top_y + (handle.height() >> 1)
            if global_y < center_y:
                return i
        return len(self._slot_handles) - 1

    # ── 键盘事件(Delete 删除选中) ──────────────────────────

    def keyPressEvent(self, event) -> None:
        """Delete 键:删除当前选中槽。无选中则交给父类处理。"""
        if event.key() == Qt.Key_Delete and self._selected_index is not None:
            self._remove_slot(self._selected_index)
            event.accept()
            return
        super().keyPressEvent(event)

    def _on_delete_shortcut(self):
        """QShortcut 版删除：在 Houdini 全局快捷键拦截前认领 Delete。

        焦点在输入框内时 QLineEdit 会接受 ShortcutOverride，本快捷键
        不触发，Delete 保持正常的文本编辑行为。
        """
        if self._selected_index is not None:
            self._remove_slot(self._selected_index)

    # ── 鼠标事件(空白处取消选中) ───────────────────────────────

    def _is_widget_on_slot(self, widget) -> bool:
        """检查 ``widget`` 是否在某个任务槽上(widget 自身或父链上是 slot)。

        用于"点空白 deselect"判定的核心 walk-up 逻辑:
        - 点 handle / combo / line edit → widgetAt 返回最深层子 widget,
          沿 ``.parent()`` 链向上走到 slot → True(不 deselect)
        - 点 slot 卡片内部空隙(槽 widget 的 background 区域)→ widgetAt
          返回 slot 自身 → True
        - 点 slot_container 的 stretch 区域(最后一个 slot 下面那条
          "推上去"的留白)→ widgetAt 返回 slot_container,沿父链走到
          viewport → scroll_area → dialog,都**不是 slot** → False(deselect)
        - 点 dialog 自身 margin / spacing → widgetAt 返回 dialog → False

        之前的 ``self.childAt(event.pos()) is None`` 判定不靠谱,因为
        ``childAt`` 只识别**直接子**:点滚动区里 slot_container 的 stretch
        时,``childAt`` 返回 ``QScrollArea``(直接子)而非 None,所以误判
        "在子 widget 上"不 deselect。``QApplication.widgetAt`` 拿最顶层
        widget + walk-up 父链才正确。
        """
        while widget is not None:
            for slot in self._slot_widgets:
                if widget is slot:
                    return True
            widget = widget.parent()
        return False

    def mousePressEvent(self, event) -> None:  # noqa: N802 — Qt 命名约定
        """点击 dialog 任意空白处 → 取消任务槽选中 + 取消输入框焦点。

        行为对齐 Houdini 主窗口风格:点空白取消选中,方便用 Delete
        键连删多个任务时,先 deselect 再选下一个。

        ``QApplication.widgetAt(global_pos)`` 拿全局坐标最顶层 widget,
        ``_is_widget_on_slot`` 沿父链 walk-up 判定:
        - 在某 slot 上(handle / combo / line edit / 卡片空隙)→ 不 deselect
        - 不在任何 slot 上(dialog margin / 滚动区 stretch / 工具栏按钮
          / 任意非 slot 区域)→ deselect

        只响应左键 + 有选中态;无选中 / 右键都 no-op。
        """
        if event.button() == Qt.LeftButton:
            # 取消输入框焦点
            self._config_combo.clearFocus()
            self._task_count_input.clearFocus()
            # 取消任务槽选中
            if (
                self._selected_index is not None
                and not self._is_widget_on_slot(QApplication.widgetAt(event.globalPos()))
            ):
                self._selected_index = None
                self._update_selection_style()
        super().mousePressEvent(event)

    # ── 右键菜单(任务槽操作) ───────────────────────────────

    def contextMenuEvent(self, event) -> None:  # noqa: N802 — Qt 命名约定
        """右键任务槽：删除选定任务 / 在上方插入任务 / 在下方插入任务。

        右键时先选中所在槽再弹菜单，动作始终作用于该槽。输入框等子控件
        有自己的标准右键菜单（复制/粘贴），不会传播到这里。
        """
        pos_global = event.globalPos()
        target = None
        for i, slot in enumerate(self._slot_widgets):
            rect = QRect(slot.mapToGlobal(QPoint(0, 0)), slot.size())
            if rect.contains(pos_global):
                target = i
                break
        if target is None:
            return  # 空白处右键不给菜单

        self._select_slot(target)
        menu = QMenu(self)
        act_delete = menu.addAction("删除选定任务")
        act_above = menu.addAction("在上方插入任务")
        act_below = menu.addAction("在下方插入任务")
        chosen = menu.exec(pos_global)

        if chosen is act_delete:
            self._remove_slot(target)
        elif chosen is act_above:
            self._insert_slot_at(target)
        elif chosen is act_below:
            self._insert_slot_at(target + 1)

    # ── 设置面板 ───────────────────────────────────────────

    def _open_settings(self):
        """打开设置面板。"""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QCheckBox, QPushButton

        dialog = QDialog(self)
        dialog.setWindowTitle("设置")
        dialog.setMinimumWidth(300)
        dialog.setStyleSheet(
            "QDialog { background-color: #1D1D20; color: white; }"
            "QCheckBox { color: white; spacing: 8px; }"
            "QCheckBox::indicator { width: 16px; height: 16px; }"
            "QPushButton { background-color: #2d2d2d; color: white; border: 1px solid #3d3d3d; "
            "border-radius: 4px; padding: 8px 16px; min-width: 60px; }"
            "QPushButton:hover { background-color: #3d3d3d; }"
        )

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # 日志输出到磁盘选项
        self._log_to_disk_cb = QCheckBox("将日志输出到磁盘")
        self._log_to_disk_cb.setChecked(self._log_to_disk_enabled)
        self._log_to_disk_cb.setToolTip("勾选后，执行日志将保存到 $HIP/HouTools_cfg/Automation_logs/")
        # 引擎运行中禁用:stdout 重定向与该开关耦合(开始执行时按它包
        # _LogTee),运行中切换会留下"包了没人还原"的缺口;_on_all_completed
        # 复位 _running 后,下次打开设置自然恢复可勾
        self._log_to_disk_cb.setEnabled(not self._running)
        layout.addWidget(self._log_to_disk_cb)

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("取消")
        ok_btn = QPushButton("确定")
        cancel_btn.clicked.connect(dialog.reject)
        ok_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

        if dialog.exec() == QDialog.Accepted:
            self._log_to_disk_enabled = self._log_to_disk_cb.isChecked()
            self._save_settings()

    def _load_settings(self):
        """从配置文件加载设置。"""
        settings = AutomationDataManager.load_settings(self._current_config_name)
        self._log_to_disk_enabled = settings.get("log_to_disk", False)

    def _save_settings(self):
        """保存设置到配置文件(失败弹窗提示,不静默)。"""
        ok = AutomationDataManager.save_settings(
            {"log_to_disk": self._log_to_disk_enabled},
            self._current_config_name,
        )
        if not ok:
            logger.warning(
                "Automation: 设置保存失败 filename=%s", self._current_config_name
            )
            dialogs.warn(
                self, "保存失败",
                "设置写入配置文件失败:\n"
                + AutomationDataManager.get_data_path(self._current_config_name),
            )

    def _get_log_path(self) -> str:
        """获取日志文件路径（基于当前时间，防覆盖）。

        同一分钟内多次执行时自动加后缀 .2, .3, ... 防止覆盖已有日志。
        """
        try:
            import hou
            hip = hou.getenv("HIP")
            if hip:
                base = hip
            else:
                import tempfile
                base = tempfile.gettempdir()
        except ImportError:
            import tempfile
            base = tempfile.gettempdir()

        log_dir = os.path.join(base, "HouTools_cfg", "Automation_logs")
        try:
            os.makedirs(log_dir, exist_ok=True)
        except OSError as e:
            # 目录建不出来(权限/磁盘满等):禁用磁盘日志并明确告知,否则
            # 每次 _write_log 都在同一个失败点上反复抛错
            logger.warning("无法创建日志目录，已停用磁盘日志: %s (%s)", log_dir, e)
            self._log_to_disk_enabled = False
            dialogs.warn(
                self, "日志目录创建失败",
                f"无法创建日志目录:\n{log_dir}\n\n本次执行已停用日志输出到磁盘。",
            )
            return ""

        base_name = datetime.now().strftime("%Y%m%d%H%M")
        log_path = os.path.join(log_dir, base_name + ".log")
        if not os.path.exists(log_path):
            return log_path

        # 同一分钟已有文件，从 .2 开始递增
        counter = 2
        while True:
            log_path = os.path.join(log_dir, f"{base_name}.{counter}.log")
            if not os.path.exists(log_path):
                return log_path
            counter += 1

    def _write_log(self, message: str):
        """写入日志（如果启用了日志输出到磁盘）。

        使用缓存路径,同一次执行内所有输出写入同一文件。
        """
        if not self._log_to_disk_enabled:
            return

        # 首次调用时确定路径并缓存("" = 目录创建失败,本次已停用)
        if not hasattr(self, "_current_log_path") or self._current_log_path is None:
            self._current_log_path = self._get_log_path()
        if not self._current_log_path:
            return

        try:
            with open(self._current_log_path, "a", encoding="utf-8") as f:
                f.write(message + "\n")
        except Exception as exc:
            # 日志写入失败不影响主流程，但留一条诊断记录
            logger.debug("写执行日志失败 %s: %s", self._current_log_path, exc)

    def _write_log_header(self):
        """写入日志头部（当前任务列表信息）。"""
        print("=" * 80)
        print(f"📝 {self._current_config_name} 执行日志")
        if not self._log_to_disk_enabled:
            return

        lines = [f"{self._current_config_name} 执行日志", ""]

        # 收集当前任务列表信息
        tasks_data = self._collect_data()
        if tasks_data:
            lines.append("任务列表:")
            for i, task in enumerate(tasks_data, 1):
                task_type = task.get("type", "UNKNOWN")
                enabled = "启用" if task.get("enabled", True) else "禁用"
                params = task.get("params", {})

                lines.append(f"任务 {i}: {task_type} [{enabled}]")
                if task_type == "BUTTON_CLICK":
                    node_path = params.get("node_path", "")
                    parm_name = params.get("parm_name", "")
                    lines.append(f"  节点: {node_path}")
                    lines.append(f"  参数: {parm_name}")
                elif task_type == "FLIPBOOK":
                    sf = params.get("start_frame", "$RFSTART")
                    ef = params.get("end_frame", "$RFEND")
                    op = params.get("output_path", "")
                    sd = params.get("save_to_disk", True)
                    lines.append(f"  帧范围: {sf} ~ {ef}")
                    lines.append(f"  输出路径: {op}")
                    lines.append(f"  保存到磁盘: {'是' if sd else '否'}")
                elif task_type == "HOME_ASSISTANT":
                    webhook_url = params.get("webhook_url", "")
                    lines.append(f"  Webhook: {webhook_url}")
                elif task_type == "OPEN_DW":
                    lines.append(f"  DW 软件路径: {AutomationDataManager.load_dw_exe_path()}")
                lines.append("")
        else:
            lines.append("任务列表: (空)")
            lines.append("")

        lines.append("=" * 80)
        self._write_log("\n".join(lines))

    # ── 数据持久化 ─────────────────────────────────────────

    def _load_data(self):
        """从 DataManager 加载数据并重建 UI 槽。

        首次打开(无数据)时自动添加一个空任务槽,方便用户直接开始操作。

        流程:
          1. ``_refresh_config_dropdown()`` — 列出配置目录下所有现存 .json
             并恢复当前选中(``_current_config_name``)
          2. ``AutomationDataManager.load(self._current_config_name)`` —
             加载该配置文件
          3. 清空现有槽 + 重建;若为空则添加1个空槽
        """
        # 1. 先刷新下拉(列表 + 恢复当前选中),让 UI 与状态同步
        self._refresh_config_dropdown()
        # 2. 加载当前选中的配置
        raw_list = AutomationDataManager.load(self._current_config_name)

        # 清空现有槽
        for slot in self._slot_widgets:
            self._slot_layout.removeWidget(slot)
            slot.deleteLater()
        self._slot_widgets.clear()
        self._slot_handles.clear()  # 同步清空 handle 平行列表
        self._selected_index = None  # 选中指针随槽一起清空(同 _remove_slot 契约)
        self._last_selected_index = None  # 重置差量状态

        for item_data in raw_list:
            self._add_slot(item_data)

        # 首次打开(无数据)时添加1个空任务槽
        if not raw_list:
            self._add_slot()

    # ── 配置下拉 helper ────────────────────────────────────

    def _refresh_config_dropdown(self):
        """刷新配置下拉,列出配置目录下所有 .json 文件 basename(无后缀)。

        关键:用 ``blockSignals(True)`` 防止 ``clear()`` / ``addItems()`` /
        ``setCurrentIndex()`` 触发 ``currentIndexChanged`` →
        ``_on_config_changed`` → ``_load_data`` 死循环。

        当前配置名不在列表时(首次打开 / 刚新建尚未落盘)直接插入为列表项,
        保证下拉始终显示用户正在使用的配置名。
        """
        configs = AutomationDataManager.list_configs()
        self._config_combo.blockSignals(True)
        try:
            self._config_combo.clear()
            self._config_combo.addItems(configs)
            if self._current_config_name:
                idx = self._config_combo.findText(self._current_config_name)
                if idx < 0:
                    # 不在列表中(首次打开 / 新建未落盘):插入为列表项
                    self._config_combo.addItem(self._current_config_name)
                    idx = self._config_combo.findText(self._current_config_name)
                self._config_combo.setCurrentIndex(idx)
        finally:
            self._config_combo.blockSignals(False)

    def _on_config_changed(self, index: int):
        """用户从下拉选了不同配置 → 重新加载该文件覆盖面板。

        注意:``currentIndexChanged`` 在 ``clear()`` / ``addItems()`` 期间
        也会触发,已用 ``_refresh_config_dropdown`` 的 ``blockSignals``
        屏蔽。只有用户**主动**选择时才会进这里。``index < 0``(被清空后)
        直接返回,避免误清空面板。
        """
        if index < 0:
            return
        name = self._config_combo.itemText(index)
        if not name:
            return
        self._current_config_name = name
        self._load_data()  # 重新加载,内部会再 refresh 一次(无副作用)
        self._load_settings()  # 同步加载新配置的设置项

    def _on_new_config(self):
        """新建配置:输入名字确认后**立即**落盘一份空配置文件,并把面板
        刷新为该配置的内容(空配置 → 清空任务槽,补 1 个空槽)。

        独立 QInputDialog 而非在 combo 里键入 —— Houdini 浮动面板会
        清空部件焦点,内联编辑不可靠。同名配置已存在时**不覆盖**(否则
        会把用户任务清成空),仅切换并加载其已有内容。
        """
        name, ok = dialogs.prompt_text(self, "新建配置", "配置名称:")
        if not ok:
            return
        sanitized = _sanitize_config_name(name)
        if not sanitized:
            dialogs.warn(
                self, "无效名称",
                "配置名不能为空,不能包含路径分隔符,\n"
                "也不能使用 Windows 保留名(CON/PRN/AUX/NUL/COM1-9/LPT1-9)。",
            )
            return

        path = AutomationDataManager.get_data_path(sanitized)
        if os.path.exists(path):
            dialogs.info(
                self, "配置已存在",
                f"配置 {sanitized} 已存在,已切换到该配置(内容未改动)。",
            )
        elif not AutomationDataManager.save([], filename=sanitized):
            logger.warning(
                "Automation: 新建配置落盘失败 filename=%s", sanitized
            )
            dialogs.warn(self, "创建失败", f"无法创建配置文件:\n{path}")
            return
        self._current_config_name = sanitized
        # 与下拉切换配置同款行为:加载新配置的内容到面板
        # (空配置 → 清空任务槽并补 1 个空槽)。_load_data 内部的
        # _refresh_config_dropdown 带 blockSignals,不会误触发切换。
        self._load_data()
        self._load_settings()

    def _get_save_target_name(self) -> str | None:
        """从下拉当前文本提取保存文件名(经 ``_sanitize_config_name`` 净化)。

        下拉文本的来源(列表项 / "新建"对话框)在上游已净化,此处再跑一遍
        纯属防御;非法名返回 ``None``(fall back 到默认 ``Automation.json``)。
        """
        return _sanitize_config_name(self._config_combo.currentText())

    def _collect_data(self) -> list[dict]:
        """读取 UI 槽，构建 list[dict]（与 TaskItem.to_dict() 格式一致）。

        参数控件一律在槽层级 ``slot.findChild`` 查找（objectName 唯一），
        不依赖 stacked 当前页 —— Flipbook 的控件在 stacked 之外，且
        stacked 页序号会随类型切换而变化。
        """
        tasks: list[dict] = []
        for slot in self._slot_widgets:
            # 未知类型槽:原样透传创建时挂上的原始 dict(仅 enabled 跟随
            # 勾选框),避免按 UI 控件重建把未知类型数据改坏 —— 见
            # _populate_slot_from_data 的未知类型分支
            raw = getattr(slot, "_raw_data", None)
            if raw is not None:
                passthrough = dict(raw)
                enabled_cb = slot.findChild(QCheckBox, "slotEnabled")
                passthrough["enabled"] = (
                    enabled_cb.isChecked() if enabled_cb
                    else raw.get("enabled", True)
                )
                tasks.append(passthrough)
                continue

            combo = slot.findChild(QComboBox, "taskType")
            if combo is None:
                continue
            type_idx = combo.currentIndex()

            enabled_cb = slot.findChild(QCheckBox, "slotEnabled")
            enabled = enabled_cb.isChecked() if enabled_cb else True

            if type_idx == 0:  # 按钮点击
                node_path = ""
                parm_name = ""
                pp_le = slot.findChild(QLineEdit, "parmPath")
                if pp_le is not None:
                    node_path, parm_name = _split_parm_path(pp_le.text())
                params = ButtonClickParams(node_path=node_path, parm_name=parm_name)
                item = TaskItem(
                    task_type=TaskType.BUTTON_CLICK,
                    params=params,
                    enabled=enabled,
                )

            elif type_idx == 1:  # Flipbook
                start_frame = "$RFSTART"
                end_frame = "$RFEND"
                output_path = "$HIP/FlipBook/$HIPNAME/$HIPNAME.$F4.jpg"
                save_to_disk = True
                sf_le = slot.findChild(QLineEdit, "flipbookStartFrame")
                ef_le = slot.findChild(QLineEdit, "flipbookEndFrame")
                op_le = slot.findChild(QLineEdit, "flipbookOutputPath")
                sd_cb = slot.findChild(QCheckBox, "flipbookSaveToDisk")
                if sf_le is not None:
                    start_frame = sf_le.text()
                if ef_le is not None:
                    end_frame = ef_le.text()
                if op_le is not None:
                    output_path = op_le.text()
                if sd_cb is not None:
                    save_to_disk = sd_cb.isChecked()
                params = FlipbookParams(
                    start_frame=start_frame,
                    end_frame=end_frame,
                    output_path=output_path,
                    save_to_disk=save_to_disk,
                )
                item = TaskItem(
                    task_type=TaskType.FLIPBOOK,
                    params=params,
                    enabled=enabled,
                )

            elif type_idx == 2:  # HomeAssistant Webhook
                webhook_url = ""
                wh_le = slot.findChild(QLineEdit, "webhookUrl")
                if wh_le is not None:
                    webhook_url = wh_le.text()
                params = HomeAssistantParams(webhook_url=webhook_url)
                item = TaskItem(
                    task_type=TaskType.HOME_ASSISTANT,
                    params=params,
                    enabled=enabled,
                )

            elif type_idx == 3:  # 打开DW —— 软件路径在应用级配置文件中
                item = TaskItem(
                    task_type=TaskType.OPEN_DW,
                    params=OpenDWParams(),
                    enabled=enabled,
                )

            else:  # 未知类型(理论不可达),跳过避免写坏配置
                logger.warning("未知任务类型索引: %s", type_idx)
                continue

            tasks.append(item.to_dict())

        return tasks

    def _save_data(self) -> list[dict]:
        """收集并持久化任务数据,返回收集到的 ``list[dict]`` 供调用方使用。

        保存目标由 ``_get_save_target_name()`` 决定(从下拉当前文本提取):
          - 空 / 全空白 / 含路径分隔符 → fall back 到默认 ``Automation.json``
          - 其它 → 写到该名 .json(**不存在则创建**)。新配置的空文件由
            "新建配置"对话框立即落盘;此处负责把当前任务写进当前配置

        **失败处理**:``AutomationDataManager.save()`` 返回 ``False``(写盘
        异常:磁盘满 / 权限 / 只读 / OS 拒绝保留名)时,``logger.warning``
        记录 + **不**更新 ``_current_config_name`` / **不** refresh 下拉,
        让用户重试。否则 UI 会"假装成功",后续 ``_load_data`` 加载错误的旧
        数据,看起来"丢失未保存修改"。

        保存成功完成后:
          1. 更新 ``_current_config_name`` 为刚保存的文件名(状态同步,
             让后续操作基于新保存的文件)
          2. 刷新下拉(让新建文件出现在列表中,用户能看到自己刚保存的配置)
        """
        tasks_data = self._collect_data()
        save_name = self._get_save_target_name()  # 可能为 None
        ok = AutomationDataManager.save(tasks_data, filename=save_name)
        if not ok:
            # 写盘失败:不更新状态,让用户重试 + 看到日志提示
            logger.warning(
                "Automation: 保存失败 filename=%s", save_name
            )
            return tasks_data
        # 状态同步:更新当前配置名(只有显式保存到某名时才更新)
        if save_name:
            self._current_config_name = save_name
        # 刷新下拉让新文件出现在列表中 + 恢复选中
        self._refresh_config_dropdown()
        return tasks_data

    # ── 执行集成 ───────────────────────────────────────────

    def _on_start(self):
        """Start / Cancel 切换按钮。"""
        if self._cancel_requested:
            # 已请求停止但引擎还挂在在途任务上（可能数小时），此时绝不能再
            # Start：旧引擎退出的 all_completed 会把新引擎的状态打翻
            return
        if self._running:
            self._cancel_execution()
            return
        self._start_execution()

    def _start_execution(self):
        """收集任务 → 落盘 JSON → 创建 ExecutionEngine → 启动后台执行。

        JSON 仅在 Start 时落盘,关窗不保存。
        语义:JSON = 用户决定执行的任务,不是当前 UI 状态;
        编辑后未点 Start 直接关窗 = 丢弃未执行编辑(有意为之)。
        """
        tasks_data = self._save_data()  # 收集 + 落盘(只此一处)
        # 未知类型/字段缺损的任务原样留在落盘数据里,但不参与本次执行
        # (TaskItem.from_dict 对它们抛 ValueError/KeyError/TypeError)
        task_items = []
        for d in tasks_data:
            try:
                task_items.append(TaskItem.from_dict(d))
            except (ValueError, KeyError, TypeError) as e:
                logger.warning(
                    "任务无法执行，已从本次运行跳过: type=%s (%s)", d.get("type"), e
                )

        # 先还原可能残留的旧重定向(上次执行异常收尾/重复 Start),杜绝
        # _LogTee 嵌套;必须在写日志头之前做 —— 还原会清日志路径缓存
        self._restore_stdout()

        # 清除上次日志路径缓存，本次执行重新计算
        self._current_log_path = None

        # 写入日志头部（任务列表信息）
        self._write_log_header()

        # 重定向 stdout/stderr 到日志文件
        if self._log_to_disk_enabled:
            self._orig_stdout = sys.stdout
            self._orig_stderr = sys.stderr
            sys.stdout = _LogTee(self._orig_stdout, self._write_log)
            sys.stderr = _LogTee(self._orig_stderr, self._write_log)

        self._engine = ExecutionEngine(task_items)
        _LIVE_ENGINES.append(self._engine)
        # QThread.finished 无参发射，而 PySide6 在发射时才检查槽的参数个数
        # （连接时不查），直连单参普通函数会在每次线程收尾时报 TypeError 且
        # _release_engine 从不执行——用 partial 在连接时绑死 engine。
        # 显式 DirectConnection：finished 连普通函数（无 QObject 接收者）时
        # AutoConnection 在 PySide6 下不会触发；Direct 则在线程收尾时直接执行，
        # _release_engine 只做 list.remove，跨线程安全，且不依赖窗口存活
        self._engine.finished.connect(
            functools.partial(_release_engine, self._engine), Qt.DirectConnection
        )
        self._engine.task_started.connect(self._on_task_started)
        self._engine.task_completed.connect(self._on_task_completed)
        self._engine.all_completed.connect(self._on_all_completed)

        self._cancel_requested = False
        self._running = True
        self._start_btn.setText("取消")
        self._engine.start()

    def _cancel_execution(self):
        """请求取消：协作式，引擎在当前任务完成后才真正停止。

        引擎对主线程的同步等待是有意设计（见 execution_engine 模块文档），
        在途任务无法被打断，cancel 只在任务边界生效。因此这里不得立即把
        UI 复位成空闲——按钮保持「停止中」并禁用，直到引擎真正发出
        all_completed 由 _on_all_completed 复位；否则会留下"看似已取消、
        实际引擎还在跑"的窗口，期间再次 Start 会造成双引擎状态串扰。
        stdout 也不在此处恢复，让在途任务的收尾输出继续落进日志。
        """
        if self._engine is not None:
            self._engine.cancel()
        self._cancel_requested = True
        self._start_btn.setEnabled(False)
        self._start_btn.setText("停止中…")
        print("Automation: 已请求停止，将在当前任务完成后退出")

    def _on_task_started(self, idx: int, task_type: str, timestamp: str):
        """单个任务开始时的回调。"""
        print(f"任务 {idx + 1} ({task_type}) {timestamp}")

    def _on_task_completed(self, idx: int, ok: bool, msg: str, elapsed: float):
        """单个任务完成时的回调。"""
        if ok:
            hours = int(elapsed // 3600)
            minutes = int((elapsed % 3600) // 60)
            seconds = int(elapsed % 60)
            print(f"✓ - 执行成功 耗时: {hours:02d}时{minutes:02d}分{seconds:02d}秒")
        else:
            print(f"✗ - {msg}")

    def _on_all_completed(self, success: int, failed: int, timestamp: str, total_elapsed: float):
        """全部任务执行完毕的回调。"""
        hours = int(total_elapsed // 3600)
        minutes = int((total_elapsed % 3600) // 60)
        seconds = int(total_elapsed % 60)
        print(f"{timestamp} 执行完成 总耗时: {hours:02d}时{minutes:02d}分{seconds:02d}秒 — 成功 {success}, 失败 {failed}")
        print("=" * 80)  # 通过 tee 同时写入控制台和日志文件
        self._running = False
        self._cancel_requested = False
        self._start_btn.setEnabled(True)
        self._start_btn.setText("执行")
        self._engine = None
        self._restore_stdout()

    def _restore_stdout(self):
        """恢复被重定向的 stdout/stderr（幂等，可安全重复调用）。

        不看 ``_log_to_disk_enabled`` 现值 —— 开关可能在运行期间被改
        （换配置重载设置等），只要还有挂着的重定向就一律还原，否则开关
        关掉后 tee 永远卸不下来。
        """
        if hasattr(self, "_orig_stdout"):
            sys.stdout = self._orig_stdout
            sys.stderr = self._orig_stderr
            del self._orig_stdout
            del self._orig_stderr
            self._current_log_path = None  # 清除日志路径缓存
        elif isinstance(sys.stdout, _LogTee):
            # 兜底:上次收尾没走到(面板带着重定向被销毁等)导致实例属性
            # 丢失、sys.stdout 仍是被包的 tee。沿 _original 链解包,防止
            # 下次执行在 tee 之上再包一层(嵌套后同一行输出会写多份日志)
            while isinstance(sys.stdout, _LogTee):
                sys.stdout = sys.stdout._original
            while isinstance(sys.stderr, _LogTee):
                sys.stderr = sys.stderr._original
            self._current_log_path = None

    # ── 工具栏动作 ──────────────────────────────────────────

    # ── Auto Fill ───────────────────────────────────────────────────

    def _is_slot_empty_at(self, index: int) -> bool:
        """检查指定索引的槽是否为空（仅对 BUTTON_CLICK 类型判断）。

        判定条件：BUTTON_CLICK 类型 + parmPath 为空字符串。
        索引越界 或 非 BUTTON_CLICK → 返回 False（避免误覆盖其他类型任务）。
        """
        pp_le = self._get_button_click_widgets(index)
        if pp_le is None:
            return False
        return not pp_le.text().strip()

    def _get_button_click_widgets(self, index: int) -> QLineEdit | None:
        """定位指定槽的 BUTTON_CLICK 参数路径 LineEdit。

        返回 ``parmPath_le``(单字段,UI 层把 ``node_path`` + ``parm_name`` 合并
        显示,数据收集时再拆分 —— 见 ``_split_parm_path`` / ``_combine_parm_path``);
        任一前置条件不满足返回 ``None``:
          - 索引越界
          - 槽内缺少 ``taskType`` combo / 当前不是 BUTTON_CLICK
          - 缺少 ``paramsStacked`` / 当前 page 为空
          - 缺少 ``parmPath`` LineEdit

        统一 ``_is_slot_empty_at`` 和 ``_fill_slot_at`` 的 widget 查找逻辑,
        避免两处镜像的 findChild + None 守卫代码。
        """
        if not (0 <= index < len(self._slot_widgets)):
            return None
        slot = self._slot_widgets[index]

        combo = slot.findChild(QComboBox, "taskType")
        if combo is None or combo.currentIndex() != 0:  # 0 = BUTTON_CLICK
            return None

        stacked = slot.findChild(QStackedWidget, "paramsStacked")
        current_page = stacked.currentWidget() if stacked is not None else None
        if current_page is None:
            return None

        pp_le = current_page.findChild(QLineEdit, "parmPath")
        if pp_le is None:
            return None
        return pp_le

    def _fill_slot_at(self, index: int, data: dict) -> None:
        """用 data 填充指定索引的槽的输入控件（不创建新槽）。

        仅处理 BUTTON_CLICK 类型；其他类型直接 noop（防御性）。
        数据模型 ``node_path`` + ``parm_name`` 在 UI 层合并为单个
        ``parmPath`` 字段(见 ``_combine_parm_path``)。
        """
        if data.get("type") != "BUTTON_CLICK":
            return

        pp_le = self._get_button_click_widgets(index)
        if pp_le is None:
            return
        params = data.get("params", {})
        pp_le.setText(_combine_parm_path(
            params.get("node_path", ""),
            params.get("parm_name", ""),
        ))

    def _find_trailing_empty_slots(self) -> list[int]:
        """从后往前扫描连续空槽（BUTTON_CLICK），返回索引列表（从小到大）。

        "连续"：从末尾往前，遇到第一个非空槽时停止扫描。
        返回从小到大：填充时从前往后顺序消费，**末尾保留空槽**
        （用户可继续手动填，而非末尾被填掉）。

        例：列表 [填, 填, 空, 空, 空] → 返回 [2, 3, 4]。
        """
        result = []
        for i in range(len(self._slot_widgets) - 1, -1, -1):
            if self._is_slot_empty_at(i):
                result.append(i)
            else:
                break
        result.reverse()
        return result

    def _on_auto_fill(self):
        """从当前选中的节点中提取 execute 按钮路径，填入任务列表。

        遍历 hou.selectedNodes()，对每个有 'execute' 参数的节点，
        构造 BUTTON_CLICK 任务：
          - 预扫描连续空槽队列（从后往前），逐个填入（不新增）
          - 空槽用完后仍有节点剩余 → 追加新槽

        **静默执行**：无选中节点 / 无有效节点 / 正常完成都**不打印、不弹窗、
        不写日志**(用户不要任何提醒)。失败由调用方(选中无效节点)默默处理。

        注意：``_find_trailing_empty_slots`` 只在循环开始前调用一次，
        维护索引队列逐个消费。否则 fill 后 widget text 立即更新，
        循环内重新扫描会把"刚填的槽"误判为非空，导致后续节点走新增分支。
        """
        import hou  # Houdini-only, 放入方法内部

        selected = hou.selectedNodes()
        if not selected:
            return

        # 预扫描一次，连续空槽索引队列（从小到大：填充时从前往后消费，
        # **末尾保留** 空槽给用户手动填，详见 _find_trailing_empty_slots 注释）
        empty_iter = iter(self._find_trailing_empty_slots())

        processed = 0
        for node in selected:
            parm = node.parm("execute")
            if parm is None:
                continue
            data = {
                "type": "BUTTON_CLICK",
                "params": {
                    "node_path": node.path(),
                    "parm_name": "execute",
                },
                "enabled": True,
            }
            try:
                idx = next(empty_iter)
                self._fill_slot_at(idx, data)
            except StopIteration:
                # 连续空槽已用完，新增
                self._add_slot(data)
            processed += 1

    def _on_clear(self):
        """清空所有槽,保留 1 个空槽。

        **必须同步清空 ``_slot_handles`` 平行列表** —— 漏掉会导致下次
        ``_renumber_slots``(在 ``_add_slot`` 里调)拿一堆已经被
        ``deleteLater()`` 的 handle 调 ``setText``,触发
        ``RuntimeError: Internal C++ object (_SlotHandle) already deleted``。
        与 ``_remove_slot``(双 pop)同款契约。
        """
        for slot in self._slot_widgets:
            self._slot_layout.removeWidget(slot)
            slot.deleteLater()
        self._slot_widgets.clear()
        self._slot_handles.clear()  # 平行列表必须同步 —— 见 docstring
        self._selected_index = None  # 选中指针随槽一起清空(同 _remove_slot 契约)
        self._last_selected_index = None  # 差量样式状态同步归零
        self._add_slot()

    # ── 窗口关闭 ───────────────────────────────────────────

    def closeEvent(self, event):
        """关闭时取消执行中的任务并恢复 stdout 重定向（数据仅在 Start 时落盘）。

        引擎线程无法被打断（有意设计，见 execution_engine）：关窗只请求取消，
        线程会继续挂在在途任务的同步等待上，等主线程空闲后在后台自然退出并
        完成收尾——期间由模块级 _LIVE_ENGINES 持有引用，防止运行中的 QThread
        被 GC 销毁。其信号随面板销毁自动断开，不会再回调本窗口。
        """
        if self._engine is not None:
            self._engine.cancel()
        # _running/_cancel_requested 不在此复位:状态收尾只能由
        # _on_all_completed/引擎负责 —— 面板子部件大概率收不到 closeEvent,
        # 这里被调用不代表窗口真正销毁,贸然复位会制造"假空闲"(按钮恢复
        # 可点,旧引擎迟到的 all_completed 又把新引擎状态打翻)。
        # stdout 恢复是幂等的,重复调用无副作用。
        self._restore_stdout()
        super().closeEvent(event)
