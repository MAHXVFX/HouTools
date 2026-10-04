"""Recipe Library 主窗口：基于官方 recipes 的资产浏览/应用/文档面板。

布局：顶部工具栏（刷新/库目录/搜索/大小）+ 左侧栏（全部/收藏/分类/标签）
+ 中部缩略图网格 + 右侧预览面板（动图预览/元信息/标签编辑）+ 底部状态栏。

交互（四类 recipe 语义不同，见 store.apply_*）：
- 双击卡片：Tool 按官方工具架体验立即在当前网络创建并框选（无二次点击）；
  Node/Param 预设与 Decoration 需要"先选中目标节点"再双击应用。
- 按住卡片拖到网络编辑器释放：立即在鼠标点创建（自研拖拽，不依赖
  网络编辑器接受任何 MIME——拖动期间装 QApplication 级事件过滤器，
  松手时用 hou.ui.paneTabUnderCursor() 判定落点、editor.cursorPosition()
  取网络坐标，apply 后按返回 items 锚点平移对齐）。仅 Tool 支持拖拽。
- GIF 缩略图：网格里静态首帧（性能），预览区/文档里动起来。

无头约束：__init__ 不调 hou（smoke test 直接实例化）；数据获取全在
reload() 里经 store.list_recipes()（测试里打补丁替换）。
"""

import os

from PySide6 import QtCore, QtGui, QtWidgets

from houtools.core.log import get_logger
from houtools.recipelib import metadata, store
from houtools.recipelib.docs import DocEditorDialog, MarkdownMediaView, MediaDialog
from houtools.ui.badge import FavoriteBadge
from houtools.ui.taskbar import apply_appwindow_flags
from houtools.core.settings import JsonStore

log = get_logger("recipelib.browser")

TOOL_ID = "recipe_library"


def _push_houdini_error(message):
    """把错误推到 Houdini 左下角状态栏（红色 severity=Error）。

    面板自身状态栏也有同款文案；这里保证用户视线留在网络编辑器时
    也能看到失败原因。无头/旧版签名差异时静默降级。
    """
    try:
        import hou
        hou.ui.setStatusMessage("Recipe Library: {}".format(message),
                                severity=hou.severityType.Error)
    except Exception as exc:
        log.debug("setStatusMessage failed: %s", exc)

KEY_ALL = "__all__"
KEY_FAV = "__fav__"
CAT_PREFIX = "cat::"
TAG_PREFIX = "tag::"

GRID_PADDING_X = 24   # 格子水平留白（卡片左右各 7 + 空隙）
GRID_CARD_GAP = 7     # 格子边缘到卡片的留白


