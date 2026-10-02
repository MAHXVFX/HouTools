"""Hdr Library - HDR 环境贴图浏览器。

浏览 HDR 库（缩略图网格），选中场景里的灯光节点（envlight 等）后
双击缩略图，把 HDR 路径写入该灯光的环境贴图参数（env_map）。

设计要点：
- 库按"总目录 / 一级分类子文件夹"组织（如 hdr白天、hdr黑夜、室内、户外），
  递归扫描；第一级子文件夹即分类，左侧侧栏切换（全部 / 收藏 / 各分类），
  分类文件夹可在侧栏右键新建/打开，后续下载的 HDR 放进对应分类即可。
- 缩略图按需生成：打开面板/刷新时比对缓存目录，仅缺失的由后台
  QThread 调用 Houdini 自带 hoiiotool 生成（linear→sRGB + 缩放），
  生成完线程即退出，无常驻开销；无主缓存（HDR 已删）在刷新时清理。
- 缓存固定 256px 宽（清晰度），以相对路径命名（目录用 __ 连接），
  子文件夹同名文件不冲突；根目录文件命名与旧版一致，旧缓存直接复用。
- 显示大小：滑条值决定列数，网格列宽自动拉伸铺满面板宽度（防抖重算），
  窗口缩放不留大片空白；图标上限 400px（缓存 256px，过度放大会模糊）。
- 收藏：右键收藏/取消收藏，侧栏"★ 收藏"一键过滤；收藏以 normcase
  后的绝对路径存 settings（Windows 不区分大小写）。
- 用户设置（库目录/显示大小/置顶/收藏）经 houtools.core.settings.JsonStore
  持久化到项目 settings/ 目录（gitignored，随机器各自保存）。
- 窗口经 houtools.ui.window_manager 单例登记，Reload 热加载时自动关闭。
- 本机 hoiiotool（OIIO 2.5.18）的坑：--resize 不支持省略高度的
  "256x" 写法（静默无输出），必须先 --info 取分辨率写全 WxH；
  --colorconvert 需要 OCIO 配置，未设置时会崩溃（自动注入 packages/
  ocio/houdini-config*.ocio）。
"""

import glob
import os
import re
import subprocess
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from houtools.core.log import get_logger
from houtools.core.settings import JsonStore

log = get_logger("hdrlight.browser")

TOOL_ID = "hdr_library"

HDR_EXTS = (".hdr", ".hdri", ".exr")
THUMB_WIDTH = 256          # 缓存缩略图宽度（固定，保证清晰度）
DEFAULT_THUMB_SIZE = 128   # 打开工具时的默认显示大小（滑条可调 64-256）
ICON_MAX_WIDTH = 400       # 显示图标上限（超过则相对缓存过度放大而模糊）
GRID_PADDING_X = 24        # 网格单元内图标左右的留白
GRID_PADDING_Y = 46        # 网格单元内图标下方留给文件名的高度

# 侧栏特殊分类键（普通分类键为子文件夹名，根目录文件为 ""）
KEY_ALL = "__all__"
KEY_FAV = "__fav__"

# 灯光节点上可能的环境贴图参数，按顺序匹配
MAP_PARMS = ("env_map", "map", "envmap", "environment_map", "texture_map")
# 灯光类节点判断：类型名包含这些关键字，或带有贴图参数
LIGHT_HINTS = ("light", "env")

_SETTINGS = JsonStore("hdr_library.json", defaults={
    "lib_dir": "",          # 空 = 用默认库目录
    "thumb_size": DEFAULT_THUMB_SIZE,
    "pin_on_top": True,
    "favorites": [],        # 收藏的 HDR 绝对路径（normcase 后）
})


# --------------------------------------------------------------------------
# 路径与外部工具
# --------------------------------------------------------------------------

def default_lib_dir():
    """默认 HDR 库目录：环境变量 HDR_TOOL_LIB → ~/HouTools/hdri。"""
    env = os.environ.get("HDR_TOOL_LIB")
    if env and os.path.isdir(env):
        return env
    return str(Path.home() / "HouTools" / "hdri")


