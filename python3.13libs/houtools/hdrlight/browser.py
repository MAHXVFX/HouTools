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
  线程不 parent 到窗口：Reload 关窗销毁窗口时，运行中的线程改为
  自行跑完自删（每张图生成前后及子进程等待中均响应中断请求），
  避免 Qt "QThread: Destroyed while thread is still running" 崩溃。
- 缓存固定 256px 宽（清晰度），以相对路径+原扩展名命名（目录用
  __ 连接），子文件夹同名、同名字不同扩展（a.hdr 与 a.exr）不冲突；
  HDR 文件比缩略图新（内容更新过）时缓存自动失效重生成。
- 显示大小：滑条值决定列数，网格列宽自动拉伸铺满面板宽度（防抖重算），
  窗口缩放不留大片空白；图标上限 400px（缓存 256px，过度放大会模糊）。
  网格计算是幂等的（滚动条宽度恒定预留 + 结果未变即跳过）——否则
  滚动条显隐会翻转列数形成重排死循环，表现为缩略图一直闪。
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

import concurrent.futures
import glob
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from houtools.core.log import get_logger
from houtools.core.settings import JsonStore
from houtools.ui.taskbar import apply_appwindow_flags

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

# 灯光节点上可能的环境贴图参数，按顺序精确匹配（parm 名全等）：
# env_map = Karma OBJ envlight 与 RenderMan pxrdomelight；
# rman__EnvMap = RenderMan pxrstdenvmaplight；light_texture = 遗留 hlight；
# "map" 放最后兜底（灯光过滤器已在 is_light_node 里排除）
MAP_PARMS = ("env_map", "rman__EnvMap", "envmap", "environment_map",
             "texture_map", "light_texture", "map")
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
    """缓存缩略图路径：按相对路径+原扩展名命名（目录用 __ 连接），
    子文件夹同名、同名字不同扩展（a.hdr 与 a.exr）不冲突。"""
    rel = os.path.relpath(hdr_path, lib_dir)
    safe = rel.replace(os.sep, "__").replace("/", "__")
    return os.path.join(thumb_cache_dir(lib_dir), safe + ".jpg")


def _run_oiiotool(cmd, env, timeout, interrupt=None):
    """运行 hoiiotool 子进程，1 秒粒度轮询等待，返回 (stdout, rc)。

    interrupt() 返回 True（Reload/关窗）或超时都立即 kill 子进程，
    不让生成线程卡在最长 120s 的外部调用上。CREATE_NO_WINDOW 防止
    每次调用弹控制台窗口（后台批量生成时会连续闪黑框）。
    """
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
        creationflags=flags)
    deadline = time.monotonic() + timeout
    while True:
        try:
            out, _err = proc.communicate(timeout=1)
            return out, proc.returncode
        except subprocess.TimeoutExpired:
            if (interrupt is not None and interrupt()) \
                    or time.monotonic() >= deadline:
                proc.kill()
                try:
                    proc.communicate(timeout=5)
                except Exception:
                    proc.wait()
                return b"", -1


def _image_size(oiiotool, image_path, env, interrupt=None):
    """用 hoiiotool --info 读取分辨率，返回 (w, h)，失败返回 None。"""
    out, rc = _run_oiiotool(
        [oiiotool, "--info", image_path], env, 30, interrupt)
    if rc != 0:
        log.warning("oiiotool --info failed for %s (rc=%s)", image_path, rc)
        return None
    m = re.search(r"(\d+)\s*x\s*(\d+)", out.decode("utf-8", "ignore"))
    return (int(m.group(1)), int(m.group(2))) if m else None


