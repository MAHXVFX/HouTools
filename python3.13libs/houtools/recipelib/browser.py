"""Recipe Library 主窗口：基于官方 recipes 的资产浏览/应用/文档面板。

布局：顶部工具栏（新建/刷新/搜索/大小）+ 左侧栏（全部/收藏/分类/标签）
+ 中部缩略图网格 + 右侧预览面板（动图预览/元信息/标签编辑）+ 底部状态栏。

交互（四类 recipe 语义不同，见 store.apply_*）：
- 双击卡片：Tool 进入官方"点击放置"流程（在网络编辑器里点一下落位）；
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
from houtools.recipelib.docs import DocEditorDialog, MediaDialog
from houtools.ui.taskbar import apply_appwindow_flags
from houtools.core.settings import JsonStore

log = get_logger("recipelib.browser")

TOOL_ID = "recipe_library"

KEY_ALL = "__all__"
KEY_FAV = "__fav__"
CAT_PREFIX = "cat::"
TAG_PREFIX = "tag::"

GRID_PADDING_X = 24
GRID_PADDING_Y = 46

_UI_SETTINGS = JsonStore("recipelib_ui.json", defaults={
    "grid_size": 112,     # 缩略图基准大小（滑条 64-256）
    "pin_on_top": False,  # 全局置顶，按机器记住
})


class _Grid(QtWidgets.QListWidget):
    """缩略图网格：识别"按住左键拖动"手势交给窗口（自研拖拽入网）。"""

    dragStarted = QtCore.Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._press_pos = None

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._press_pos = event.position().toPoint()
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


class _LibraryDirsDialog(QtWidgets.QDialog):
    """库文件夹管理对话框：可配置多个，列表即加载顺序。

    「新建 Recipe」落到第一个文件夹的 HouToolsRecipes.hda；其余文件夹
    常用来挂共享库（队友/项目的 recipe .hda 直接丢进去就被扫描）。
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
            "不加载）；「新建 Recipe」存到第一个文件夹的 HouToolsRecipes.hda。")
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
        self._drag_state = None     # 拖拽中: {name, ghost}
        self._placeholder = self._placeholder_icon()
        self._loaded = False        # 首次 show 时自动枚举（见 showEvent）

        # ---- 顶部栏 ----
        self.new_btn = QtWidgets.QPushButton("新建 Recipe")
        self.new_btn.setToolTip("把当前选中的节点集保存为 Tool Recipe"
                                "（存到第一个库文件夹的 HouToolsRecipes.hda）")
        self.refresh_btn = QtWidgets.QPushButton("刷新")
        self.lib_btn = QtWidgets.QPushButton("库目录...")
        self.lib_btn.setToolTip("管理 recipe 库文件夹（可多个，递归扫描其中的"
                                " .hda；官方出厂 recipes 不加载）")
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
        top.addWidget(self.new_btn)
        top.addSpacing(8)
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
        self.list.setUniformItemSizes(True)
        self.list.setWordWrap(True)
        self.list.itemDoubleClicked.connect(self._on_double_click)
        self.list.currentItemChanged.connect(self._on_selection_changed)
        self.list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_context_menu)
        self.list.dragStarted.connect(self._start_drag)
        self._apply_grid_size()

        # ---- 右侧预览面板 ----
        self.preview_label = QtWidgets.QLabel()
        self.preview_label.setFixedSize(self.PREVIEW_W, self.PREVIEW_H)
        self.preview_label.setAlignment(QtCore.Qt.AlignCenter)
        self.preview_label.setStyleSheet(
            "background-color: #1D1D20; border: 1px solid #3d3d3d; "
            "border-radius: 6px; color: #666666;")

        self.preview_name = QtWidgets.QLabel()
        self.preview_name.setWordWrap(True)
        self.preview_name.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.preview_meta = QtWidgets.QLabel()
        self.preview_meta.setWordWrap(True)
        self.preview_meta.setStyleSheet("color: #888888;")
        self.preview_comment = QtWidgets.QLabel()
        self.preview_comment.setWordWrap(True)
        self.preview_comment.setStyleSheet("color: #aaaaaa;")

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

        self.new_btn.clicked.connect(self._on_new_recipe)
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

    def _match_search(self, info):
        text = self.search.text().strip().lower()
        if not text:
            return True
        tags = " ".join(metadata.get_tags(info.name))
        hay = " ".join([info.display_label, info.name, info.comment, tags])
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
                fav = metadata.is_favorite(info.name)
                item.setText("★ " + info.display_label if fav
                             else info.display_label)
                item.setToolTip(self._tooltip_for(info))
                item.setIcon(self._icon_for(info))
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
        lines = [info.name]
        tags = metadata.get_tags(info.name)
        if tags:
            lines.append("标签: " + ", ".join(tags))
        if info.comment:
            lines.append(info.comment)
        if info.patterns:
            lines.append("作用: " + ", ".join(info.patterns))
        return "\n".join(lines)

    # ---------------- 图标与预览 ----------------

    def _icon_for(self, info):
        """网格图标：缩略图文件；GIF 取首帧（网格保持静态，预览区才动）。"""
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
        pm = QtGui.QPixmap(96, 64)
        pm.fill(QtGui.QColor("#313136"))
        painter = QtGui.QPainter(pm)
        painter.setPen(QtGui.QColor("#666666"))
        painter.drawText(pm.rect(), QtCore.Qt.AlignCenter, "recipe")
        painter.end()
        return QtGui.QIcon(pm)

    def _on_size_changed(self, val):
        self.size_label.setText("{}px".format(val))
        self._apply_grid_size()

    def _apply_grid_size(self):
        base = int(self.size_slider.value())
        self.list.setIconSize(QtCore.QSize(base, int(base * 0.66)))
        self.list.setGridSize(QtCore.QSize(base + GRID_PADDING_X,
                                           int(base * 0.66) + GRID_PADDING_Y))

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

    def _clear_preview(self):
        self._stop_preview_movie()
        self.preview_label.setPixmap(QtGui.QPixmap())
        self.preview_label.setText("未选中")
        self.preview_name.setText("")
        self.preview_meta.setText("")
        self.preview_comment.setText("")
        self.tags_edit.setText("")
        for btn in (self.fav_btn, self.doc_btn, self.place_btn,
                    self.thumb_btn, self.tags_apply_btn):
            btn.setEnabled(False)

    def _update_preview(self, info):
        for btn in (self.fav_btn, self.doc_btn, self.place_btn,
                    self.thumb_btn, self.tags_apply_btn):
            btn.setEnabled(True)
        fav = metadata.is_favorite(info.name)
        self.fav_btn.setText("★ 已收藏" if fav else "☆ 收藏")
        self.preview_name.setText(info.display_label)
        lib = os.path.basename(info.library) if info.library else ""
        meta_lines = [
            "类型: " + store.CATEGORY_LABELS.get(info.category, info.category),
            "分类: " + (info.submenu or "（未分类）"),
        ]
        if lib:
            meta_lines.append("来源: " + lib)
        if info.patterns:
            meta_lines.append("作用: " + ", ".join(info.patterns))
        self.preview_meta.setText("\n".join(meta_lines))
        self.preview_comment.setText(
            info.comment or "（无备注——点「编辑文档」补一篇用法说明）")
        self.tags_edit.setText(", ".join(metadata.get_tags(info.name)))
        self._preview_name_text = info.display_label

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
        """按类型分发应用；错误进状态栏不打断窗口。"""
        try:
            if info.category == "tool":
                editor = store.current_network_editor()
                if editor is None:
                    self.status.setText("找不到网络编辑器面板，请先打开一个网络视图")
                    return
                store.apply_tool_recipe(info.name, pane=editor,
                                        click_to_place=True)
                self.status.setText(
                    "{}：请在网络编辑器中点击放置（Esc 取消）".format(info.display_label))
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
        except Exception as exc:
            log.warning("apply %s failed: %s", info.name, exc, exc_info=True)
            self.status.setText("应用失败: {}".format(exc))

    # ---------------- 拖拽进网络编辑器 ----------------

    def _start_drag(self, item):
        info = self._info_by_name.get(item.data(QtCore.Qt.UserRole))
        if info is None:
            return
        if info.category != "tool":
            self.status.setText(
                "{} 是 {} 类型：请先选中目标节点后双击应用（拖拽仅支持 Tool）"
                .format(info.display_label,
                        store.CATEGORY_LABELS.get(info.category, info.category)))
            return
        icon = self._icon_for(info)
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
                                    click_to_place=False)
            self.status.setText("已在当前网络中创建 {}".format(info.display_label))
        except Exception as exc:
            log.warning("drag apply %s failed: %s", info.name, exc, exc_info=True)
            self.status.setText("创建失败: {}".format(exc))

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
        menu = QtWidgets.QMenu(self)
        act_fav = menu.addAction("取消收藏" if fav else "★ 收藏")
        act_doc = menu.addAction("编辑文档...")
        act_thumb = menu.addAction("设置缩略图...")
        act_thumb_clear = None
        if metadata.get_thumb(info.name):
            act_thumb_clear = menu.addAction("清除缩略图")
        act_copy = menu.addAction("复制内部名")
        menu.addSeparator()
        act_del = None
        if not info.under_hfs:
            act_del = menu.addAction("删除 Recipe...")
        act = menu.exec_(self.list.mapToGlobal(pos))
        if act is act_fav:
            self._set_favorite(info, not fav)
        elif act is act_doc:
            self._open_doc_for(info)
        elif act is act_thumb:
            self._set_thumb(info)
        elif act is not None and act is act_thumb_clear:
            metadata.clear_thumb(info.name)
            self._thumb_cache.pop(info.name, None)
            item.setIcon(self._icon_for(info))
            self._refresh_current_item()
        elif act is act_copy:
            QtWidgets.QApplication.clipboard().setText(info.name)
        elif act is not None and act is act_del:
            self._delete_recipe(info)

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

    def _set_favorite(self, info, fav):
        metadata.set_favorite(info.name, fav)
        self._rebuild_sidebar()
        self._apply_filter()
        self._update_preview(info)
        self.status.setText("{}：{}".format(
            info.display_label, "已收藏" if fav else "已取消收藏"))

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
        self.status.setText("{}：标签已更新".format(info.display_label))

    def _set_thumb_selected(self):
        info = self._selected_info()
        if info is not None:
            self._set_thumb(info)

    def _set_thumb(self, info):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "设置缩略图 - {}".format(info.display_label), "",
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
            info.display_label, os.path.basename(stored)))

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
        self._doc_dialog = DocEditorDialog(self, info)
        self._doc_dialog.show()
        self._doc_dialog.raise_()

    # ---------------- 新建 / 删除 ----------------

    def _on_new_recipe(self):
        lib_dirs = metadata.get_lib_dirs()
        if not lib_dirs:
            QtWidgets.QMessageBox.information(
                self, "新建 Recipe",
                "请先点「库目录...」设置保存位置（recipe 库文件夹）。")
            return
        try:
            nodes = store.selected_nodes()
        except Exception as exc:
            log.debug("selected nodes lookup failed: %s", exc)
            nodes = []
        if not nodes:
            QtWidgets.QMessageBox.information(
                self, "新建 Recipe",
                "请先在网络编辑器中选中要保存的节点（最后一个作为锚点），"
                "再点「新建 Recipe」。")
            return
        label, ok = QtWidgets.QInputDialog.getText(
            self, "新建 Tool Recipe", "名称（显示名）：")
        label = (label or "").strip()
        if not ok or not label:
            return
        try:
            name = store.create_tool_recipe(label, nodes, lib_dirs[0])
        except Exception as exc:
            log.warning("create recipe failed: %s", exc, exc_info=True)
            QtWidgets.QMessageBox.warning(self, "新建 Recipe",
                                          "保存失败: {}".format(exc))
            return
        self.reload()
        self.status.setText("已保存 Tool Recipe「{}」（{} 个节点）到 {}".format(
            name, len(nodes), lib_dirs[0]))

    def _delete_recipe(self, info):
        answer = QtWidgets.QMessageBox.question(
            self, "删除 Recipe",
            "确定删除「{}」？\n{}\n（缩略图与文档也会一并清理）".format(
                info.display_label, info.name),
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