def get_lib_dir():
    return _SETTINGS.get("lib_dir") or default_lib_dir()


def _houdini_bin():
    """Houdini 安装目录下的 bin 路径（不依赖 cwd）。"""
    try:
        import hou
        return os.path.join(hou.text.expandString("$HFS"), "bin")
    except ImportError:
        env = os.environ.get("HFS")
        if env:
            return os.path.join(env, "bin")
        return None


def _oiiotool_path():
    bin_dir = _houdini_bin()
    if not bin_dir:
        return None
    for name in ("hoiiotool.exe", "hoiiotool", "oiiotool.exe", "oiiotool"):
        p = os.path.join(bin_dir, name)
        if os.path.exists(p):
            return p
    return None


def _ocio_env():
    """hoiiotool 做 linear→sRGB 转换需要 OCIO 配置，缺失时会崩溃。"""
    if os.environ.get("OCIO"):
        return dict(os.environ)
    env = dict(os.environ)
    bin_dir = _houdini_bin()
    if bin_dir:
        pkgs = os.path.join(bin_dir, "..", "packages", "ocio")
        cfgs = glob.glob(os.path.join(pkgs, "houdini-config*.ocio")) or \
            glob.glob(os.path.join(pkgs, "*config*.ocio"))
        if cfgs:
            env["OCIO"] = cfgs[0]
    return env


# --------------------------------------------------------------------------
# 收藏
# --------------------------------------------------------------------------

def _norm_path(path):
    """收藏键：绝对路径 + normcase（Windows 不区分大小写）。"""
    return os.path.normcase(os.path.abspath(path))


def get_favorites():
    return {_norm_path(p) for p in (_SETTINGS.get("favorites") or [])}


def set_favorite(hdr_path, fav):
    """添加/移除收藏并持久化，返回操作后的收藏状态。"""
    favs = get_favorites()
    key = _norm_path(hdr_path)
    if fav:
        favs.add(key)
    else:
        favs.discard(key)
    _SETTINGS.set("favorites", sorted(favs))
    return fav


# --------------------------------------------------------------------------
# 缩略图
# --------------------------------------------------------------------------

def thumb_cache_dir(lib_dir):
    return os.path.join(lib_dir, ".thumb_cache")


def thumb_path(lib_dir, hdr_path):
    """缓存缩略图路径：按相对路径命名（目录用 __ 连接），子文件夹同名
    文件不冲突；根目录文件的命名与旧版一致（<文件名>.jpg），旧缓存复用。"""
    rel = os.path.splitext(os.path.relpath(hdr_path, lib_dir))[0]
    safe = rel.replace(os.sep, "__").replace("/", "__")
    return os.path.join(thumb_cache_dir(lib_dir), safe + ".jpg")