def make_thumbnail(lib_dir, hdr_path, interrupt=None):
    """生成单张缩略图，返回缩略图路径；失败或被中断返回 None。

    先写 ~tmp_ 临时文件再原子改名，避免被中断/失败时留下半张
    比 HDR 新的坏缩略图（mtime 失效机制会被它骗过）。临时名必须
    以 .jpg 结尾：OIIO 靠扩展名选输出格式写入器，未知后缀（曾用
    .part）会让 hoiiotool 直接报错，整条生成链路失败。
    """
    oiiotool = _oiiotool_path()
    if not oiiotool:
        log.warning("hoiiotool not found, cannot generate thumbnails")
        return None
    out = thumb_path(lib_dir, hdr_path)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp_out = os.path.join(os.path.dirname(out),
                           "~tmp_" + os.path.basename(out))
    env = _ocio_env()
    size = _image_size(oiiotool, hdr_path, env, interrupt)
    if not size or size[0] <= 0:
        return None
    th = max(1, int(round(size[1] * float(THUMB_WIDTH) / size[0])))
    cmd = [
        oiiotool, hdr_path,
        "--colorconvert", "linear", "sRGB",
        "--resize", "{}x{}".format(THUMB_WIDTH, th),
        "-d", "uint8",
        "-o", tmp_out,
    ]
    _out, rc = _run_oiiotool(cmd, env, 120, interrupt)
    if rc != 0:
        if os.path.exists(tmp_out):
            try:
                os.remove(tmp_out)
            except OSError as exc:
                log.warning("cannot remove partial thumb %s: %s", tmp_out, exc)
        if interrupt is None or not interrupt():
            log.warning("thumbnail generation failed for %s (rc=%s)",
                        hdr_path, rc)
        return None
    os.replace(tmp_out, out)
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


def _thumb_valid(thumb, hdr):
    """缩略图存在且不旧于 HDR 文件（HDR 内容更新后缓存自动失效）。"""
    try:
        return os.path.getmtime(thumb) >= os.path.getmtime(hdr)
    except OSError:
        return False


def scan_hdrs(lib_dir):
    """递归扫描 HDR 库目录，返回 [(hdr_path, category, thumb_path_or_None), ...]。"""
    results = []
    for p, category in _iter_hdrs(lib_dir):
        tp = thumb_path(lib_dir, p)
        results.append((p, category, tp if _thumb_valid(tp, p) else None))
    return results


def clean_stale_thumbs(lib_dir):
    """删除已无对应 HDR 的残留缩略图（含历史 .part 与 ~tmp_ 残片），
    返回删除数量。"""
    cache = thumb_cache_dir(lib_dir)
    if not os.path.isdir(cache):
        return 0
    expected = {os.path.basename(thumb_path(lib_dir, p))
                for p, _category in _iter_hdrs(lib_dir)}
    removed = 0
    candidates = glob.glob(os.path.join(cache, "*.jpg")) \
        + glob.glob(os.path.join(cache, "*.part"))
    for f in candidates:
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


def _find_lop_texture_parm(node):
    """找 LOP 灯（domelight 家族等）的环境贴图参数。

    Solaris 灯的环境贴图是 USD 输入 inputs:texture:file，LOP 把带特
    殊字符的参数名 punycode 化（如 xn__inputstexturefile_r3ah），无法
    按名字直取；按"名字含 texturefile"特征匹配，并返回其 _control
    姊妹参数（USD 属性作者状态，须为 'set' 值才会写入 stage）。
    返回 (值参数, 控制参数或 None)。
    """
    value = None
    control = None
    for parm in node.parms():
        name = parm.name().lower()
        if "texturefile" not in name:
            continue
        if "_control" in name:
            control = control or parm
        else:
            value = value or parm
    return value, control


