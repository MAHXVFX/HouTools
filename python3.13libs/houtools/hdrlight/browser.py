"""Hdr Library - HDR 环境贴图浏览器。

浏览 HDR 库（缩略图网格），选中场景里的灯光节点（envlight 等）后
双击缩略图，把 HDR 路径写入该灯光的环境贴图参数（env_map）。

设计要点：
- 缩略图按需生成：打开面板/刷新时比对缓存目录，仅缺失的由后台
  QThread 调用 Houdini 自带 hoiiotool 生成（linear→sRGB + 缩放），
  生成完线程即退出，无常驻开销；无主缓存（HDR 已删）在刷新时清理。
- 缓存固定 256px 宽（清晰度），显示大小由滑条控制（64-256px）。
- 用户设置（库目录/显示大小/置顶）经 houtools.core.settings.JsonStore
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

# 灯光节点上可能的环境贴图参数，按顺序匹配
MAP_PARMS = ("env_map", "map", "envmap", "environment_map", "texture_map")
# 灯光类节点判断：类型名包含这些关键字，或带有贴图参数
LIGHT_HINTS = ("light", "env")

_SETTINGS = JsonStore("hdr_library.json", defaults={
    "lib_dir": "",          # 空 = 用默认库目录
    "thumb_size": DEFAULT_THUMB_SIZE,
    "pin_on_top": True,
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
# 缩略图
# --------------------------------------------------------------------------

def thumb_cache_dir(lib_dir):
    return os.path.join(lib_dir, ".thumb_cache")


def thumb_path(lib_dir, hdr_path):
    base = os.path.splitext(os.path.basename(hdr_path))[0]
    return os.path.join(thumb_cache_dir(lib_dir), base + ".jpg")


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


def scan_hdrs(lib_dir):
    """扫描 HDR 库目录，返回 [(hdr_path, thumb_path_or_None), ...]。"""
    results = []
    for ext in HDR_EXTS:
        results.extend(glob.glob(os.path.join(lib_dir, "*" + ext)))
    results = sorted(set(results), key=lambda p: os.path.basename(p).lower())
    return [(p, thumb_path(lib_dir, p) if os.path.exists(thumb_path(lib_dir, p)) else None)
            for p in results]


def clean_stale_thumbs(lib_dir):
    """删除已无对应 HDR 的残留缩略图，返回删除数量。"""
    cache = thumb_cache_dir(lib_dir)
    if not os.path.isdir(cache):
        return 0
    hdr_bases = set()
    for ext in HDR_EXTS:
        for p in glob.glob(os.path.join(lib_dir, "*" + ext)):
            hdr_bases.add(os.path.splitext(os.path.basename(p))[0])
    removed = 0
    for f in glob.glob(os.path.join(cache, "*.jpg")):
        if os.path.splitext(os.path.basename(f))[0] not in hdr_bases:
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

    STYLE_SHEET = """
        QWidget { background-color: #18181b; color: #dddddd; }
        QLabel#statusLabel { color: #888888; padding: 4px 8px; }
        QListWidget {
            background-color: #1D1D20; border: 1px solid #3d3d3d;
            border-radius: 6px;
        }
        QListWidget::item { color: #bbbbbb; }
        QListWidget::item:selected { background-color: #0d6399; }
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

        self.setWindowTitle("Hdr Library")
        self.resize(1080, 600)
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
        self.size_slider.setToolTip("缩略图大小")
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

        # ---- 缩略图列表 ----
        self.list = QtWidgets.QListWidget()
        self.list.setViewMode(QtWidgets.QListWidget.IconMode)
        self._apply_thumb_size(self.size_slider.value())
        self.list.setResizeMode(QtWidgets.QListWidget.Adjust)
        # 性能关键项：Batched 分批重排 + UniformItemSizes，
        # 条目多时拖动面板/滚动不卡顿
        self.list.setLayoutMode(QtWidgets.QListWidget.Batched)
        self.list.setUniformItemSizes(True)
        self.list.setWordWrap(True)
        self.list.itemDoubleClicked.connect(self._on_double_click)
        self.list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_context_menu)

        # ---- 底部状态 ----
        self.status = QtWidgets.QLabel("选中场景里的灯光节点后，双击缩略图即可链接贴图")
        self.status.setObjectName("statusLabel")

        lay = QtWidgets.QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.status)

        self.dir_btn.clicked.connect(self._choose_dir)
        self.refresh_btn.clicked.connect(self.reload)
        self.pin_chk.toggled.connect(self._toggle_pin)

        # 缩略图大小变化做 150ms 防抖，拖动滑条时不反复重排
        self._size_timer = QtCore.QTimer(self)
        self._size_timer.setSingleShot(True)
        self._size_timer.setInterval(150)
        self._size_timer.timeout.connect(
            lambda: self._apply_thumb_size(self.size_slider.value()))
        self.size_slider.valueChanged.connect(self._on_size_changed)

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

        # 先清理无主缩略图（HDR 已被删除/移走的），再扫描
        stale = clean_stale_thumbs(self.lib_dir)
        hdrs = scan_hdrs(self.lib_dir)
        need_gen = []
        # 批量填充时暂停重绘，条目多时明显更快
        self.list.setUpdatesEnabled(False)
        try:
            for hdr_path, thumb in hdrs:
                item = QtWidgets.QListWidgetItem(os.path.basename(hdr_path))
                item.setData(QtCore.Qt.UserRole, hdr_path)
                item.setToolTip(hdr_path)
                if thumb:
                    item.setIcon(QtGui.QIcon(thumb))
                else:
                    item.setIcon(placeholder)
                    need_gen.append(hdr_path)
                self.list.addItem(item)
        finally:
            self.list.setUpdatesEnabled(True)

        self.status.setText("共 {} 个 HDR（{}{}）".format(
            len(hdrs),
            "已清理 {} 张无主缩略图；".format(stale) if stale else "",
            "正在后台生成 {} 张缩略图...".format(len(need_gen))
            if need_gen else "缩略图就绪"))
        if need_gen:
            self._start_worker(need_gen)

    def _update_dir_label(self):
        fm = self.dir_label.fontMetrics()
        text = fm.elidedText(self.lib_dir, QtCore.Qt.ElideMiddle, 240)
        self.dir_label.setText("当前目录: {}".format(text))
        self.dir_label.setToolTip(self.lib_dir)

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
        menu = QtWidgets.QMenu(self)
        act_copy = menu.addAction("复制路径")
        act_open = menu.addAction("打开所在文件夹")
        act = menu.exec_(self.list.mapToGlobal(pos))
        if act is act_copy:
            QtWidgets.QApplication.clipboard().setText(path)
        elif act is act_open:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])

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

    def _on_size_changed(self, val):
        self.size_label.setText("{}px".format(val))
        self._size_timer.start()

    def _apply_thumb_size(self, w):
        h = w // 2  # 环境贴图都是 2:1
        self.list.setIconSize(QtCore.QSize(w, h))
        self.list.setGridSize(QtCore.QSize(w + 24, h + 46))

    def closeEvent(self, event):
        _SETTINGS.set("thumb_size", int(self.size_slider.value()))
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