class _CardDelegate(QtWidgets.QStyledItemDelegate):
    """网格卡片：缩略图 + 名字（颜色竖条）/ 类型·节点数 / 版本 / 标签。

    参考官方 Recipe Manager 卡片布局，去掉"使用次数"，且类型只在第二行
    出现一次（标签行不再重复网络类别）。整体自绘（不调父类 paint），
    选中态画高亮边框。行高公式与 text_block_height 必须保持一致。
    """

    MARGIN = 8        # 卡片内边距（缩略图/文字与卡边距离）
    TEXT_TOP_GAP = 5  # 缩略图与名字行间距
    LINE_GAP = 3      # 文字行间距
    TEXT_BOTTOM_PAD = 3  # 标签行到底边的留白（比 MARGIN 紧，底部不空）
    BAR_W = 3         # 名字旁颜色竖条宽度

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._win = window

    @staticmethod
    def _font_metrics(pixel_size, bold):
        f = QtWidgets.QApplication.font()
        f.setPixelSize(pixel_size)
        f.setBold(bold)
        return QtGui.QFontMetrics(f)

    @staticmethod
    def _tint(color, alpha):
        """主题色按 alpha 比例混入卡片暗底，得文字区的浅色调背景。"""
        base = QtGui.QColor("#1D1D20")
        c = QtGui.QColor(color)

        def mix(a, b):
            return round(a * (1 - alpha) + b * alpha)

        return QtGui.QColor(mix(base.red(), c.red()),
                            mix(base.green(), c.green()),
                            mix(base.blue(), c.blue()))

    @staticmethod
    def _readable(color):
        """标签行文字：主题色太暗时提亮，保证浅色调背景上可读。"""
        c = QtGui.QColor(color)
        if c.lightness() < 90:
            c = c.lighter(180)
        return c

    @classmethod
    def text_block_height(cls):
        """缩略图以下文字区的总高度（与 paint 的行序严格一致）。"""
        name_h = cls._font_metrics(13, True).height()
        line_h = cls._font_metrics(12, True).height()   # 三行小字也加粗
        return (cls.TEXT_TOP_GAP + name_h + cls.LINE_GAP
                + (line_h + cls.LINE_GAP) * 3 + cls.TEXT_BOTTOM_PAD)

    def sizeHint(self, option, index):
        return self._win.list.gridSize()

    def paint(self, painter, option, index):
        info = self._win._info_by_name.get(index.data(QtCore.Qt.UserRole))
        thumb_h = self._win.thumb_height()
        selected = bool(option.state & QtWidgets.QStyle.State_Selected)

        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

    def paint(self, painter, option, index):
        info = self._win._info_by_name.get(index.data(QtCore.Qt.UserRole))
        thumb_h = self._win.thumb_height()
        selected = bool(option.state & QtWidgets.QStyle.State_Selected)

        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        # 整卡主题（参考官方 Recipe Manager 卡片）：自定义颜色时边框/名字
        # 竖条/标签行用主题色，背景铺主题色混暗底——文字区 0.35、缩略图区
        # 更暗 0.12 分出层次；未设置颜色时为默认灰主题（选中亮蓝边框）
        custom = metadata.get_color(info.name) if info else ""
        if custom:
            theme = QtGui.QColor(custom)
            bar = QtGui.QColor(theme)
            tag_color = self._readable(theme)
        else:
            theme = QtGui.QColor("#9a9aa2")
            bar = QtGui.QColor("#e8e8ec")
            tag_color = QtGui.QColor("#c0c0c8")
        bg = self._tint(theme, 0.35)
        if selected:
            bg = bg.lighter(115)

        card = option.rect.adjusted(GRID_CARD_GAP, GRID_CARD_GAP,
                                    -GRID_CARD_GAP, -GRID_CARD_GAP)
        path = QtGui.QPainterPath()
        path.addRoundedRect(QtCore.QRectF(card), 8, 8)
        painter.fillPath(path, bg)

        m = self.MARGIN
        text_w = card.width() - m * 2
        y = card.top() + m

        # 缩略图区（clip 进卡片圆角）比文字区更暗一档，再 contain 居中画
        # 缩略图；收藏角标单独贴在缩略图区（卡片内容区）右上角——合成进
        # 图标的话位置随图片留白漂移，贴不到卡片角
        painter.save()
        painter.setClipPath(path)
        painter.fillRect(QtCore.QRectF(card.left() + m, y, text_w, thumb_h),
                         self._tint(theme, 0.12))
        painter.restore()
        icon = index.data(QtCore.Qt.DecorationRole)
        if icon is not None:
            pm = icon.pixmap(text_w, thumb_h)
            if not pm.isNull():
                painter.drawPixmap(
                    card.left() + m + (text_w - pm.width()) // 2,
                    y + (thumb_h - pm.height()) // 2, pm)
        if info is not None and metadata.is_favorite(info.name):
            badge_size = max(12, min(24, int(text_w * 0.12)))
            badge = self._win._badge.badge_pixmap(badge_size)
            margin = max(3, badge_size // 8)
            pad = (badge.width() - badge_size) // 2  # 画布含投影余量
            painter.drawPixmap(
                int(card.left() + m + text_w - badge_size - margin - pad),
                int(card.top() + m + margin - pad), badge)
        y += thumb_h + self.TEXT_TOP_GAP

        border = theme if custom else QtGui.QColor(
            "#0d6399" if selected else "#9a9aa2")
        painter.setPen(QtGui.QPen(border, 2 if selected else 1))
        painter.drawPath(path)

        if info is None:  # 理论不达（条目都带 UserRole）；兜底不画文字
            painter.restore()
            return

        # 行1：颜色竖条 + 名字（粗体白，超长省略）
        nfm = self._font_metrics(13, True)
        painter.setFont(self._font(13, True))   # 度量之外必须真正设置字体
        bar_h = nfm.height()
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(bar)
        painter.drawRoundedRect(QtCore.QRectF(card.left() + m, y + 1,
                                              self.BAR_W, bar_h - 2),
                                1.5, 1.5)
        name = nfm.elidedText(self._win._display_label(info),
                              QtCore.Qt.ElideRight, text_w - self.BAR_W - 6)
        painter.setPen(QtGui.QColor("#eeeeee"))
        painter.drawText(QtCore.QRect(card.left() + m + self.BAR_W + 6, y,
                                      text_w - self.BAR_W - 6, bar_h),
                         QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, name)
        y += bar_h + self.LINE_GAP

        # 行2-4：类型·节点数 / 版本 / 标签（全部加粗）
        painter.setFont(self._font(12, True))
        line_h = painter.fontMetrics().height()

        def _line(text, color):
            painter.setPen(QtGui.QPen(QtGui.QColor(color)))
            painter.drawText(QtCore.QRect(card.left() + m, y, text_w, line_h),
                             QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                             text)
            return y + line_h + self.LINE_GAP

        y = _line(self._type_line(info), "#a8a8b0")
        y = _line(store.version_label(info.houdini_version), "#a8a8b0")
        _line(" ".join("#" + t for t in metadata.get_tags(info.name)),
              tag_color)
        painter.restore()

    @staticmethod
    def _font(pixel_size, bold):
        f = QtWidgets.QApplication.font()
        f.setPixelSize(pixel_size)
        f.setBold(bold)
        return f

    @staticmethod
    def _type_line(info):
        """第二行：网络类别（缺省用类别短名）+ 节点数。"""
        parts = []
        cat = info.net_category or {
            "tool": "Tool", "node": "Node", "parm": "Parm",
            "decoration": "Deco", "parmTemplate": "ParmTpl",
            "data": "Data"}.get(info.category, "")
        if cat:
            parts.append(cat)
        if info.node_count >= 0:
            parts.append("{} 节点".format(info.node_count))
        return " • ".join(parts)

_UI_SETTINGS = JsonStore("recipelib_ui.json", defaults={
    "grid_size": 112,     # 缩略图基准大小（滑条 64-256）
    "pin_on_top": False,  # 全局置顶，按机器记住
})


class _Grid(QtWidgets.QListWidget):
    """缩略图网格：识别"按住左键拖动"手势交给窗口（自研拖拽入网）；
    左键点在条目外的空白处则取消选中。"""

    dragStarted = QtCore.Signal(object)
    emptyClicked = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._press_pos = None

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._press_pos = event.position().toPoint()
            if self.itemAt(self._press_pos) is None:
                self.emptyClicked.emit()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_pos is not None \
                and event.buttons() & QtCore.Qt.LeftButton \
                and (event.position().toPoint() - self._press_pos) \
                .manhattanLength() \
                >= QtWidgets.QApplication.startDragDistance():
            item = self.itemAt(self._press_pos)
            self._press_pos = None
            if item is not None:
                self.dragStarted.emit(item)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press_pos = None
        super().mouseReleaseEvent(event)


class _PreviewNameLabel(QtWidgets.QLabel):
    """预览面板的名称标签：双击可自定义显示名（支持中文）。"""

    nameDoubleClicked = QtCore.Signal()

    def mouseDoubleClickEvent(self, event):
        self.nameDoubleClicked.emit()
        super().mouseDoubleClickEvent(event)


class _LibraryDirsDialog(QtWidgets.QDialog):
    """库文件夹管理对话框：可配置多个，列表即加载顺序。

    创建 recipe 用 Houdini 官方保存流程（位置指向库文件夹里的 .hda）；
    其余文件夹常用来挂共享库（队友/项目的 recipe .hda 直接丢进去就被扫描）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Recipe 库文件夹")
        self.resize(580, 340)

        self.list = QtWidgets.QListWidget()
        self.list.addItems(metadata.get_lib_dirs())

        add_btn = QtWidgets.QPushButton("添加文件夹...")
        rm_btn = QtWidgets.QPushButton("移除选中")
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        hint = QtWidgets.QLabel(
            "每个文件夹里任意层级的 .hda 都会被扫描加载（官方出厂 recipes "
            "不加载）。创建 recipe 用 Houdini 官方保存流程（网络编辑器右键 "
            "▸ Recipes ▸ Save），位置指向库文件夹里的 .hda；"
            "或直接把 .hda 文件放进文件夹。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888888;")

        side = QtWidgets.QVBoxLayout()
        side.addWidget(add_btn)
        side.addWidget(rm_btn)
        side.addStretch(1)

        body = QtWidgets.QHBoxLayout()
        body.addWidget(self.list, 1)
        body.addLayout(side)

        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(hint)
        lay.addLayout(body, 1)
        lay.addWidget(buttons)

        add_btn.clicked.connect(self._add_dir)
        rm_btn.clicked.connect(self._remove_dir)

    def _add_dir(self):
        start = self.list.currentItem().text() if self.list.currentItem() \
            else ""
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self, "选择 recipe 库文件夹", start)
        if not d:
            return
        d = os.path.abspath(d)
        for i in range(self.list.count()):
            if os.path.abspath(self.list.item(i).text()) == d:
                self.list.setCurrentRow(i)  # 已存在：选中即可，不重复
                return
        self.list.addItem(d)
        self.list.setCurrentRow(self.list.count() - 1)

    def _remove_dir(self):
        row = self.list.currentRow()
        if row >= 0:
            self.list.takeItem(row)

    def dirs(self):
        return [self.list.item(i).text() for i in range(self.list.count())]


class _RecipeLibraryWindow(QtWidgets.QWidget):
    """Recipe Library 主窗口（window_manager 单例登记）。"""

    PREVIEW_W = 272
    PREVIEW_H = 190

    STYLE_SHEET = """
        QWidget { background-color: #18181b; color: #dddddd; }
        QPushButton {
            background-color: #2d2d2d; border: 1px solid #3d3d3d;
            border-radius: 4px; padding: 4px 14px;
        }
        QPushButton:hover { background-color: #3d3d3d; border-color: #0d6399; }
        QPushButton:pressed { background-color: #0d6399; }
        QPushButton:disabled { color: #666666; background-color: #232326; }
        QMenu {
            background-color: #1D1D20; border: 1px solid #3d3d3d; padding: 4px;
        }
        QMenu::item {
            color: #bbbbbb; padding: 5px 28px 5px 14px; border-radius: 4px;
        }
        QMenu::item:selected { background-color: #0d6399; color: #ffffff; }
        QMenu::item:disabled { color: #666666; }
        QMenu::separator { height: 1px; background: #3d3d3d; margin: 4px 6px; }
        QLabel#statusLabel { color: #888888; padding: 4px 8px; }
        QListWidget, QPlainTextEdit, QTextBrowser {
            background-color: #1D1D20; border: 1px solid #3d3d3d;
            border-radius: 6px;
        }
        QListWidget::item { color: #bbbbbb; }
        QListWidget::item:selected { background-color: #0d6399; }
        QLineEdit {
            background-color: #1D1D20; border: 1px solid #3d3d3d;
            border-radius: 4px; padding: 4px 8px;
        }
        QLineEdit:focus { border-color: #0d6399; }
        QSplitter::handle:horizontal { background: #2d2d2d; width: 2px; }
        QSlider::groove:horizontal { height: 4px; background: #3d3d3d; border-radius: 2px; }
        QSlider::handle:horizontal {
            background: #0d6399; width: 12px; margin: -5px 0; border-radius: 6px;
        }
    """

    def __init__(self, parent=None, flags=None):
        if flags is None:
            flags = QtCore.Qt.Window
        super().__init__(parent, flags)
        self.setWindowTitle("Recipe Library")
        self.resize(1240, 680)
        self.setStyleSheet(self.STYLE_SHEET)
        apply_appwindow_flags(self)

        self._recipes = []          # 全量 RecipeInfo（store.list_recipes）
        self._info_by_name = {}
        self._category_key = KEY_ALL
        self._thumb_cache = {}      # name -> QIcon（GIF 首帧也在这里）
        self._preview_movie = None
        self._doc_dialog = None
        self._preview_info = None
        self._drag_state = None     # 拖拽中: {name, ghost}
        self._placeholder = self._placeholder_icon()
        self._badge = FavoriteBadge()   # 收藏角标（共享组件，见 ui.badge；
                                        # delegate 画在卡片右上角，非合成进图标）
        self._loaded = False        # 首次 show 时自动枚举（见 showEvent）

        # ---- 顶部栏 ----
        self.refresh_btn = QtWidgets.QPushButton("刷新")
        self.lib_btn = QtWidgets.QPushButton("库目录...")
        self.lib_btn.setToolTip("管理 recipe 库文件夹（可多个，递归扫描其中的"
                                " .hda；官方出厂 recipes 不加载。创建 recipe "
                                "请用 Houdini 官方保存流程，把位置指到库文件夹）")
        self.lib_label = QtWidgets.QLabel()
        self.lib_label.setStyleSheet("color: #888888;")
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("搜索 名称 / 标签 / 备注...")
        self.search.setClearButtonEnabled(True)
        self.pin_chk = QtWidgets.QCheckBox("全局置顶")
        self.pin_chk.setChecked(bool(_UI_SETTINGS.get("pin_on_top")))
        self.size_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.size_slider.setRange(64, 256)
        self.size_slider.setValue(int(_UI_SETTINGS.get("grid_size")))
        self.size_slider.setFixedWidth(140)
        self.size_slider.setToolTip("缩略图基准大小")
        self.size_label = QtWidgets.QLabel("{}px".format(self.size_slider.value()))

        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.refresh_btn)
        top.addSpacing(8)
        top.addWidget(self.lib_btn)
        top.addSpacing(4)
        top.addWidget(self.lib_label)
        top.addSpacing(12)
        top.addWidget(self.search, 1)
        top.addSpacing(12)
        top.addWidget(self.pin_chk)
        top.addSpacing(12)
        top.addWidget(QtWidgets.QLabel("大小:"))
        top.addWidget(self.size_slider)
        top.addWidget(self.size_label)

        # ---- 左侧栏：全部 / 收藏 / 分类 / 标签 ----
        self.sidebar = QtWidgets.QListWidget()
        self.sidebar.setMinimumWidth(150)
        self.sidebar.setMaximumWidth(280)
        self.sidebar.currentItemChanged.connect(self._on_category_changed)

        # ---- 中部网格 ----
        self.list = _Grid()
        self.list.setViewMode(QtWidgets.QListWidget.IconMode)
        self.list.setMovement(QtWidgets.QListWidget.Static)
        self.list.setResizeMode(QtWidgets.QListWidget.Adjust)
        self.list.setLayoutMode(QtWidgets.QListWidget.Batched)
        # UniformItemSizes 必须关（离屏渲染实测）：缩略图原图尺寸超过
        # iconSize 时（竖版 GIF/PNG 很常见），条目 sizeHint 按原图算、
        # 超出 gridSize，Uniform 模式按首个条目的尺寸强行裁剪——表现为
        # 该条目文字标签整个不可见、图标比例失真。本工具条目量小（用户
        # 库），不需要 Uniform 的布局优化
        self.list.setUniformItemSizes(False)
        self.list.setWordWrap(True)
        # 卡片式条目：缩略图 + 名字/类型/版本/标签（自绘 delegate，见下）
        self._thumb_h = 0
        self.list.setItemDelegate(_CardDelegate(self))
        self.list.itemDoubleClicked.connect(self._on_double_click)
        self.list.currentItemChanged.connect(self._on_selection_changed)
        self.list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_context_menu)
        self.list.dragStarted.connect(self._start_drag)
        self.list.emptyClicked.connect(self._clear_selection)
        self._apply_grid_size()

        # ---- 右侧预览面板 ----
        self.preview_label = QtWidgets.QLabel()
        self.preview_label.setFixedSize(self.PREVIEW_W, self.PREVIEW_H)
        self.preview_label.setAlignment(QtCore.Qt.AlignCenter)
        self.preview_label.setStyleSheet(
            "background-color: #1D1D20; border: 1px solid #3d3d3d; "
            "border-radius: 6px; color: #666666;")

        self.preview_name = _PreviewNameLabel()
        self.preview_name.setWordWrap(True)
        self.preview_name.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.preview_name.setToolTip("双击可自定义显示名称（支持中文）")
        self.preview_name.nameDoubleClicked.connect(self._rename_selected)
        self.preview_meta = QtWidgets.QLabel()
        self.preview_meta.setWordWrap(True)
        self.preview_meta.setStyleSheet("color: #888888;")
        self.preview_comment = QtWidgets.QLabel()
        self.preview_comment.setWordWrap(True)
        self.preview_comment.setStyleSheet("color: #aaaaaa;")
        # 有文档时直接渲染 markdown 文档内容（内联 GIF/视频按钮），
        # 无文档时隐藏、显示官方备注 comment
        self.preview_doc = MarkdownMediaView("")
        self.preview_doc.setStyleSheet(
            "color: #cccccc; background: transparent; border: none;")

        self.tags_edit = QtWidgets.QLineEdit()
        self.tags_edit.setPlaceholderText("标签，逗号分隔")
        self.tags_apply_btn = QtWidgets.QPushButton("更新标签")

        self.fav_btn = QtWidgets.QPushButton("☆ 收藏")
        self.doc_btn = QtWidgets.QPushButton("编辑文档...")
        self.doc_btn.setToolTip("Markdown 编辑 + 实时预览，可插入图片和视频")
        self.place_btn = QtWidgets.QPushButton("应用 / 放置")
        self.thumb_btn = QtWidgets.QPushButton("设置缩略图...")

        pv = QtWidgets.QVBoxLayout()
        pv.setContentsMargins(0, 0, 0, 0)
        pv.addWidget(self.preview_label)
        pv.addWidget(self.preview_name)
        pv.addWidget(self.preview_meta)
        pv.addWidget(self.preview_comment, 1)
        pv.addWidget(self.preview_doc, 1)
        tag_row = QtWidgets.QHBoxLayout()
        tag_row.addWidget(self.tags_edit, 1)
        tag_row.addWidget(self.tags_apply_btn)
        pv.addLayout(tag_row)
        btn_grid = QtWidgets.QGridLayout()
        btn_grid.addWidget(self.fav_btn, 0, 0)
        btn_grid.addWidget(self.doc_btn, 0, 1)
        btn_grid.addWidget(self.place_btn, 1, 0)
        btn_grid.addWidget(self.thumb_btn, 1, 1)
        pv.addLayout(btn_grid)

        preview_panel = QtWidgets.QWidget()
        preview_panel.setLayout(pv)
        preview_panel.setFixedWidth(self.PREVIEW_W + 24)

        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.splitter.setHandleWidth(4)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(self.list)
        self.splitter.addWidget(preview_panel)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([190, 740, 300])

        self.status = QtWidgets.QLabel("双击卡片应用；或按住卡片拖入网络编辑器。")
        self.status.setObjectName("statusLabel")

        lay = QtWidgets.QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.splitter, 1)
        lay.addWidget(self.status)

        self.refresh_btn.clicked.connect(self.reload)
        self.lib_btn.clicked.connect(self._manage_lib_dirs)
        self.pin_chk.toggled.connect(self._toggle_pin)
        self.size_slider.valueChanged.connect(self._on_size_changed)
        self.place_btn.clicked.connect(self._apply_selected)
        self.fav_btn.clicked.connect(self._toggle_fav_selected)
        self.doc_btn.clicked.connect(self._open_doc)
        self.thumb_btn.clicked.connect(self._set_thumb_selected)
        self.tags_apply_btn.clicked.connect(self._apply_tags)

        self._search_timer = QtCore.QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._apply_filter)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())

        self._clear_preview()

    # ---------------- 数据加载与过滤 ----------------

    def reload(self):
        """枚举用户库里的 recipe 并重建侧栏/网格（保留分类与搜索词）。"""
        metadata.migrate_legacy_thumb_paths()  # 旧版绝对路径 → 相对插件根
        lib_dirs = metadata.get_lib_dirs()
        self._update_lib_label()
        if not lib_dirs:
            self._recipes = []
            self._info_by_name = {}
            self._rebuild_sidebar()
            self._apply_filter(note="；请先点「库目录...」设置库文件夹")
            return
        try:
            self._recipes = store.list_recipes(lib_dirs)
        except Exception as exc:
            log.warning("list recipes failed: %s", exc, exc_info=True)
            self._recipes = []
            self.status.setText("枚举 recipe 失败: {}".format(exc))
            return
        self._info_by_name = {r.name: r for r in self._recipes}
        self._rebuild_sidebar()
        self._apply_filter(
            note="，共 {} 个".format(len(self._recipes)))

    def _update_lib_label(self):
        dirs = metadata.get_lib_dirs()
        if not dirs:
            self.lib_label.setText("（未设置库文件夹）")
            self.lib_label.setToolTip("")
            return
        if len(dirs) == 1:
            text = dirs[0]
        else:
            text = "{} | 等 {} 个".format(dirs[0], len(dirs))
        fm = self.lib_label.fontMetrics()
        self.lib_label.setText("库: " + fm.elidedText(
            text, QtCore.Qt.ElideMiddle, 260))
        self.lib_label.setToolTip("\n".join(dirs))

    def _manage_lib_dirs(self):
        dlg = _LibraryDirsDialog(self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            metadata.set_lib_dirs(dlg.dirs())
            self._category_key = KEY_ALL
            self.reload()

    def _rebuild_sidebar(self):
        """全部 / 收藏 / 分类(submenu) / 标签 四段侧栏，带计数。

        分类取自官方字段（node preset 的 submenu、tool 的 tab_submenu）；
        标签是本工具自己的元数据层。空分类显示为"未分类"。
        """
        cat_counts = {}
        tag_counts = {}
        fav_count = 0
        for r in self._recipes:
            for sub in self._submenus_of(r):
                cat_counts[sub] = cat_counts.get(sub, 0) + 1
            tags = metadata.get_tags(r.name)
            for t in tags:
                tag_counts[t] = tag_counts.get(t, 0) + 1
            if metadata.is_favorite(r.name):
                fav_count += 1

        self.sidebar.blockSignals(True)
        self.sidebar.clear()

        def _add(key, label, count, header=False):
            item = QtWidgets.QListWidgetItem(
                label if header else "{} ({})".format(label, count))
            item.setData(QtCore.Qt.UserRole, key)
            if header:
                item.setFlags(QtCore.Qt.ItemIsEnabled)
                item.setForeground(QtGui.QColor("#666666"))
            self.sidebar.addItem(item)

        _add(KEY_ALL, "全部", len(self._recipes))
        _add(KEY_FAV, "★ 收藏", fav_count)
        _add(None, "── 分类 ──", 0, header=True)
        for sub in sorted(cat_counts, key=str.lower):
            _add(CAT_PREFIX + sub, sub or "未分类", cat_counts[sub])
        _add(None, "── 标签 ──", 0, header=True)
        for tag in sorted(tag_counts, key=str.lower):
            _add(TAG_PREFIX + tag, tag, tag_counts[tag])

        row = next((i for i in range(self.sidebar.count())
                    if self.sidebar.item(i).data(QtCore.Qt.UserRole)
                    == self._category_key), 0)
        self.sidebar.setCurrentRow(row)
        self.sidebar.blockSignals(False)

    @staticmethod
    def _submenus_of(info):
        return [s.strip() for s in (info.submenu or "").split(",") if s.strip()]

    def _on_category_changed(self, current, _previous=None):
        if current is None:
            return
        self._category_key = current.data(QtCore.Qt.UserRole) or KEY_ALL
        self._apply_filter()

    def _select_category(self, key):
        """编程式切分类（测试入口）。"""
        self._category_key = key
        self._apply_filter()
        self.sidebar.blockSignals(True)
        for i in range(self.sidebar.count()):
            if self.sidebar.item(i).data(QtCore.Qt.UserRole) == key:
                self.sidebar.setCurrentRow(i)
                break
        self.sidebar.blockSignals(False)

    def _display_label(self, info):
        """网格/预览用的显示名链：用户自定义名 > 官方 label >
        内部名末段（自定义名支持中文，存元数据层）。"""
        custom = metadata.get_display_name(info.name)
        if custom:
            return custom
        return info.display_label

    def _match_search(self, info):
        text = self.search.text().strip().lower()
        if not text:
            return True
        tags = " ".join(metadata.get_tags(info.name))
        hay = " ".join([self._display_label(info), info.name,
                        info.comment, tags])
        return text in hay.lower()

    def _match_category(self, info):
        key = self._category_key
        if key in (KEY_ALL, None):
            return True
        if key == KEY_FAV:
            return metadata.is_favorite(info.name)
        if key.startswith(CAT_PREFIX):
            sub = key[len(CAT_PREFIX):]
            return sub in self._submenus_of(info)
        if key.startswith(TAG_PREFIX):
            tag = key[len(TAG_PREFIX):]
            return tag in metadata.get_tags(info.name)
        return True

    def _apply_filter(self, note=""):
        """重建网格条目（hdrlight 同款结论：IconMode+gridSize 下
        setHidden 的条目仍占槽位，必须重建式过滤）。"""
        entries = [r for r in self._recipes
                   if self._match_category(r) and self._match_search(r)]
        self.list.setUpdatesEnabled(False)
        try:
            self.list.clear()
            for info in entries:
                item = QtWidgets.QListWidgetItem()
                item.setData(QtCore.Qt.UserRole, info.name)
                # 收藏不加名字前缀，角标由卡片 delegate 画在卡片右上角
                item.setText(self._display_label(info))
                item.setToolTip(self._tooltip_for(info))
                item.setIcon(self._base_icon(info))
                self.list.addItem(item)
        finally:
            self.list.setUpdatesEnabled(True)
        self.status.setText("{}：{}/{} 个 recipe{}。{}".format(
            self._category_label(), len(entries), len(self._recipes), note,
            "双击应用；或按住拖入网络编辑器。"))
        if self.list.count() == 0:
            self._clear_preview()

    def _category_label(self):
        key = self._category_key
        if key in (KEY_ALL, None):
            return "全部"
        if key == KEY_FAV:
            return "★ 收藏"
        if key.startswith(CAT_PREFIX):
            return key[len(CAT_PREFIX):] or "未分类"
        if key.startswith(TAG_PREFIX):
            return "标签 " + key[len(TAG_PREFIX):]
        return key

    def _tooltip_for(self, info):
        lines = [self._display_label(info), "内部名称: " + info.name]
        meta = []
        if info.net_category:
            meta.append(info.net_category)
        if info.node_count >= 0:
            meta.append("{} 个节点".format(info.node_count))
        if meta:
            lines.insert(2, " • ".join(meta))
        if info.houdini_version:
            lines.append("版本: {}（{}）".format(
                info.houdini_version,
                store.version_label(info.houdini_version)))
        tags = metadata.get_tags(info.name)
        if tags:
            lines.append("标签: " + ", ".join(tags))
        if info.comment:
            lines.append(info.comment)
        if info.patterns:
            lines.append("作用: " + ", ".join(info.patterns))
        return "\n".join(lines)

    # ---------------- 图标与预览 ----------------

    def _base_icon(self, info):
        """无角标的底图（按内部名缓存）。"""
        icon = self._thumb_cache.get(info.name)
        if icon is not None:
            return icon
        thumb = metadata.get_thumb(info.name)
        if thumb:
            if thumb.lower().endswith(".gif"):
                movie = QtGui.QMovie(thumb)
                movie.jumpToFrame(0)
                pm = movie.currentPixmap()
                movie.stop()
                icon = QtGui.QIcon(pm) if not pm.isNull() else None
            else:
                icon = QtGui.QIcon(thumb)
        if icon is None:
            return self._placeholder
        self._thumb_cache[info.name] = icon
        return icon

    def _placeholder_icon(self):
        # 256px 档：QIcon 不会把小图放大，占位图太小会在大网格下与真实
        # 缩略图尺寸不一致；给足尺寸让各档位都由 QIcon 缩小呈现
        pm = QtGui.QPixmap(256, 168)
        pm.fill(QtGui.QColor("#313136"))
        painter = QtGui.QPainter(pm)
        painter.setPen(QtGui.QColor("#666666"))
        f = painter.font()
        f.setPixelSize(22)
        painter.setFont(f)
        painter.drawText(pm.rect(), QtCore.Qt.AlignCenter, "recipe")
        painter.end()
        return QtGui.QIcon(pm)

    def _on_size_changed(self, val):
        self.size_label.setText("{}px".format(val))
        self._apply_grid_size()

    def thumb_height(self):
        """当前卡片缩略图区高度（delegate paint 与 gridSize 共用）。"""
        return self._thumb_h

    def _apply_grid_size(self):
        base = int(self.size_slider.value())   # 缩略图宽度基准
        self._thumb_h = int(base * 0.66)
        self.list.setIconSize(QtCore.QSize(base, self._thumb_h))
        card_w = base + _CardDelegate.MARGIN * 2
        grid_h = (GRID_CARD_GAP + _CardDelegate.MARGIN + self._thumb_h
                  + _CardDelegate.text_block_height() + GRID_CARD_GAP)
        self.list.setGridSize(QtCore.QSize(
            card_w + GRID_CARD_GAP * 2, grid_h))

    def _on_selection_changed(self, current, _previous=None):
        if current is None:
            self._clear_preview()
            return
        info = self._info_by_name.get(current.data(QtCore.Qt.UserRole))
        if info is not None:
            self._update_preview(info)

    def _selected_info(self):
        item = self.list.currentItem()
        if item is None:
            return None
        return self._info_by_name.get(item.data(QtCore.Qt.UserRole))

    def _clear_selection(self):
        """点空白处取消选中：currentItem 置空，预览面板随之复位。"""
        self.list.setCurrentRow(-1)

    def _clear_preview(self):
        self._stop_preview_movie()
        self.preview_label.setPixmap(QtGui.QPixmap())
        self.preview_label.setText("未选中")
        self.preview_name.setText("")
        self.preview_meta.setText("")
        self.preview_comment.setText("")
        self.preview_doc.setVisible(False)
        self.tags_edit.setText("")
        for btn in (self.fav_btn, self.doc_btn, self.place_btn,
                    self.thumb_btn, self.tags_apply_btn):
            btn.setEnabled(False)

    def _update_preview(self, info):
        for btn in (self.fav_btn, self.doc_btn, self.place_btn,
                    self.thumb_btn, self.tags_apply_btn):
            btn.setEnabled(True)
        self._preview_info = info
        fav = metadata.is_favorite(info.name)
        self.fav_btn.setText("★ 已收藏" if fav else "☆ 收藏")
        self.preview_name.setText(self._display_label(info))
        lib = os.path.basename(info.library) if info.library else ""
        meta_lines = [
            "内部名称: " + info.name,
            "类型: " + store.CATEGORY_LABELS.get(info.category, info.category),
            "分类: " + (info.submenu or "（未分类）"),
        ]
        if lib:
            meta_lines.append("来源: " + lib)
        if info.net_category:
            meta_lines.append("网络: " + info.net_category)
        if info.node_count >= 0:
            meta_lines.append("节点数: {}".format(info.node_count))
        if info.houdini_version:
            meta_lines.append("版本: {}（{}）".format(
                info.houdini_version,
                store.version_label(info.houdini_version)))
        if info.patterns:
            meta_lines.append("作用: " + ", ".join(info.patterns))
        self.preview_meta.setText("\n".join(meta_lines))
        # 文档区：有文档渲染 markdown（内联 GIF/视频按钮），
        # 无文档回退显示官方备注
        if metadata.doc_exists(info.name):
            self.preview_doc.set_doc_dir(metadata.doc_dir(info.name))
            self.preview_doc.set_markdown_with_media(
                metadata.read_doc(info.name))
            self.preview_doc.setVisible(True)
            self.preview_doc.setEnabled(True)
            self.preview_comment.setVisible(False)
        else:
            self.preview_doc.setVisible(False)
            self.preview_comment.setVisible(True)
            self.preview_comment.setText(
                info.comment or "（无备注——点「编辑文档」补一篇用法说明）")
        self.tags_edit.setText(", ".join(metadata.get_tags(info.name)))

        # 大图预览：GIF 动起来，其余静态缩放
        self._stop_preview_movie()
        self.preview_label.setText("")
        thumb = metadata.get_thumb(info.name)
        if thumb and thumb.lower().endswith(".gif"):
            movie = QtGui.QMovie(self.preview_label)
            movie.setFileName(thumb)
            movie.setCacheMode(QtGui.QMovie.CacheAll)
            movie.jumpToFrame(0)
            pm = movie.currentPixmap()
            if not pm.isNull():
                scaled = pm.size().scaled(
                    QtCore.QSize(self.PREVIEW_W - 8, self.PREVIEW_H - 8),
                    QtCore.Qt.KeepAspectRatio)
                movie.setScaledSize(scaled)
            self.preview_label.setMovie(movie)
            movie.start()
            self._preview_movie = movie
        elif thumb:
            pm = QtGui.QPixmap(thumb)
            if not pm.isNull():
                self.preview_label.setPixmap(pm.scaled(
                    self.PREVIEW_W - 8, self.PREVIEW_H - 8,
                    QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
            else:
                self._clear_preview_image()
        else:
            self._clear_preview_image()

    def _clear_preview_image(self):
        self.preview_label.setText("无缩略图\n（右键卡片 → 设置缩略图）")

    def _stop_preview_movie(self):
        movie = self._preview_movie
        if movie is not None:
            try:
                movie.stop()
            except RuntimeError:
                pass
        self.preview_label.setMovie(None)
        self._preview_movie = None

    # ---------------- 应用（双击 / 按钮） ----------------

    def _on_double_click(self, item):
        info = self._info_by_name.get(item.data(QtCore.Qt.UserRole))
        if info is not None:
            self._apply_recipe(info)

    def _apply_selected(self):
        info = self._selected_info()
        if info is not None:
            self._apply_recipe(info)

    def _apply_recipe(self, info):
        """按类型分发应用；错误进状态栏不打断窗口。

        Tool 走官方工具架参数（立即在当前网络创建并 frame 框选），
        与"点工架按钮"同体验；预设/装饰类需要先选中目标节点。
        """
        try:
            if info.category == "tool":
                editor = store.current_network_editor()
                if editor is None:
                    self.status.setText("找不到网络编辑器面板，请先打开一个网络视图")
                    return
                store.apply_tool_recipe(info.name, pane=editor, mode="shelf")
                self.status.setText("已把「{}」创建到当前网络".format(
                    info.display_label))
            elif info.category == "node":
                nodes = store.selected_nodes()
                if not nodes:
                    self.status.setText("Node 预设：请先在场景中选中目标节点")
                    return
                store.apply_node_preset(info.name, nodes[0])
                self.status.setText("已把预设应用到 {}".format(nodes[0].path()))
            elif info.category == "parm":
                nodes = store.selected_nodes()
                if not nodes:
                    self.status.setText("参数预设：请先在场景中选中目标节点")
                    return
                store.apply_parm_preset(info.name, nodes[0])
                self.status.setText("已把参数预设应用到 {}".format(nodes[0].path()))
            elif info.category == "decoration":
                nodes = store.selected_nodes()
                if not nodes:
                    self.status.setText("Decoration：请先选中中心节点")
                    return
                store.apply_decoration(info.name, nodes[0])
                self.status.setText("已把装饰应用到 {}".format(nodes[0].path()))
            else:
                self.status.setText(
                    "类型 {} 暂不支持面板内应用，请用官方菜单".format(info.category))
        except store.ContextMismatch as exc:
            # 预期内的操作反馈（层级点错）：红字提示即可，控制台保持安静
            self.status.setText(str(exc))
            _push_houdini_error(str(exc))
        except Exception as exc:
            log.warning("apply %s failed: %s", info.name, exc, exc_info=True)
            self.status.setText("应用失败: {}".format(exc))
            _push_houdini_error(str(exc))

    # ---------------- 拖拽进网络编辑器 ----------------

    def _start_drag(self, item):
        info = self._info_by_name.get(item.data(QtCore.Qt.UserRole))
        if info is None:
            return
        if info.category != "tool":
            self.status.setText(
                "{} 是 {} 类型：请先选中目标节点后双击应用（拖拽仅支持 Tool）"
                .format(self._display_label(info),
                        store.CATEGORY_LABELS.get(info.category, info.category)))
            return
        icon = self._base_icon(info)
        pm = icon.pixmap(56, 56)
        ghost = QtWidgets.QLabel(None)
        ghost.setPixmap(pm)
        ghost.setWindowFlags(QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint
                             | QtCore.Qt.WindowStaysOnTopHint)
        ghost.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        ghost.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        ghost.setStyleSheet(
            "background-color: rgba(13, 99, 153, 0.35); border-radius: 6px;")
        ghost.move(QtGui.QCursor.pos() + QtCore.QPoint(16, 16))
        ghost.show()
        self._drag_state = {"info": info, "ghost": ghost}
        QtWidgets.QApplication.instance().installEventFilter(self)
        self.status.setText("拖到目标网络编辑器上松手放置（Esc 取消）")

    def eventFilter(self, obj, event):
        state = self._drag_state
        if state is not None:
            et = event.type()
            if et == QtCore.QEvent.MouseMove:
                ghost = state["ghost"]
                ghost.move(QtGui.QCursor.pos() + QtCore.QPoint(16, 16))
                over = store.network_editor_under_cursor() is not None
                ghost.setStyleSheet(
                    "background-color: rgba(13, 99, 153, 0.55);"
                    if over else
                    "background-color: rgba(13, 99, 153, 0.25);")
                return True
            if et == QtCore.QEvent.MouseButtonRelease \
                    and event.button() == QtCore.Qt.LeftButton:
                self._finish_drag()
                return True
            if et == QtCore.QEvent.KeyPress and event.key() == QtCore.Qt.Key_Escape:
                self._cancel_drag()
                return True
        return super().eventFilter(obj, event)

    def _finish_drag(self):
        state = self._drag_state
        if state is None:
            return
        self._teardown_drag()
        info = state["info"]
        pane = store.network_editor_under_cursor()
        if pane is None:
            self.status.setText("落点不在网络编辑器上，已取消放置")
            return
        position = None
        try:
            position = pane.cursorPosition()
        except Exception as exc:
            log.debug("cursorPosition failed, fallback to apply default: %s", exc)
        try:
            store.apply_tool_recipe(info.name, pane=pane, position=position,
                                    mode="drag")
            self.status.setText("已在当前网络中创建 {}".format(
                self._display_label(info)))
        except store.ContextMismatch as exc:
            # 预期内的操作反馈（层级点错）：红字提示即可，控制台保持安静
            self.status.setText(str(exc))
            _push_houdini_error(str(exc))
        except Exception as exc:
            log.warning("drag apply %s failed: %s", info.name, exc, exc_info=True)
            self.status.setText("创建失败: {}".format(exc))
            _push_houdini_error(str(exc))

    def _cancel_drag(self):
        self._teardown_drag()
        self.status.setText("已取消拖拽")

    def _teardown_drag(self):
        state = self._drag_state
        if state is None:
            return
        self._drag_state = None
        QtWidgets.QApplication.instance().removeEventFilter(self)
        ghost = state["ghost"]
        ghost.hide()
        ghost.deleteLater()

    # ---------------- 右键菜单 ----------------

    def _on_context_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        info = self._info_by_name.get(item.data(QtCore.Qt.UserRole))
        if info is None:
            return
        fav = metadata.is_favorite(info.name)
        display = self._display_label(info)
        menu = QtWidgets.QMenu(self)
        act_fav = menu.addAction("取消收藏" if fav else "★ 收藏")
        act_rename = menu.addAction("自定义显示名...")
        act_rename_reset = None
        if display:
            act_rename_reset = menu.addAction("恢复默认显示名")
        act_doc = menu.addAction("编辑文档...")
        act_thumb = menu.addAction("设置缩略图...")
        act_thumb_clear = None
        if metadata.get_thumb(info.name):
            act_thumb_clear = menu.addAction("清除缩略图")
        act_color = menu.addAction("自定义颜色...")
        act_color_clear = None
        if metadata.get_color(info.name):
            act_color_clear = menu.addAction("清除颜色")
        act_copy = menu.addAction("复制内部名")
        menu.addSeparator()
        act_del = None
        if not info.under_hfs:
            act_del = menu.addAction("删除 Recipe...")
        act = menu.exec_(self.list.mapToGlobal(pos))
        if act is act_fav:
            self._set_favorite(info, not fav)
        elif act is act_rename:
            self._rename_selected()
        elif act is not None and act is act_rename_reset:
            metadata.set_display_name(info.name, None)
            self._refresh_current_item()
        elif act is act_doc:
            self._open_doc_for(info)
        elif act is act_thumb:
            self._set_thumb(info)
        elif act is not None and act is act_thumb_clear:
            metadata.clear_thumb(info.name)
            self._thumb_cache.pop(info.name, None)
            item.setIcon(self._base_icon(info))
            self._refresh_current_item()
        elif act is act_color:
            self._set_color(info)
        elif act is not None and act is act_color_clear:
            metadata.set_color(info.name, None)
            self._refresh_current_item()
        elif act is act_copy:
            QtWidgets.QApplication.clipboard().setText(info.name)
        elif act is not None and act is act_del:
            self._delete_recipe(info)

    def _set_color(self, info):
        """卡片颜色框取色（QColorDialog），写元数据层并重刷网格/预览。"""
        current = metadata.get_color(info.name) or "#8a5cf5"
        color = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(current), self,
            "自定义颜色 - {}".format(self._display_label(info)))
        if not color.isValid():
            return
        metadata.set_color(info.name, color.name())
        self._refresh_current_item()
        self.status.setText("{}：颜色已更新（{}）".format(
            self._display_label(info), color.name()))

    def _refresh_current_item(self):
        """当前条目按最新元数据重刷（收藏星标/图标/提示）。"""
        current = self.list.currentItem()
        if current is None:
            return
        info = self._info_by_name.get(current.data(QtCore.Qt.UserRole))
        if info is not None:
            self._apply_filter()
            self._update_preview(info)

    # ---------------- 元数据操作 ----------------

    def _rename_selected(self):
        """自定义显示名（支持中文）；清空输入即恢复默认显示链。"""
        info = self._selected_info() or self._preview_info
        if info is None:
            return
        current = self._display_label(info)
        title, ok = QtWidgets.QInputDialog.getText(
            self, "自定义显示名",
            "显示名称（留空恢复默认，支持中文）：\n内部名称: {}".format(
                info.name),
            text=current)
        if not ok:
            return
        metadata.set_display_name(info.name, title)
        self._rebuild_sidebar()
        self._apply_filter()
        if self._preview_info is not None:
            self._update_preview(self._preview_info)
        if (title or "").strip():
            self.status.setText("「{}」显示名已设为「{}」".format(
                info.name, title.strip()))
        else:
            self.status.setText("「{}」已恢复默认显示名".format(info.name))

    def _set_favorite(self, info, fav):
        metadata.set_favorite(info.name, fav)
        self._rebuild_sidebar()
        self._apply_filter()
        self._update_preview(info)
        self.status.setText("{}：{}".format(
            self._display_label(info), "已收藏" if fav else "已取消收藏"))

    def _toggle_fav_selected(self):
        info = self._selected_info()
        if info is not None:
            self._set_favorite(info, not metadata.is_favorite(info.name))

    def _apply_tags(self):
        info = self._selected_info()
        if info is None:
            return
        tags = [t.strip() for t in self.tags_edit.text().split(",") if t.strip()]
        metadata.set_tags(info.name, tags)
        self._rebuild_sidebar()
        self._apply_filter()
        self._update_preview(info)
        self.status.setText("{}：标签已更新".format(self._display_label(info)))

    def _set_thumb_selected(self):
        info = self._selected_info()
        if info is not None:
            self._set_thumb(info)

    def _set_thumb(self, info):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "设置缩略图 - {}".format(self._display_label(info)), "",
            metadata.MEDIA_FILTER)
        if not path:
            return
        try:
            stored = metadata.set_thumb_from_file(info.name, path)
        except (OSError, RuntimeError) as exc:
            QtWidgets.QMessageBox.warning(self, "设置缩略图", str(exc))
            return
        self._thumb_cache.pop(info.name, None)
        self._apply_filter()
        self._update_preview(info)
        self.status.setText("{}：缩略图已更新（{}）".format(
            self._display_label(info), os.path.basename(stored)))

    def _open_doc(self):
        info = self._selected_info()
        if info is not None:
            self._open_doc_for(info)

    def _open_doc_for(self, info):
        if self._doc_dialog is not None:
            try:
                self._doc_dialog.close()
                self._doc_dialog.deleteLater()
            except RuntimeError:
                pass
        self._doc_dialog = DocEditorDialog(self, info,
                                           display_label=self._display_label(info))
        self._doc_dialog.docSaved.connect(self._on_doc_saved)
        self._doc_dialog.show()
        self._doc_dialog.raise_()

    def _on_doc_saved(self, name):
        """文档编辑器保存后，主面板若正显示同一配方则同步刷新文档预览。"""
        info = self._preview_info
        if info is not None and info.name == name \
                and metadata.doc_exists(name):
            self.preview_doc.set_doc_dir(metadata.doc_dir(name))
            self.preview_doc.set_markdown_with_media(
                metadata.read_doc(name))
            self.preview_doc.setVisible(True)
            self.preview_comment.setVisible(False)

    # ---------------- 删除 ----------------

    def _delete_recipe(self, info):
        answer = QtWidgets.QMessageBox.question(
            self, "删除 Recipe",
            "确定删除「{}」？\n{}\n（缩略图与文档也会一并清理）".format(
                self._display_label(info), info.name),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            store.delete_recipe(info.name)
        except Exception as exc:
            log.warning("delete recipe failed: %s", exc, exc_info=True)
            QtWidgets.QMessageBox.warning(self, "删除 Recipe",
                                          "删除失败: {}".format(exc))
            return
        metadata.clear_thumb(info.name)
        metadata.set_favorite(info.name, False)
        metadata.set_tags(info.name, [])
        metadata.set_display_name(info.name, None)
        metadata.set_color(info.name, None)
        metadata.delete_doc_dir(info.name)
        self._thumb_cache.pop(info.name, None)
        self.reload()
        self.status.setText("已删除 {}".format(info.display_label))

    # ---------------- 窗口生命周期 ----------------

    def _toggle_pin(self, on):
        _UI_SETTINGS.set("pin_on_top", bool(on))
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, on)
        self.show()  # setWindowFlag 会使窗口隐藏，需要重新 show

    def showEvent(self, event):
        super().showEvent(event)
        # 枚举开销小（HDA section 内存读取），开窗即载；无头冒烟测试
        # 不 show，因此 __init__ 保持零 hou 依赖不受影响
        if not self._loaded:
            self._loaded = True
            self.reload()

    def closeEvent(self, event):
        _UI_SETTINGS.set("grid_size", int(self.size_slider.value()))
        if self._doc_dialog is not None:
            try:
                self._doc_dialog.close()
                self._doc_dialog.deleteLater()
            except RuntimeError:
                pass
            self._doc_dialog = None
        if MediaDialog._current is not None:
            try:
                MediaDialog._current.close()
                MediaDialog._current.deleteLater()
            except RuntimeError:
                pass
        self._stop_preview_movie()
        if self._drag_state is not None:
            self._teardown_drag()
        super().closeEvent(event)


# --------------------------------------------------------------------------
# 入口（tools.recipe_library.run 调用）
# --------------------------------------------------------------------------

def show_recipe_library():
    """打开（或置前）Recipe Library 窗口（window_manager 单例登记）。"""
    from houtools.ui import window_manager

    parent = None
    try:
        import hou
        parent = hou.qt.mainWindow()
    except (ImportError, AttributeError):
        pass

    pin = bool(_UI_SETTINGS.get("pin_on_top"))

    def factory():
        flags = QtCore.Qt.Window
        if pin:
            flags |= QtCore.Qt.WindowStaysOnTopHint
        return _RecipeLibraryWindow(parent, flags)

    return window_manager.open_window(TOOL_ID, factory)