def is_light_node(node):
    """判断节点是否可作为 HDR 赋值目标。灯光过滤器不接收环境贴图。"""
    try:
        type_name = node.type().name().lower()
        if "filter" in type_name:
            return False
        if find_map_parm(node):
            return True
        return any(h in type_name for h in LIGHT_HINTS)
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
    """把 HDR 路径赋给灯光节点的环境贴图参数，返回 (节点路径, 参数显示名)。

    支持（已用 hython 实测参数结构）：
    - OBJ envlight（Karma）：env_map；skymap_enable != 0（程序化天空）
      时 env_map 被禁用，先关天空模式再赋值
    - RenderMan dome 灯（pxrdomelight / pxrstdenvmaplight）：
      env_map / rman__EnvMap，走通用匹配
    - LOP domelight 全家族（Solaris）：USD 输入 inputs:texture:file，
      参数名被 punycode 化（xn__inputstexturefile_*），按特征匹配；
      其 _control 参数（作者状态）默认即 'set'，若被改过则修正，否则
      值不会写入 USD stage
    其余灯型（distant/point/rect 等非 dome 灯）本无环境贴图参数，抛错说明。
    """
    import hou
    node = node or get_target_node()
    if node is None:
        raise RuntimeError(
            "请先在场景中选中一个灯光节点（如 OBJ envlight / LOP domelight）")
    path = os.path.abspath(hdr_path).replace("\\", "/")

    parm_name = find_map_parm(node)
    if parm_name:
        sky = node.parm("skymap_enable")
        if sky is not None and sky.eval():
            sky.set(0)
        node.parm(parm_name).set(path)
        return node.path(), parm_name

    value, control = _find_lop_texture_parm(node)
    if value is not None:
        if control is not None and control.evalAsString() != "set" \
                and "set" in (control.menuItems() or []):
            control.set("set")
        value.set(path)
        return node.path(), value.description() or value.name()

    raise RuntimeError(
        "节点 {} 上找不到环境贴图参数——该灯类型可能不支持环境贴图"
        "（如 distant / point / rect 等非 dome 灯）".format(node.path()))


# --------------------------------------------------------------------------
# 后台缩略图线程
# --------------------------------------------------------------------------