def _image_size(oiiotool, image_path, env):
    """用 hoiiotool --info 读取分辨率，返回 (w, h)，失败返回 None。"""
    try:
        out = subprocess.check_output(
            [oiiotool, "--info", image_path],
            stderr=subprocess.STDOUT, env=env, timeout=30,
        ).decode("utf-8", "ignore")
    except Exception as exc:
        log.warning("oiiotool --info failed for %s: %s", image_path, exc)
        return None
    m = re.search(r"(\d+)\s*x\s*(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else None


def make_thumbnail(lib_dir, hdr_path):
    """生成单张缩略图，返回缩略图路径；失败返回 None。"""
    oiiotool = _oiiotool_path()
    if not oiiotool:
        log.warning("hoiiotool not found, cannot generate thumbnails")
        return None
    out = thumb_path(lib_dir, hdr_path)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    env = _ocio_env()
    size = _image_size(oiiotool, hdr_path, env)
    if not size or size[0] <= 0:
        return None
    th = max(1, int(round(size[1] * float(THUMB_WIDTH) / size[0])))
    cmd = [
        oiiotool, hdr_path,
        "--colorconvert", "linear", "sRGB",
        "--resize", "{}x{}".format(THUMB_WIDTH, th),
        "-d", "uint8",
        "-o", out,
    ]
    try:
        subprocess.check_output(
            cmd, stderr=subprocess.STDOUT, env=env, timeout=120,
        )
    except Exception as exc:
        log.warning("thumbnail generation failed for %s: %s", hdr_path, exc)
        return None
    return out if os.path.exists(out) else None


def _iter_hdrs(lib_dir):
    """递归列出库里的 HDR，返回 [(path, category)]。

    category 为第一级子文件夹名（嵌套子文件夹归到第一级），
    根目录文件为 ""；跳过隐藏目录（含 .thumb_cache）。
    """
    out = []
    for root, dirs, files in os.walk(lib_dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        rel = os.path.relpath(root, lib_dir)
        category = "" if rel == "." else rel.split(os.sep)[0]
        for name in files:
            if name.lower().endswith(HDR_EXTS):
                out.append((os.path.join(root, name), category))
    out.sort(key=lambda t: (t[1].lower(), os.path.basename(t[0]).lower()))
    return out


def list_categories(lib_dir):
    """库目录下的一级子文件夹（含空分类），已排除隐藏目录，排序返回。"""
    try:
        names = os.listdir(lib_dir)
    except OSError as exc:
        log.warning("cannot list %s: %s", lib_dir, exc)
        return []
    return sorted(
        (n for n in names
         if not n.startswith(".") and os.path.isdir(os.path.join(lib_dir, n))),
        key=str.lower)


def scan_hdrs(lib_dir):
    """递归扫描 HDR 库目录，返回 [(hdr_path, category, thumb_path_or_None), ...]。"""
    results = []
    for p, category in _iter_hdrs(lib_dir):
        tp = thumb_path(lib_dir, p)
        results.append((p, category, tp if os.path.exists(tp) else None))
    return results


def clean_stale_thumbs(lib_dir):
    """删除已无对应 HDR 的残留缩略图，返回删除数量。"""
    cache = thumb_cache_dir(lib_dir)
    if not os.path.isdir(cache):
        return 0
    expected = {os.path.basename(thumb_path(lib_dir, p))
                for p, _category in _iter_hdrs(lib_dir)}
    removed = 0
    for f in glob.glob(os.path.join(cache, "*.jpg")):
        if os.path.basename(f) not in expected:
            try:
                os.remove(f)
                removed += 1
            except OSError as exc:
                log.warning("cannot remove stale thumb %s: %s", f, exc)
    return removed


# --------------------------------------------------------------------------
# 灯光赋值逻辑
# --------------------------------------------------------------------------

def find_map_parm(node):
    """返回节点上第一个存在的环境贴图参数名，找不到返回 None。"""
    for name in MAP_PARMS:
        if node.parm(name) is not None:
            return name
    return None


def is_light_node(node):
    """判断节点是否可作为 HDR 赋值目标。"""
    try:
        if find_map_parm(node):
            return True
        return any(h in node.type().name().lower() for h in LIGHT_HINTS)
    except Exception:
        return False


def get_target_node():
    """从当前选中节点里找第一个灯光目标，没有则返回 None。"""
    import hou
    for node in hou.selectedNodes():
        if is_light_node(node):
            return node
    return None


def assign_hdr(hdr_path, node=None):
    """把 HDR 路径赋给灯光节点的环境贴图参数，返回 (节点路径, 参数名)。

    envlight 的 env_map 在 skymap_enable != 0（程序化天空）时被禁用，
    所以先关掉天空模式再赋值。
    """
    import hou
    node = node or get_target_node()
    if node is None:
        raise RuntimeError("请先在场景中选中一个灯光节点（如 envlight）")
    parm_name = find_map_parm(node)
    if parm_name is None:
        raise RuntimeError(
            "节点 {} 上找不到环境贴图参数（{}）".format(node.path(), ", ".join(MAP_PARMS)))
    sky = node.parm("skymap_enable")
    if sky is not None and sky.eval():
        sky.set(0)
    path = os.path.abspath(hdr_path).replace("\\", "/")
    node.parm(parm_name).set(path)
    return node.path(), parm_name


# --------------------------------------------------------------------------
# 后台缩略图线程
# --------------------------------------------------------------------------

class ThumbnailThread(QtCore.QThread):
    """后台逐个生成缺失的缩略图；run() 里响应中断请求，关窗不卡死。"""

    thumbReady = QtCore.Signal(str, str)   # hdr_path, thumb_path
    finishedCount = QtCore.Signal(int)     # 成功数量

    def __init__(self, lib_dir, hdr_paths, parent=None):
        super().__init__(parent)
        self.lib_dir = lib_dir
        self.hdr_paths = hdr_paths

    def run(self):
        ok = 0
        for hdr_path in self.hdr_paths:
            if self.isInterruptionRequested():
                break
            out = make_thumbnail(self.lib_dir, hdr_path)
            if out:
                ok += 1
                self.thumbReady.emit(hdr_path, out)
        self.finishedCount.emit(ok)


# --------------------------------------------------------------------------
# 窗口
# --------------------------------------------------------------------------

class _HdrLibraryWindow(QtWidgets.QWidget):
    """HDR 库浏览器主窗口（经 window_manager 单例登记）。"""

    REFRESH_MS = 400
    FIT_DELAY_MS = 150     # 面板尺寸变化后重排网格的防抖
    SIDEBAR_MIN = 140
    SIDEBAR_MAX = 300

    STYLE_SHEET = """
        QWidget { background-color: #18181b; color: #dddddd; }
        QLabel#statusLabel { color: #888888; padding: 4px 8px; }
        QListWidget {
            background-color: #1D1D20; border: 1px solid #3d3d3d;
            border-radius: 6px;
        }
        QListWidget::item { color: #bbbbbb; }
        QListWidget::item:selected { background-color: #0d6399; }
        QSplitter::handle:horizontal { background: #2d2d2d; width: 2px; }
        QSlider::groove:horizontal { height: 4px; background: #3d3d3d; border-radius: 2px; }
        QSlider::handle:horizontal {
            background: #0d6399; width: 12px; margin: -5px 0; border-radius: 6px;
        }
    """

    def __init__(self, lib_dir, parent=None, flags=None):
        if flags is None:
            flags = QtCore.Qt.Window
        super().__init__(parent, flags)
        self.lib_dir = lib_dir
        self._thread = None
        self._last_target = None
        self._category_key = KEY_ALL
        self._favs = get_favorites()
        self._hdrs = []   # 最近一次 scan_hdrs 的结果，供侧栏计数复用

        self.setWindowTitle("Hdr Library")
        self.resize(1080, 620)
        self.setStyleSheet(self.STYLE_SHEET)

        # ---- 顶部栏 ----
        self.target_label = QtWidgets.QLabel("目标灯光: (未选中)")
        self.target_label.setStyleSheet("padding: 4px 8px;")
        self.dir_label = QtWidgets.QLabel()
        self.dir_btn = QtWidgets.QPushButton("更换目录...")
        self.refresh_btn = QtWidgets.QPushButton("刷新")
        self.pin_chk = QtWidgets.QCheckBox("置顶")
        self.pin_chk.setChecked(bool(
            self.windowFlags() & QtCore.Qt.WindowStaysOnTopHint))
        self.pin_chk.setToolTip("勾选后窗口始终浮在 Houdini 之上；"
                                "取消勾选或手动最小化则恢复正常")
        self.size_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.size_slider.setRange(64, 256)
        self.size_slider.setValue(int(_SETTINGS.get("thumb_size")))
        self.size_slider.setFixedWidth(140)
        self.size_slider.setToolTip(
            "基准大小：决定每行列数，网格自动拉伸铺满面板宽度")
        self.size_label = QtWidgets.QLabel("{}px".format(self.size_slider.value()))

        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.target_label, 1)
        top.addWidget(self.dir_label, 1)
        top.addWidget(self.pin_chk)
        top.addWidget(QtWidgets.QLabel("大小:"))
        top.addWidget(self.size_slider)
        top.addWidget(self.size_label)
        top.addWidget(self.dir_btn)
        top.addWidget(self.refresh_btn)

        # ---- 分类侧栏 ----
        self.sidebar = QtWidgets.QListWidget()
        self.sidebar.setMinimumWidth(self.SIDEBAR_MIN)
        self.sidebar.setMaximumWidth(self.SIDEBAR_MAX)
        self.sidebar.currentItemChanged.connect(self._on_category_changed)
        self.sidebar.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.sidebar.customContextMenuRequested.connect(self._on_sidebar_menu)

        # ---- 缩略图列表 ----
        self.list = QtWidgets.QListWidget()
        self.list.setViewMode(QtWidgets.QListWidget.IconMode)
        self.list.setResizeMode(QtWidgets.QListWidget.Adjust)
        # 性能关键项：Batched 分批重排 + UniformItemSizes，
        # 条目多时拖动面板/滚动不卡顿
        self.list.setLayoutMode(QtWidgets.QListWidget.Batched)
        self.list.setUniformItemSizes(True)
        self.list.setWordWrap(True)
        self.list.itemDoubleClicked.connect(self._on_double_click)
        self.list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_context_menu)

        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.splitter.setHandleWidth(4)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(self.list)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([190, 890])

        # ---- 底部状态 ----
        self.status = QtWidgets.QLabel("选中场景里的灯光节点后，双击缩略图即可链接贴图")
        self.status.setObjectName("statusLabel")

        lay = QtWidgets.QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.splitter, 1)
        lay.addWidget(self.status)

        self.dir_btn.clicked.connect(self._choose_dir)
        self.refresh_btn.clicked.connect(self.reload)
        self.pin_chk.toggled.connect(self._toggle_pin)

        # 缩略图基准大小变化做 150ms 防抖，拖动滑条时不反复重排
        self._size_timer = QtCore.QTimer(self)
        self._size_timer.setSingleShot(True)
        self._size_timer.setInterval(self.FIT_DELAY_MS)
        self._size_timer.timeout.connect(self._fit_grid)
        self.size_slider.valueChanged.connect(self._on_size_changed)

        # 面板宽度变化（窗口缩放/滚动条出现消失）后防抖重排网格，铺满面板
        self._fit_timer = QtCore.QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(self.FIT_DELAY_MS)
        self._fit_timer.timeout.connect(self._fit_grid)
        self.list.viewport().installEventFilter(self)

        # 定时跟踪 Houdini 选择
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._poll_selection)
        self._timer.start(self.REFRESH_MS)

        self._update_dir_label()
        self.reload()

    # ---------------- 数据加载 ----------------

    def reload(self):
        self.list.clear()
        placeholder = self._make_placeholder()

        # 先清理无主缩略图（HDR 已被删除/移走的），再递归扫描
        stale = clean_stale_thumbs(self.lib_dir)
        hdrs = scan_hdrs(self.lib_dir)
        self._hdrs = hdrs
        need_gen = []
        # 批量填充时暂停重绘，条目多时明显更快
        self.list.setUpdatesEnabled(False)
        try:
            for hdr_path, category, thumb in hdrs:
                item = QtWidgets.QListWidgetItem(os.path.basename(hdr_path))
                item.setData(QtCore.Qt.UserRole, hdr_path)
                item.setData(QtCore.Qt.UserRole + 1, category)
                item.setToolTip(hdr_path)
                if thumb:
                    item.setIcon(QtGui.QIcon(thumb))
                else:
                    item.setIcon(placeholder)
                    need_gen.append(hdr_path)
                self.list.addItem(item)
        finally:
            self.list.setUpdatesEnabled(True)

        self._favs = get_favorites()
        self._mark_favorite_items()
        self._rebuild_sidebar()
        self._apply_filter()

        self.status.setText("共 {} 个 HDR（{}{}）".format(
            len(hdrs),
            "已清理 {} 张无主缩略图；".format(stale) if stale else "",
            "正在后台生成 {} 张缩略图...".format(len(need_gen))
            if need_gen else "缩略图就绪"))
        if need_gen:
            self._start_worker(need_gen)

    # ---------------- 分类侧栏 ----------------

    def _rebuild_sidebar(self):
        """按最近一次扫描结果 + 库里的一级子文件夹重建分类侧栏。

        空分类文件夹也列出（方便"按分类下载"先建目录）；
        保持当前选中分类，原分类不存在（换目录/被删）时回"全部"。
        """
        counts = {}
        fav_count = 0
        for hdr_path, category, _thumb in self._hdrs:
            counts[category] = counts.get(category, 0) + 1
            if _norm_path(hdr_path) in self._favs:
                fav_count += 1
        categories = set(list_categories(self.lib_dir))
        categories.update(counts)

        self.sidebar.blockSignals(True)
        self.sidebar.clear()
        entries = [(KEY_ALL, "全部", len(self._hdrs)),
                   (KEY_FAV, "★ 收藏", fav_count)]
        entries.extend(
            (cat, cat or "未分类", counts.get(cat, 0))
            for cat in sorted(categories, key=str.lower))
        for key, label, n in entries:
            item = QtWidgets.QListWidgetItem("{} ({})".format(label, n))
            item.setData(QtCore.Qt.UserRole, key)
            if key not in (KEY_ALL, KEY_FAV):
                item.setToolTip(os.path.join(self.lib_dir, key))
            self.sidebar.addItem(item)
        row = next((i for i in range(self.sidebar.count())
                    if self.sidebar.item(i).data(QtCore.Qt.UserRole)
                    == self._category_key), 0)
        self.sidebar.setCurrentRow(row)
        self.sidebar.blockSignals(False)

    def _on_category_changed(self, current, _previous=None):
        if current is None:
            return
        self._category_key = current.data(QtCore.Qt.UserRole) or KEY_ALL
        self._apply_filter()

    def _select_category(self, key):
        """切换分类（侧栏编程入口，测试也用它）。"""
        self._category_key = key
        self._apply_filter()
        self.sidebar.blockSignals(True)
        for i in range(self.sidebar.count()):
            if self.sidebar.item(i).data(QtCore.Qt.UserRole) == key:
                self.sidebar.setCurrentRow(i)
                break
        self.sidebar.blockSignals(False)

    @staticmethod
    def _category_label(key):
        if key == KEY_ALL:
            return "全部"
        if key == KEY_FAV:
            return "收藏"
        return key or "未分类"

    def _apply_filter(self):
        """按当前分类隐藏/显示缩略图条目，并把统计写进状态栏。"""
        visible = 0
        total = self.list.count()
        for i in range(total):
            item = self.list.item(i)
            path = item.data(QtCore.Qt.UserRole)
            if self._category_key == KEY_ALL:
                show = True
            elif self._category_key == KEY_FAV:
                show = _norm_path(path) in self._favs
            else:
                show = item.data(QtCore.Qt.UserRole + 1) == self._category_key
            item.setHidden(not show)
            visible += show
        self.status.setText("{}：{}/{} 个 HDR。选中场景灯光后双击缩略图链接贴图".format(
            self._category_label(self._category_key), visible, total))

    def _visible_count(self):
        return sum(0 if self.list.item(i).isHidden() else 1
                   for i in range(self.list.count()))

    # ---------------- 收藏 ----------------

    def _mark_favorite_items(self):
        for i in range(self.list.count()):
            self._set_item_text(self.list.item(i))

    def _set_item_text(self, item):
        path = item.data(QtCore.Qt.UserRole)
        name = os.path.basename(path)
        item.setText("★ " + name if _norm_path(path) in self._favs else name)

    def _set_favorite(self, item, fav):
        """收藏/取消收藏一个条目，刷新文本、侧栏计数与过滤。"""
        path = item.data(QtCore.Qt.UserRole)
        set_favorite(path, fav)
        self._favs = get_favorites()
        self._set_item_text(item)
        self._rebuild_sidebar()
        self._apply_filter()
        self.status.setText("{}：{}".format(
            os.path.basename(path), "已收藏" if fav else "已取消收藏"))

    # ---------------- 网格自适应（铺满面板宽度） ----------------

    def eventFilter(self, obj, event):
        if obj is self.list.viewport() and event.type() == QtCore.QEvent.Resize:
            self._fit_timer.start()
        return super().eventFilter(obj, event)

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_grid()

    def _fit_grid(self):
        """网格列宽拉伸到正好铺满面板宽度。

        滑条值只决定列数（列数 = 可视宽 / 基准单元宽），单元格宽 =
        可视宽 / 列数，缩略图随之拉伸——窗口拉大不再留大片空白。
        纵向滚动条出现时预留其宽度，避免重排引发滚动条闪烁。
        """
        base = self.size_slider.value()
        vw = self.list.viewport().width()
        sb = self.list.verticalScrollBar()
        if sb.isVisible():
            vw -= sb.width() + 2
        if vw < 120:
            return
        cols = max(1, int(vw // (base + GRID_PADDING_X)))
        cell_w = int(vw / cols)
        icon_w = max(64, min(cell_w - GRID_PADDING_X, ICON_MAX_WIDTH))
        icon_h = icon_w // 2  # 环境贴图都是 2:1
        self.list.setIconSize(QtCore.QSize(icon_w, icon_h))
        self.list.setGridSize(QtCore.QSize(cell_w, icon_h + GRID_PADDING_Y))

    def _on_size_changed(self, val):
        self.size_label.setText("{}px".format(val))
        self._size_timer.start()

    # ---------------- 目标灯光跟踪 ----------------

    def _poll_selection(self):
        node = None
        try:
            node = get_target_node()
        except Exception as exc:  # 窗口在无场景/非 GUI 环境下打开时静默
            log.debug("selection poll failed: %s", exc)
        cur_path = node.path() if node is not None else None
        if cur_path != self._last_target:
            self._last_target = cur_path
        if node is not None:
            self.target_label.setText("目标灯光: {}".format(node.path()))
            self.target_label.setStyleSheet("padding: 4px 8px; color: #6f6;")
        else:
            self.target_label.setText("目标灯光: (未选中灯光节点)")
            self.target_label.setStyleSheet("padding: 4px 8px; color: #f96;")

    # ---------------- 交互 ----------------

    def _on_double_click(self, item):
        hdr_path = item.data(QtCore.Qt.UserRole)
        try:
            node_path, parm = assign_hdr(hdr_path)
            self.status.setText("已把贴图赋给 {} 的 {} 参数".format(node_path, parm))
        except Exception as exc:
            log.warning("assign failed: %s", exc, exc_info=True)
            self.status.setText("赋值失败: {}: {}".format(type(exc).__name__, exc))

    def _on_context_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        path = item.data(QtCore.Qt.UserRole)
        fav = _norm_path(path) in self._favs
        menu = QtWidgets.QMenu(self)
        act_fav = menu.addAction("取消收藏" if fav else "收藏")
        menu.addSeparator()
        act_copy = menu.addAction("复制路径")
        act_open = menu.addAction("打开所在文件夹")
        act = menu.exec_(self.list.mapToGlobal(pos))
        if act is act_fav:
            self._set_favorite(item, not fav)
        elif act is act_copy:
            QtWidgets.QApplication.clipboard().setText(path)
        elif act is act_open:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])

    def _on_sidebar_menu(self, pos):
        item = self.sidebar.itemAt(pos)
        menu = QtWidgets.QMenu(self)
        act_new = menu.addAction("新建分类...")
        act_open = None
        if item is not None:
            key = item.data(QtCore.Qt.UserRole)
            if key not in (KEY_ALL, KEY_FAV):
                act_open = menu.addAction("打开分类文件夹")
        act = menu.exec_(self.sidebar.mapToGlobal(pos))
        if act is act_new:
            self._create_category()
        elif act is act_open and item is not None:
            subprocess.Popen(["explorer",
                              os.path.join(self.lib_dir,
                                           item.data(QtCore.Qt.UserRole))])

    def _create_category(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "新建分类", "分类文件夹名：")
        name = (name or "").strip()
        if not ok or not name:
            return
        if any(c in name for c in '\\/:*?"<>|'):
            QtWidgets.QMessageBox.warning(
                self, "新建分类", "名称不能包含 \\/ : * ? \" < > | 等字符")
            return
        try:
            os.makedirs(os.path.join(self.lib_dir, name), exist_ok=True)
        except OSError as exc:
            log.warning("create category failed: %s", exc)
            self.status.setText("新建分类失败: {}".format(exc))
            return
        self._category_key = name  # 重建后停在新分类上
        self.reload()
        self.status.setText("已创建分类文件夹 {}，把下载的 HDR 放进去后点刷新".format(name))

    def _choose_dir(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self, "选择 HDR 库目录", self.lib_dir)
        if d:
            self.lib_dir = d
            _SETTINGS.set("lib_dir", d)  # 记住该机器上的选择
            self._update_dir_label()
            self.reload()

    def _toggle_pin(self, on):
        _SETTINGS.set("pin_on_top", bool(on))
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, on)
        self.show()  # setWindowFlag 会使窗口隐藏，需要重新 show

    def _make_placeholder(self):
        pm = QtGui.QPixmap(THUMB_WIDTH, THUMB_WIDTH // 2)
        pm.fill(QtCore.Qt.darkGray)
        return QtGui.QIcon(pm)

    def _start_worker(self, hdr_paths):
        if self._thread is not None and self._thread.isRunning():
            self._thread.requestInterruption()
            self._thread.wait(1000)
        self._thread = ThumbnailThread(self.lib_dir, hdr_paths, self)
        self._thread.thumbReady.connect(self._on_thumb_ready)
        self._thread.finishedCount.connect(self._on_thumbs_done)
        self._thread.start()

    def _on_thumb_ready(self, hdr_path, thumb_path):
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(QtCore.Qt.UserRole) == hdr_path:
                item.setIcon(QtGui.QIcon(thumb_path))
                break

    def _on_thumbs_done(self, ok_count):
        self.status.setText("共 {} 个 HDR，缩略图就绪（本次新生成 {} 张）。"
                            "选中灯光后双击缩略图链接贴图".format(self.list.count(), ok_count))

    def _update_dir_label(self):
        fm = self.dir_label.fontMetrics()
        text = fm.elidedText(self.lib_dir, QtCore.Qt.ElideMiddle, 240)
        self.dir_label.setText("当前目录: {}".format(text))
        self.dir_label.setToolTip(self.lib_dir)

    def closeEvent(self, event):
        _SETTINGS.set("thumb_size", int(self.size_slider.value()))
        self._fit_timer.stop()
        self._size_timer.stop()
        if self._thread is not None and self._thread.isRunning():
            self._thread.requestInterruption()
            self._thread.wait(2000)  # 最多等 2 秒，避免界面卡死
        super().closeEvent(event)


# --------------------------------------------------------------------------
# 入口（tools.hdr_library.run 调用）
# --------------------------------------------------------------------------

def show_hdr_library():
    """打开（或置前）HDR 库窗口（window_manager 单例登记）。"""
    from houtools.ui import window_manager

    parent = None
    try:
        import hou
        parent = hou.qt.mainWindow()
    except (ImportError, AttributeError):
        pass

    lib_dir = get_lib_dir()
    pin = bool(_SETTINGS.get("pin_on_top"))

    def factory():
        flags = QtCore.Qt.Window
        if pin:
            flags |= QtCore.Qt.WindowStaysOnTopHint
        return _HdrLibraryWindow(lib_dir, parent, flags)

    return window_manager.open_window(TOOL_ID, factory)