class ThumbnailThread(QtCore.QThread):
    """后台线程池批量生成缺失缩略图。

    - 不 parent 到窗口：Reload/关窗时窗口可能被 deleteLater，QObject 带
      运行中的 QThread 一起销毁会报 "QThread: Destroyed while thread is
      still running"（Windows 上有崩溃风险）。改为窗口持 Python 引用，
      线程结束自删；中断请求在每张开工前后及子进程等待中响应。
    - 并发数按机器性能取中低档（核数/4，夹在 2..4）：每张 hoiiotool
      是独立子进程，几张并行能吃掉空闲核，又给 Houdini 留足余量。
    - progress(done, total) 每完成一张发一次；pause()/resume() 让每张
      开工前挂起等待（进行中的一张会跑完），停止用 requestInterruption。
    """

    thumbReady = QtCore.Signal(str, str)   # hdr_path, thumb_path
    finishedCount = QtCore.Signal(int)     # 成功数量
    progress = QtCore.Signal(int, int)     # 已完成数, 总数

    def __init__(self, lib_dir, hdr_paths):
        super().__init__()
        self.lib_dir = lib_dir
        self.hdr_paths = hdr_paths
        cores = os.cpu_count() or 4
        self.workers = max(2, min(4, cores // 4))
        self._mutex = QtCore.QMutex()
        self._pause_cond = QtCore.QWaitCondition()
        self._paused = False

    def pause(self):
        self._mutex.lock()
        self._paused = True
        self._mutex.unlock()

    def resume(self):
        self._mutex.lock()
        self._paused = False
        self._mutex.unlock()
        self._pause_cond.wakeAll()

    def is_paused(self):
        return self._paused

    def _wait_if_paused(self):
        """在暂停标志与中断请求之间等待；唤醒条件：resume 或停止。"""
        self._mutex.lock()
        try:
            while self._paused and not self.isInterruptionRequested():
                self._pause_cond.wait(self._mutex)
        finally:
            self._mutex.unlock()

    def run(self):
        total = len(self.hdr_paths)
        counter = {"done": 0, "ok": 0}
        lock = threading.Lock()
        interrupt = self.isInterruptionRequested

        def work(path):
            out = None
            if not interrupt():
                self._wait_if_paused()
                if not interrupt():
                    out = make_thumbnail(self.lib_dir, path,
                                         interrupt=interrupt)
            with lock:
                counter["done"] += 1
                counter["ok"] += 1 if out else 0
            if out:
                self.thumbReady.emit(path, out)
            self.progress.emit(counter["done"], total)

        with concurrent.futures.ThreadPoolExecutor(
                max_workers=self.workers) as pool:
            list(pool.map(work, self.hdr_paths))
        self.finishedCount.emit(counter["ok"])


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
        self._thumbs = {}        # path -> 缩略图路径或 None
        self._item_by_path = {}  # path -> 当前网格里的条目（随过滤重建）
        self._pending_icons = {}  # 待批量应用的缩略图（防每张一重排）
        self._placeholder = self._make_placeholder()

        self.setWindowTitle("Hdr Library")
        self.resize(1080, 620)
        self.setStyleSheet(self.STYLE_SHEET)
        apply_appwindow_flags(self)  # 任务栏常驻（失败静默）
        # 网格自适应的滚动条预留量：按系统滚动条宽度度量，随 DPI 缩放
        self._sb_reserve = QtWidgets.QApplication.style().pixelMetric(
            QtWidgets.QStyle.PM_ScrollBarExtent) + 6

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
        # 路径显示与"更换目录"按钮相邻成组；各组之间等宽弹性间隔，
        # 整行均匀分布
        top.addWidget(self.dir_label)
        top.addWidget(self.dir_btn)
        top.addStretch(1)
        top.addWidget(self.refresh_btn)
        top.addStretch(1)
        top.addWidget(self.pin_chk)
        top.addStretch(1)
        top.addWidget(QtWidgets.QLabel("大小:"))
        top.addWidget(self.size_slider)
        top.addWidget(self.size_label)

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

        # ---- 缩略图生成进度（仅生成中显示）----
        self.progress_label = QtWidgets.QLabel()
        self.progress_bar = QtWidgets.QProgressBar()
        self.pause_btn = QtWidgets.QPushButton("暂停")
        self.stop_btn = QtWidgets.QPushButton("停止")
        self.pause_btn.setToolTip("暂停后台缩略图生成；进行中的一张会跑完")
        self.stop_btn.setToolTip("停止后台缩略图生成；已生成的保留，下次刷新只补缺的")
        prow = QtWidgets.QHBoxLayout()
        prow.setContentsMargins(0, 0, 0, 0)
        prow.addWidget(self.progress_label)
        prow.addWidget(self.progress_bar, 1)
        prow.addWidget(self.pause_btn)
        prow.addWidget(self.stop_btn)
        self.progress_widget = QtWidgets.QWidget()
        self.progress_widget.setLayout(prow)
        self.progress_widget.setVisible(False)

        lay = QtWidgets.QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.splitter, 1)
        lay.addWidget(self.progress_widget)
        lay.addWidget(self.status)

        self.dir_btn.clicked.connect(self._choose_dir)
        self.refresh_btn.clicked.connect(self.reload)
        self.pin_chk.toggled.connect(self._toggle_pin)
        self.pause_btn.clicked.connect(self._on_pause_resume)
        self.stop_btn.clicked.connect(self._on_stop)

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

        # 缩略图就绪后批量应用图标，避免每张触发一次全网格重排
        self._icon_timer = QtCore.QTimer(self)
        self._icon_timer.setSingleShot(True)
        self._icon_timer.setInterval(400)
        self._icon_timer.timeout.connect(self._flush_pending_icons)

        # 定时跟踪 Houdini 选择
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._poll_selection)
        self._timer.start(self.REFRESH_MS)

        self._update_dir_label()
        self.reload()

    # ---------------- 数据加载 ----------------

    def reload(self):
        placeholder = self._placeholder

        # 先清理无主缩略图（HDR 已被删除/移走的），再递归扫描
        stale = clean_stale_thumbs(self.lib_dir)
        hdrs = scan_hdrs(self.lib_dir)
        self._hdrs = hdrs
        self._thumbs = {p: t for p, _c, t in hdrs}
        need_gen = [p for p, _c, t in hdrs if not t]

        self._favs = get_favorites()
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
        """按当前分类重建网格条目（只添加匹配的）。

        不用 item.setHidden()：IconMode+gridSize 下隐藏条目不绘制但
        仍占据网格槽位，分类视图顶部会先铺满一片被隐藏条目的空槽
        （表现为"第一个位置空着"）。重建顺带把条目数压到可见范围，
        后台补图标时的重绘量也最小。
        """
        key = self._category_key
        favs = self._favs
        entries = []
        for path, category, _thumb in self._hdrs:
            if key == KEY_ALL:
                show = True
            elif key == KEY_FAV:
                show = _norm_path(path) in favs
            else:
                show = category == key
            if show:
                entries.append(path)
        self.list.setUpdatesEnabled(False)
        try:
            self.list.clear()
            self._item_by_path = {}
            for path in entries:
                item = QtWidgets.QListWidgetItem()
                name = os.path.basename(path)
                item.setData(QtCore.Qt.UserRole, path)
                item.setToolTip(path)
                item.setText("★ " + name if _norm_path(path) in favs else name)
                thumb = self._thumbs.get(path)
                item.setIcon(QtGui.QIcon(thumb) if thumb else self._placeholder)
                self.list.addItem(item)
                self._item_by_path[path] = item
        finally:
            self.list.setUpdatesEnabled(True)
        self.status.setText("{}：{}/{} 个 HDR。选中场景灯光后双击缩略图链接贴图".format(
            self._category_label(key), len(entries), len(self._hdrs)))

    def _visible_count(self):
        return sum(0 if self.list.item(i).isHidden() else 1
                   for i in range(self.list.count()))

    # ---------------- 收藏 ----------------

    def _set_favorite(self, item, fav):
        """收藏/取消收藏一个条目，重建网格与侧栏（更新星标与计数）。"""
        path = item.data(QtCore.Qt.UserRole)
        set_favorite(path, fav)
        self._favs = get_favorites()
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
        """网格列宽拉伸到正好铺满面板宽度（必须保持幂等）。

        滑条值只决定列数（列数 = 可用宽 / 基准单元宽），单元格宽 =
        可用宽 / 列数，缩略图随之拉伸——窗口拉大不再留大片空白。

        幂等的关键是计算输入不能随滚动条显隐变化：滚动条一出现视口
        就窄 17px，若用视口宽计算，两个状态各算出不同网格尺寸，互相
        触发切换 → 滚动条再翻转 → 无限重排（缩略图一直闪）。所以：
        1) 用列表控件自身宽度（含滚动条区域，滚动条显隐不改变它）减
           去按系统度量动态预留的滚动条宽度；
        2) 算出的尺寸与当前一致时直接跳过，不触发无谓重排。
        """
        base = self.size_slider.value()
        vw = self.list.width() - self._sb_reserve
        if vw < 120:
            return
        cols = max(1, int(vw // (base + GRID_PADDING_X)))
        cell_w = int(vw / cols)
        icon_w = max(64, min(cell_w - GRID_PADDING_X, ICON_MAX_WIDTH))
        icon_h = icon_w // 2
        icon_size = QtCore.QSize(icon_w, icon_h)
        grid_size = QtCore.QSize(cell_w, icon_h + GRID_PADDING_Y)
        if self.list.iconSize() == icon_size \
                and self.list.gridSize() == grid_size:
            return
        self.list.setIconSize(icon_size)
        self.list.setGridSize(grid_size)

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
        if cur_path == self._last_target:
            return  # 选择没变就不动 label，避免每 400ms 无谓重绘
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
        pm.fill(QtGui.QColor("#313136"))  # 比背景亮一档，读作"待生成"
        return QtGui.QIcon(pm)

    def _start_worker(self, hdr_paths):
        thread = self._thread
        if thread is not None:
            try:
                if thread.isRunning():
                    thread.resume()  # 若在暂停中，先唤醒使其能响应中断
                    thread.requestInterruption()
                    thread.wait(1000)
            except RuntimeError:
                pass  # 已被 deleteLater，C++ 对象不在了
        self._thread = ThumbnailThread(self.lib_dir, hdr_paths)
        self._thread.thumbReady.connect(self._on_thumb_ready)
        self._thread.finishedCount.connect(self._on_thumbs_done)
        self._thread.progress.connect(self._on_thumb_progress)
        self._thread.finished.connect(self._on_worker_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self.progress_bar.setRange(0, max(1, len(hdr_paths)))
        self.progress_bar.setValue(0)
        self.progress_label.setText("生成缩略图 0/{}（{} 线程）".format(
            len(hdr_paths), self._thread.workers))
        self.pause_btn.setText("暂停")
        self.progress_widget.setVisible(True)
        self._thread.start()

    def _on_worker_finished(self):
        if self._thread is self.sender():
            self._thread = None
        self.progress_widget.setVisible(False)
        self.pause_btn.setText("暂停")

    def _on_thumb_progress(self, done, total):
        self.progress_bar.setValue(done)
        self.progress_label.setText("生成缩略图 {}/{}".format(done, total))

    def _on_pause_resume(self):
        thread = self._thread
        if thread is None:
            return
        try:
            if thread.is_paused():
                thread.resume()
                self.pause_btn.setText("暂停")
            else:
                thread.pause()
                self.pause_btn.setText("继续")
        except RuntimeError:
            pass

    def _on_stop(self):
        thread = self._thread
        if thread is None:
            return
        try:
            thread.resume()  # 暂停中的线程必须先唤醒才能响应中断
            thread.requestInterruption()
        except RuntimeError:
            pass

    def _on_thumb_ready(self, hdr_path, thumb_path):
        # 批量应用：每张 setIcon 都会触发 QListWidget 一次全网格延迟
        # 重排（条目上千时表现为持续闪烁），攒 400ms 合成一次重绘
        self._thumbs[hdr_path] = thumb_path
        self._pending_icons[hdr_path] = thumb_path
        self._icon_timer.start()

    def _flush_pending_icons(self):
        pending = self._pending_icons
        if not pending:
            return
        self._pending_icons = {}
        self.list.setUpdatesEnabled(False)
        try:
            for path, thumb in pending.items():
                item = self._item_by_path.get(path)
                if item is not None:  # 被过滤掉的条目不在当前网格，跳过
                    item.setIcon(QtGui.QIcon(thumb))
        finally:
            self.list.setUpdatesEnabled(True)

    def _on_thumbs_done(self, ok_count):
        self.status.setText("共 {} 个 HDR，缩略图就绪（本次新生成 {} 张）。"
                            "选中灯光后双击缩略图链接贴图".format(
                                len(self._hdrs), ok_count))

    def _update_dir_label(self):
        fm = self.dir_label.fontMetrics()
        text = fm.elidedText(self.lib_dir, QtCore.Qt.ElideMiddle, 240)
        self.dir_label.setText("当前目录: {}".format(text))
        self.dir_label.setToolTip(self.lib_dir)

    def closeEvent(self, event):
        _SETTINGS.set("thumb_size", int(self.size_slider.value()))
        self._fit_timer.stop()
        self._size_timer.stop()
        self._icon_timer.stop()
        thread = self._thread
        if thread is not None:
            try:
                if thread.isRunning():
                    thread.resume()  # 暂停中的线程必须先唤醒才能响应中断
                    thread.requestInterruption()
                    thread.wait(2000)
            except RuntimeError:
                pass
            # 2 秒内没停完就随它去：线程无 parent，窗口销毁不影响它，
            # 信号连到已销毁的窗口会被 Qt 自动断开，跑完自删。
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
