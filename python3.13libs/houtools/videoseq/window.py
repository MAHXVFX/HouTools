"""Video to Sequence - 视频转序列图工具

将 mov/mp4/avi 等视频格式转换为 JPG 序列图。
使用 ffmpeg 进行视频解码和 JPEG 编码。
"""

import os
import re
import json
import logging
import subprocess
from dataclasses import dataclass
from typing import Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QSlider, QGroupBox, QFormLayout,
    QFileDialog, QProgressBar, QInputDialog,
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer

from houtools.videoseq.ffmpeg import find_ffprobe as _get_ffprobe_path
from houtools.videoseq.ffmpeg import get_startup_kwargs as _get_startup_kwargs

from houtools.core.log import get_logger
from houtools.ui import dialogs
from houtools.ui import fonts as tool_fonts
logger = get_logger("videoseq.window")

# 支持的视频格式
VIDEO_EXTENSIONS = [
    ".mov", ".mp4", ".avi", ".mkv", ".wmv", ".flv",
    ".webm", ".m4v", ".mpg", ".mpeg", ".3gp", ".ts",
]


# ─── Styles ──────────────────────────────────────────────────────────

STYLE_SHEET = """
QGroupBox {
    background-color: #1D1D20;
    border: 1px solid #3d3d3d;
    border-radius: 8px;
    margin-top: 14px;
    padding: 2px 8px 6px 8px;
    font-weight: normal;
    font-size: 12px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    top: -2px;
    padding: 0 6px;
    color: #cccccc;
    text-decoration: none;
}
QLineEdit {
    background-color: #2d2d2d;
    color: #ffffff;
    border: 1px solid #3d3d3d;
    padding: 5px 8px;
    border-radius: 6px;
}
QLineEdit:focus {
    border: 1px solid #0d6399;
}
QSpinBox {
    background-color: #2d2d2d;
    color: #ffffff;
    border: 1px solid #3d3d3d;
    padding: 4px 8px;
    border-radius: 6px;
    min-width: 60px;
}
QSpinBox:focus {
    border: 1px solid #0d6399;
}
QProgressBar {
    border: 1px solid #3d3d3d;
    border-radius: 6px;
    text-align: center;
    background-color: #2d2d2d;
    color: #ffffff;
    min-height: 22px;
}
QProgressBar::chunk {
    background-color: #0d6399;
    border-radius: 5px;
}
QPushButton#browseBtn {
    background-color: #e0cb56;
    color: #000000;
    padding: 5px 14px;
    border-radius: 8px;
    min-width: 50px;
    font-weight: bold;
}
QPushButton#browseBtn:hover {
    background-color: #d4bf40;
}
QPushButton#convertBtn {
    background-color: #0d6399;
    color: white;
    padding: 10px 30px;
    border-radius: 10px;
    min-width: 200px;
    font-size: 14px;
    font-weight: bold;
}
QPushButton#convertBtn:hover {
    background-color: #0a4d7a;
}
QPushButton#convertBtn:disabled {
    background-color: #3d3d3d;
    color: #888888;
}
QPushButton#cancelBtn {
    background-color: #d1283e;
    color: white;
    padding: 6px 20px;
    border-radius: 8px;
    min-width: 60px;
}
QPushButton#cancelBtn:hover {
    background-color: #b82235;
}
QSlider#qualitySlider {
    background-color: transparent;
    border: none;
}
QSlider#qualitySlider::groove:horizontal {
    border: none;
    height: 6px;
    background-color: #000000;
    border-radius: 3px;
}
QSlider#qualitySlider::sub-page:horizontal {
    background-color: #8a5cf5;
    border-radius: 3px;
}
QSlider#qualitySlider::handle:horizontal {
    background-color: #ffffff;
    border: 1px solid #8a5cf5;
    width: 14px;
    height: 14px;
    margin: -4px 0;
    border-radius: 7px;
}
QSlider#qualitySlider::handle:horizontal:hover {
    background-color: #ffffff;
}
"""

# QGroupBox 内联样式，确保覆盖 Houdini 全局样式，消除标题下划线
_GROUPBOX_INLINE_STYLE = (
    "QGroupBox {"
    "  background-color: #1D1D20;"
    "  border: 1px solid #3d3d3d;"
    "  border-radius: 8px;"
    "  margin-top: 18px;"
    "  padding: 2px 8px 6px 8px;"
    "  font-weight: normal;"
    "  font-size: 12px;"
    "}"
    "QGroupBox::title {"
    "  subcontrol-origin: margin;"
    "  subcontrol-position: top left;"
    "  left: 12px;"
    "  top: -2px;"
    "  padding: 0 6px;"
    "  color: #cccccc;"
    "  text-decoration: none;"
    "}"
)


# ─── Data Classes ────────────────────────────────────────────────────

@dataclass
class VideoInfo:
    """视频信息数据类"""
    filename: str = ""
    filepath: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    total_frames: int = 0
    duration: float = 0.0
    codec: str = ""


# ─── Helper Functions（实现在 houtools/videoseq/ffmpeg.py）───────────────

def _video_path_from_mime(mime) -> str:
    """从拖放数据提取第一个有效的视频文件路径，无则返回空串。

    兼容资源管理器（URL）与纯文本路径（含引号包裹）；
    非视频扩展名或文件不存在返回空串。
    """
    candidates = []
    if mime.hasUrls():
        candidates.extend(url.toLocalFile() for url in mime.urls())
    if mime.hasText():
        text = mime.text().strip().strip('"').strip()
        if text:
            candidates.append(text)
    for path in candidates:
        if path.lower().endswith(tuple(VIDEO_EXTENSIONS)) and os.path.isfile(path):
            return path
    return ""


class _VideoSourceGroup(QGroupBox):
    """视频源分组框 —— 支持把视频文件直接拖入。

    识别逻辑见模块级 ``_video_path_from_mime``；子控件忽略的拖放事件
    会沿父链传播到这里，拖到组内任意位置都生效。
    窗口级（``_VideoToSequenceWindow``）也接收拖放作为兜底。
    """

    videoDropped = Signal(str)

    def __init__(self, title, parent=None):
        super().__init__(title, parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 — Qt 命名约定
        if _video_path_from_mime(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 — Qt 命名约定
        # dragEnter 接受后，dragMove 也须接受，drop 才会触发
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 — Qt 命名约定
        path = _video_path_from_mime(event.mimeData())
        if path:
            self.videoDropped.emit(path)
            event.acceptProposedAction()


# ─── Worker Threads ──────────────────────────────────────────────────

class _ProbeWorker(QThread):
    """后台线程：获取视频信息"""

    info_ready = Signal(object)   # VideoInfo
    error = Signal(str)

    def __init__(self, video_path: str, ffmpeg_path: str):
        super().__init__()
        self.video_path = video_path
        self.ffmpeg_path = ffmpeg_path
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            info = self._probe()
            if not self._cancelled:
                self.info_ready.emit(info)
        except Exception as e:
            if not self._cancelled:
                self.error.emit(str(e))

    def _probe(self) -> VideoInfo:
        info = VideoInfo(
            filepath=self.video_path,
            filename=os.path.basename(self.video_path),
        )
        startup = _get_startup_kwargs()
        ffprobe = _get_ffprobe_path(self.ffmpeg_path)

        if ffprobe:
            self._probe_ffprobe(info, ffprobe, startup)
        else:
            self._probe_ffmpeg(info, startup)

        return info

    def _probe_ffprobe(self, info: VideoInfo, ffprobe: str, startup: dict):
        """使用 ffprobe 获取视频元数据（JSON 格式）"""
        cmd = [
            ffprobe,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            self.video_path,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30, **startup
        )
        if result.returncode != 0:
            self._probe_ffmpeg(info, startup)
            return

        data = json.loads(result.stdout)

        # 查找视频流
        for stream in data.get("streams", []):
            if stream.get("codec_type") != "video":
                continue

            info.width = int(stream.get("width", 0))
            info.height = int(stream.get("height", 0))
            info.codec = stream.get("codec_name", "unknown")

            # FPS
            r_frame_rate = stream.get("r_frame_rate", "0/1")
            try:
                num, den = r_frame_rate.split("/")
                if int(den) > 0:
                    info.fps = int(num) / int(den)
            except (ValueError, ZeroDivisionError) as e:
                logger.debug("fps 解析失败 %r: %s", r_frame_rate, e)

            # 帧数（优先 nb_frames）
            nb = stream.get("nb_frames")
            if nb and nb != "N/A":
                try:
                    info.total_frames = int(nb)
                except ValueError as e:
                    logger.debug("nb_frames 解析失败 %r: %s", nb, e)

            # 时长
            dur = stream.get("duration")
            if dur and dur != "N/A":
                try:
                    info.duration = float(dur)
                except ValueError as e:
                    logger.debug("duration 解析失败 %r: %s", dur, e)
            break

        # format 级别的时长作为回退
        if info.duration == 0:
            fmt_dur = data.get("format", {}).get("duration")
            if fmt_dur:
                try:
                    info.duration = float(fmt_dur)
                except ValueError as e:
                    logger.debug("format.duration 解析失败 %r: %s", fmt_dur, e)

        # 通过时长计算帧数
        if info.total_frames == 0 and info.fps > 0 and info.duration > 0:
            info.total_frames = int(round(info.fps * info.duration))

        # ffprobe 未拿到帧数时用 ffmpeg 计数
        if info.total_frames == 0:
            self._count_frames_ffmpeg(info, startup)

    def _probe_ffmpeg(self, info: VideoInfo, startup: dict):
        """回退方案：用 ffmpeg 解析视频信息"""
        cmd = [
            self.ffmpeg_path,
            "-loglevel", "info",
            "-i", self.video_path,
            "-f", "null", "-",
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120, **startup
        )
        output = result.stderr

        # 解析分辨率
        res_match = re.search(r"(\d{2,5})x(\d{2,5})", output)
        if res_match:
            info.width = int(res_match.group(1))
            info.height = int(res_match.group(2))

        # 解析 FPS
        fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", output)
        if fps_match:
            info.fps = float(fps_match.group(1))

        # 解析 codec
        codec_match = re.search(r"Video:\s*(\w+)", output)
        if codec_match:
            info.codec = codec_match.group(1)

        # 解析时长
        dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", output)
        if dur_match:
            h, m, s = int(dur_match.group(1)), int(dur_match.group(2)), float(dur_match.group(3))
            info.duration = h * 3600 + m * 60 + s

        # 计算帧数
        if info.total_frames == 0 and info.fps > 0 and info.duration > 0:
            info.total_frames = int(round(info.fps * info.duration))

        if info.total_frames == 0:
            self._count_frames_ffmpeg(info, startup)

    def _count_frames_ffmpeg(self, info: VideoInfo, startup: dict):
        """使用 ffmpeg 逐帧计数获取精确帧数"""
        cmd = [
            self.ffmpeg_path,
            "-loglevel", "info",
            "-i", self.video_path,
            "-map", "0:v:0",
            "-f", "null", "-",
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300, **startup
        )
        # 尝试从 stderr 中解析 frame 数
        frame_match = re.search(r"frame=\s*(\d+)", result.stderr)
        if frame_match:
            info.total_frames = int(frame_match.group(1))


class _ExtractWorker(QThread):
    """后台线程：使用 ffmpeg 直接批量输出 JPG 序列

    性能优化方案：ffmpeg 单进程完成解码+JPEG编码+写文件，
    通过 -progress pipe:1 实时报告帧级进度。
    """

    progress = Signal(int, int)       # current_frame, total_frames
    # 注意：完成信号不可叫 finished——会覆盖 QThread 内建的 finished
    # （retire 兜底等依赖内建信号区分"线程收尾"与"业务完成"）
    done = Signal(int, str)           # total_frames, output_dir
    error = Signal(str)

    def __init__(self, video_path, output_dir, ffmpeg_path,
                 quality=90, start_frame=1001, padding=4, prefix="cam"):
        super().__init__()
        self.video_path = video_path
        self.output_dir = output_dir
        self.ffmpeg_path = ffmpeg_path
        self.quality = quality
        self.start_frame = start_frame
        self.padding = padding
        self.prefix = prefix
        self._cancelled = False
        self._process = None
        self.frames_written = 0   # 已写入帧数快照（取消时供窗口提示用）

    def cancel(self):
        # ffmpeg 未启动时（探测阶段）_process 为 None，terminate 无从谈起，
        # 这里只能置标志——真正的拦截在 _extract 的 Popen 前检查
        self._cancelled = True
        if self._process:
            try:
                self._process.terminate()
            except OSError:
                pass

    def run(self):
        try:
            self._extract()
        except Exception as e:
            # 取消途中再出异常也必须给一个收尾信号，否则窗口的
            # 「停止中…」状态永远等不到复位
            if self._cancelled:
                self.error.emit("转换已取消")
            else:
                self.error.emit(str(e))

    def _extract(self):
        # 探测阶段可达数十秒（ffprobe 30s / ffmpeg 回退 120s 超时），期间点过
        # 停止必须就此打住：此刻 _process 还是 None，cancel() 里的 terminate
        # 无从谈起，不在这里拦截 ffmpeg 会被照常启动
        if self._cancelled:
            self.error.emit("转换已取消")
            return

        os.makedirs(self.output_dir, exist_ok=True)
        startup = _get_startup_kwargs()

        # 先获取视频信息以确定总帧数
        info = self._get_video_info(startup)
        total_frames = info.total_frames
        if total_frames <= 0:
            self.error.emit("无法确定视频帧数，请检查视频文件。")
            return

        # 计算 qscale：UI quality 100(最好) → qscale 2，quality 1(最差) → qscale 31
        qscale = max(2, min(31, int(round(31 - (self.quality / 100.0) * 29))))

        # 构建输出文件模式：prefix.%0{padding}d.jpg
        output_pattern = os.path.join(
            self.output_dir,
            f"{self.prefix}.%0{self.padding}d.jpg",
        )

        # ffmpeg 直接输出 JPEG 序列（单进程，最高性能）
        # -progress pipe:1 将帧级进度信息输出到 stdout
        cmd = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel", "error",
            "-progress", "pipe:1",
            "-nostats",
            "-i", self.video_path,
            "-q:v", str(qscale),
            "-start_number", str(self.start_frame),
            "-an",             # 忽略音频
            "-sn",             # 忽略字幕
            "-map", "0:v:0",   # 只取第一个视频流
            "-y",
            output_pattern,
        ]

        # Popen 前最后一道检查，收窄"探测返回后、进程启动前"的竞态窗口
        if self._cancelled:
            self.error.emit("转换已取消")
            return

        # stderr 必须 DEVNULL：本线程只读 stdout 的 progress 流，无人读取
        # 的 stderr 管道写满（缓冲区约 4KB）后 ffmpeg 会卡死在写上，线程
        # 随之永久挂起；失败原因由下方 returncode 给出中文提示
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            **startup,
        )

        # 解析 stdout 中的 -progress 输出来追踪帧级进度
        # -progress 输出格式：
        #   frame=123
        #   fps=60.5
        #   ...
        #   progress=continue  (或 progress=end)
        frames_done = 0

        try:
            for raw_line in self._process.stdout:
                if self._cancelled:
                    break

                line = raw_line.decode("utf-8", errors="replace").strip()

                if line.startswith("frame="):
                    try:
                        frames_done = int(line.split("=", 1)[1].strip())
                    except (ValueError, IndexError) as e:
                        logger.debug("progress 行解析失败 %r: %s", line, e)
                    self.progress.emit(min(frames_done, total_frames), total_frames)

                # progress=end 表示 ffmpeg 完成
                if line == "progress=end":
                    break

        except (OSError, ValueError):
            # stdout 管道关闭（进程已终止）
            pass

        self.frames_written = frames_done

        # 收尾必须保证 ffmpeg 真正退出、且等待有界：取消若发生在 Popen 之前，
        # cancel() 里那次 terminate 被跳过，进程还活着且已无人读 stdout——
        # 它很快会卡死在 progress 管道写上（缓冲区填满即停），裸 wait() 无超时
        # 会把线程永久挂起（窗口 closeEvent 靠轮询 isRunning 延迟关闭，
        # 线程挂起 = 隐藏的窗口永不关闭）
        proc, self._process = self._process, None
        returncode = 0
        if proc is not None:
            if self._cancelled and proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass
            try:
                returncode = proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except OSError:
                    pass
                returncode = proc.wait(timeout=5)

        if self._cancelled:
            self.error.emit("转换已取消")
            return

        if returncode != 0:
            # stderr 已直弃 DEVNULL（无人读取会挂死线程），失败原因只能
            # 按退出码给中文提示
            self.error.emit(
                f"ffmpeg 退出码 {returncode}，"
                "视频可能损坏或参数不受支持。"
            )
            return

        # 完成帧数只报 ffmpeg progress 的 frames_done：目录扫描会把同前缀
        # 的旧序列文件（开工前未删/删除失败）数进去，虚报帧数
        self.done.emit(frames_done, self.output_dir)

    def _get_video_info(self, startup: dict) -> VideoInfo:
        """获取视频信息（供提取帧时使用）"""
        info = VideoInfo(filepath=self.video_path,
                         filename=os.path.basename(self.video_path))

        ffprobe = _get_ffprobe_path(self.ffmpeg_path)
        if ffprobe:
            result = None
            try:
                cmd = [
                    ffprobe, "-v", "quiet",
                    "-print_format", "json",
                    "-show_format", "-show_streams",
                    self.video_path,
                ]
                result = subprocess.run(
                    cmd, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=30, **startup
                )
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    for stream in data.get("streams", []):
                        if stream.get("codec_type") == "video":
                            info.width = int(stream.get("width", 0))
                            info.height = int(stream.get("height", 0))
                            r_frame_rate = stream.get("r_frame_rate", "0/1")
                            try:
                                num, den = r_frame_rate.split("/")
                                if int(den) > 0:
                                    info.fps = int(num) / int(den)
                            except (ValueError, ZeroDivisionError) as e:
                                logger.debug("fps 解析失败 %r: %s",
                                             r_frame_rate, e)
                            nb = stream.get("nb_frames")
                            if nb and nb != "N/A":
                                info.total_frames = int(nb)
                            dur = stream.get("duration")
                            if dur and dur != "N/A":
                                info.duration = float(dur)
                            break

                    if info.duration == 0:
                        fmt_dur = data.get("format", {}).get("duration")
                        if fmt_dur:
                            info.duration = float(fmt_dur)

                    if info.total_frames == 0 and info.fps > 0 and info.duration > 0:
                        info.total_frames = int(round(info.fps * info.duration))
                    return info
                logger.debug("ffprobe 退出码 %s，回退 ffmpeg 解析",
                             result.returncode)
            except Exception as e:
                logger.warning(
                    "ffprobe 探测失败，回退 ffmpeg 解析: %s"
                    " (returncode=%s, stdout=%r)",
                    e,
                    getattr(result, "returncode", None),
                    str(getattr(result, "stdout", "") or "")[:200],
                )

        # 回退：ffmpeg -i
        cmd = [
            self.ffmpeg_path, "-loglevel", "info",
            "-i", self.video_path, "-f", "null", "-",
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120, **startup
        )
        output = result.stderr

        res_match = re.search(r"(\d{2,5})x(\d{2,5})", output)
        if res_match:
            info.width = int(res_match.group(1))
            info.height = int(res_match.group(2))

        fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", output)
        if fps_match:
            info.fps = float(fps_match.group(1))

        dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", output)
        if dur_match:
            h, m, s = int(dur_match.group(1)), int(dur_match.group(2)), float(dur_match.group(3))
            info.duration = h * 3600 + m * 60 + s

        if info.total_frames == 0 and info.fps > 0 and info.duration > 0:
            info.total_frames = int(round(info.fps * info.duration))

        return info


# ─── Main Window ─────────────────────────────────────────────────────

def show_video_to_sequence_window():
    """显示视频转序列图窗口（单例经 houtools.ui.window_manager 登记）"""
    from houtools.ui import window_manager

    parent = None
    try:
        import hou
        parent = hou.qt.mainWindow()
    except (ImportError, AttributeError):
        pass
    return window_manager.open_window(
        "video_to_sequence", lambda: _VideoToSequenceWindow(parent)
    )


class _VideoToSequenceWindow(QDialog):
    """视频转序列图主窗口"""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("视频转序列图")
        self.setMinimumSize(500, 560)
        self.resize(540, 600)

        self._probe_worker = None
        self._extract_worker = None
        self._pending_close = False
        self._close_poll = None
        self._retired_workers = set()   # 运行中旧 worker 的引用位，防 GC 析构
        self._video_info = None
        self._current_video_path = ""

        self._build_ui()
        self.setStyleSheet(STYLE_SHEET)
        tool_fonts.apply(self)   # 工具统一字体（子树继承）
        self._apply_window_flags()
        # 整窗接收视频文件拖放（视频源分组框也单独支持，子控件未命中时兜底）
        self.setAcceptDrops(True)

    # ── Window flags (Windows) ───────────────────────────────────────

    def _apply_window_flags(self):
        """确保窗口在 Houdini 层级中正确显示（任务栏常驻，失败静默）"""
        from houtools.ui.taskbar import apply_appwindow_flags
        apply_appwindow_flags(self)

    # ── UI Construction ──────────────────────────────────────────────

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 8, 10, 8)
        main_layout.setSpacing(4)

        self._build_source_section(main_layout)
        self._build_info_section(main_layout)
        self._build_camera_section(main_layout)
        self._build_output_section(main_layout)
        self._build_progress_section(main_layout)
        main_layout.addStretch()

    def _build_source_section(self, parent_layout):
        group = _VideoSourceGroup("视频源")
        group.setStyleSheet(_GROUPBOX_INLINE_STYLE)
        group.videoDropped.connect(self._load_video)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 10, 8, 6)
        layout.setSpacing(6)

        file_row = QHBoxLayout()
        self._browse_btn = QPushButton("浏览...")
        self._browse_btn.setObjectName("browseBtn")
        self._browse_btn.clicked.connect(self._on_browse)
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("请选择、拖入或粘贴视频路径...")
        self._path_edit.setToolTip(
            "支持把视频文件直接拖进本区域；\n"
            "也可手动编辑路径，按 Enter 或移开焦点后加载，清空则重置"
        )
        # 可编辑后必须关闭自身拖放接收：否则拖文件到框上会被 Qt 当作
        # "插入文本"而非"加载视频"，事件也不再向父级分组框传播
        self._path_edit.setAcceptDrops(False)
        self._path_edit.textChanged.connect(self._on_path_text_changed)
        self._path_edit.editingFinished.connect(self._on_path_editing_finished)
        file_row.addWidget(self._path_edit, 1)
        file_row.addWidget(self._browse_btn)
        layout.addLayout(file_row)

        parent_layout.addWidget(group)

    def _build_info_section(self, parent_layout):
        group = QGroupBox("视频信息")
        group.setStyleSheet(_GROUPBOX_INLINE_STYLE)
        form = QFormLayout(group)
        form.setContentsMargins(8, 10, 8, 6)
        form.setSpacing(4)
        form.setLabelAlignment(Qt.AlignRight)

        label_style = "color: #888888; font-weight: normal;"
        value_style = "color: #ffffff; font-weight: normal;"

        self._info_labels = {}
        info_items = [
            ("filename", "文件名:"),
            ("resolution", "分辨率:"),
            ("fps", "帧率 (FPS):"),
            ("frames", "总帧数:"),
            ("duration", "时长:"),
            ("codec", "编码器:"),
        ]
        for key, label_text in info_items:
            lbl = QLabel(label_text)
            lbl.setStyleSheet(label_style)
            val = QLabel("-")
            val.setStyleSheet(value_style)
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            form.addRow(lbl, val)
            self._info_labels[key] = val

        parent_layout.addWidget(group)

    def _build_camera_section(self, parent_layout):
        group = QGroupBox("相机")
        group.setStyleSheet(_GROUPBOX_INLINE_STYLE)
        layout = QHBoxLayout(group)
        layout.setContentsMargins(8, 10, 8, 6)
        layout.setSpacing(6)

        cam_lbl = QLabel("选择相机:")
        cam_lbl.setStyleSheet("color: #888888;")
        self._camera_edit = QLineEdit()
        self._camera_edit.setReadOnly(True)
        self._camera_edit.setPlaceholderText("未选择相机...")
        self._pick_cam_btn = QPushButton("选择...")
        self._pick_cam_btn.setObjectName("browseBtn")
        self._pick_cam_btn.clicked.connect(self._on_pick_camera)
        layout.addWidget(cam_lbl)
        layout.addWidget(self._camera_edit, 1)
        layout.addWidget(self._pick_cam_btn)

        parent_layout.addWidget(group)
        self._auto_select_camera()

    def _build_output_section(self, parent_layout):
        group = QGroupBox("输出设置")
        group.setStyleSheet(_GROUPBOX_INLINE_STYLE)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 10, 8, 6)
        layout.setSpacing(6)

        # 质量
        quality_row = QHBoxLayout()
        quality_lbl = QLabel("JPG 质量:")
        quality_lbl.setStyleSheet("color: #888888; min-width: 80px;")
        self._quality_slider = QSlider(Qt.Horizontal)
        self._quality_slider.setObjectName("qualitySlider")
        self._quality_slider.setRange(1, 100)
        self._quality_slider.setValue(90)
        self._quality_spin = QSpinBox()
        self._quality_spin.setRange(1, 100)
        self._quality_spin.setValue(90)
        self._quality_spin.setSuffix(" %")
        self._quality_slider.valueChanged.connect(self._quality_spin.setValue)
        self._quality_spin.valueChanged.connect(self._quality_slider.setValue)
        quality_row.addWidget(quality_lbl)
        quality_row.addWidget(self._quality_slider, 1)
        quality_row.addWidget(self._quality_spin)
        layout.addLayout(quality_row)

        # 起始帧 + 帧号位数
        frame_row = QHBoxLayout()
        start_lbl = QLabel("起始帧号:")
        start_lbl.setStyleSheet("color: #888888; min-width: 80px;")
        self._start_frame_spin = QSpinBox()
        self._start_frame_spin.setRange(0, 999999)
        self._start_frame_spin.setValue(self._get_rfstart())
        padding_lbl = QLabel("帧号位数:")
        padding_lbl.setStyleSheet("color: #888888;")
        self._padding_spin = QSpinBox()
        self._padding_spin.setRange(1, 8)
        self._padding_spin.setValue(4)
        frame_row.addWidget(start_lbl)
        frame_row.addWidget(self._start_frame_spin)
        frame_row.addSpacing(16)
        frame_row.addWidget(padding_lbl)
        frame_row.addWidget(self._padding_spin)
        layout.addLayout(frame_row)

        # 文件名前缀
        prefix_row = QHBoxLayout()
        prefix_lbl = QLabel("文件名前缀:")
        prefix_lbl.setStyleSheet("color: #888888; min-width: 80px;")
        self._prefix_edit = QLineEdit("cam")
        self._prefix_edit.setPlaceholderText("例如: cam, render, plate")
        prefix_row.addWidget(prefix_lbl)
        prefix_row.addWidget(self._prefix_edit, 1)
        layout.addLayout(prefix_row)

        # 输出目录
        dir_row = QHBoxLayout()
        dir_lbl = QLabel("输出目录:")
        dir_lbl.setStyleSheet("color: #888888; min-width: 80px;")
        self._output_dir_edit = QLineEdit()
        self._output_dir_edit.setPlaceholderText(
            "$HIP/images/{视频文件名}/"
        )
        self._output_dir_btn = QPushButton("浏览...")
        self._output_dir_btn.setObjectName("browseBtn")
        self._output_dir_btn.clicked.connect(self._on_browse_output_dir)
        dir_row.addWidget(dir_lbl)
        dir_row.addWidget(self._output_dir_edit, 1)
        dir_row.addWidget(self._output_dir_btn)
        layout.addLayout(dir_row)

        # 输出路径预览
        self._output_path_label = QLabel(
            "输出路径: $HIP/images/{视频文件名}/cam.$F4.jpg"
        )
        self._output_path_label.setStyleSheet(
            "color: #888888; font-size: 11px; padding: 2px 0;"
        )
        self._output_path_label.setWordWrap(True)
        layout.addWidget(self._output_path_label)

        # 更新预览
        self._start_frame_spin.valueChanged.connect(self._update_output_preview)
        self._padding_spin.valueChanged.connect(self._update_output_preview)
        self._prefix_edit.textChanged.connect(self._update_output_preview)

        parent_layout.addWidget(group)

    def _build_progress_section(self, parent_layout):
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #888888; font-size: 11px;")
        self._status_label.setVisible(False)
        parent_layout.addWidget(self._status_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat("%v / %m 帧  (%p%)")
        parent_layout.addWidget(self._progress_bar)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 8, 0, 0)
        btn_row.addStretch()
        self._convert_btn = QPushButton("开始转换")
        self._convert_btn.setObjectName("convertBtn")
        self._convert_btn.setAutoDefault(False)
        self._convert_btn.clicked.connect(self._on_convert)
        btn_row.addWidget(self._convert_btn)

        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setObjectName("cancelBtn")
        self._cancel_btn.setAutoDefault(False)
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._cancel_btn.hide()
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        parent_layout.addLayout(btn_row)

    # ── Output Preview ───────────────────────────────────────────────

    def _update_output_preview(self):
        prefix = self._prefix_edit.text().strip() or "cam"
        padding = self._padding_spin.value()
        start = self._start_frame_spin.value()
        self._output_path_label.setText(
            f"输出路径示例: .../{prefix}.{str(start).zfill(padding)}.jpg"
        )

    # ── Event Handlers ───────────────────────────────────────────────

    def _on_browse(self):
        ext_filter = "视频文件 ({})".format(
            " ".join(f"*{e}" for e in VIDEO_EXTENSIONS)
        )
        filepath, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", "", ext_filter
        )
        if not filepath:
            return
        self._load_video(filepath)

    def _load_video(self, filepath: str):
        """加载视频：重置状态、按文件名填充输出目录并启动后台探测。

        浏览按钮、拖放（窗口级与 _VideoSourceGroup）和路径框手动加载
        共用此入口。
        """
        self._reset_video_state()
        self._current_video_path = filepath
        self._video_info = None
        self._path_edit.setText(filepath)  # textChanged: text==loaded → no-op
        video_name = os.path.splitext(os.path.basename(filepath))[0]
        self._output_dir_edit.setText(f"$HIP/images/{video_name}/")
        self._update_output_preview()
        self._probe_video(filepath)

    def _reset_video_state(self):
        """清空已加载的视频状态（路径框被手动清空时也走这里）。"""
        self._current_video_path = ""
        self._video_info = None
        self._status_label.setText("")
        self._status_label.setVisible(False)
        self._progress_bar.setValue(0)
        self._reset_info_labels()

    def _on_path_text_changed(self, text: str):
        """路径框即时编辑响应：手动清空 → 重置已加载状态。

        程序化 setText 在 _load_video 中先设好 _current_video_path，
        因此触发本槽时 text 总等于已加载路径，不会误清。
        """
        if not text.strip():
            self._reset_video_state()

    def _on_path_editing_finished(self):
        """路径框按 Enter / 失焦：有效视频路径则加载，无效则恢复原值。"""
        text = self._path_edit.text().strip()
        if not text or text == self._current_video_path:
            return
        if text.lower().endswith(tuple(VIDEO_EXTENSIONS)) and os.path.isfile(text):
            self._load_video(text)
            return
        self._status_label.setVisible(True)
        self._status_label.setText("路径无效或不是支持的视频格式，已恢复")
        self._status_label.setStyleSheet("color: #d1283e; font-size: 12px;")
        self._path_edit.setText(self._current_video_path)

    # ── 拖放（整窗接收；光标落在视频源分组框内时由分组框优先处理）──

    def dragEnterEvent(self, event) -> None:  # noqa: N802 — Qt 命名约定
        if _video_path_from_mime(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 — Qt 命名约定
        # dragEnter 接受后，dragMove 也须接受，drop 才会触发
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 — Qt 命名约定
        path = _video_path_from_mime(event.mimeData())
        if path:
            event.acceptProposedAction()
            self._load_video(path)

    def _on_browse_output_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if directory:
            self._output_dir_edit.setText(directory)

    def _on_convert(self):
        # 防御：停止中按钮已禁用，正常不会走到这里；绝不覆盖仍在运行的
        # worker 引用（运行中的 QThread 失去引用会被 GC 析构，Qt6 下崩溃）
        if self._extract_worker is not None and self._extract_worker.isRunning():
            logger.debug("转换仍在进行，忽略本次开始请求")
            return

        if not self._current_video_path:
            dialogs.warn(self, "提示", "请先选择一个视频文件。")
            return

        if not self._video_info:
            dialogs.warn(self, "提示", "视频信息尚未获取，请稍候。")
            return

        if self._video_info.total_frames <= 0:
            dialogs.warn(self, "提示", "无法获取视频帧数，请检查视频文件。")
            return

        from houtools.videoseq.ffmpeg import find_ffmpeg
        ffmpeg_path = find_ffmpeg()
        if not ffmpeg_path:
            dialogs.warn(
                self, "错误",
                "未找到 ffmpeg。\n"
                "通常随 Houdini 附带：$HFS/bin/hffmpeg；\n"
                "也可将 ffmpeg.exe 放到项目根目录或加入系统 PATH。",
            )
            return

        # 解析输出目录（展开 Houdini 变量）
        raw_output_dir = self._output_dir_edit.text().strip()
        if not raw_output_dir:
            dialogs.warn(self, "提示", "请设置输出目录。")
            return

        try:
            import hou
            output_dir = hou.text.expandString(raw_output_dir)
        except ImportError:
            output_dir = raw_output_dir

        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as e:
            dialogs.warn(
                self, "错误", f"无法创建输出目录:\n{output_dir}\n\n{e}")
            return

        quality = self._quality_spin.value()
        start_frame = self._start_frame_spin.value()
        padding = self._padding_spin.value()
        prefix = self._prefix_edit.text().strip() or "cam"

        # 起始帧位数超过帧号位数：文件名位数不统一，背景路径 $F{padding}
        # 的宽度语义也会与实际文件名失配
        if len(str(start_frame)) > padding:
            if not dialogs.question(
                self, "帧号位数不足",
                f"起始帧号 {start_frame} 共 {len(str(start_frame))} 位，"
                f"超过帧号位数 {padding}，输出文件名位数将不统一，"
                f"背景路径 $F{padding} 也会与实际文件名失配。\n是否继续？",
            ):
                return

        # 旧序列检查：同前缀+帧号+同扩展名的既有文件，避免新旧帧混杂
        stale = self._find_stale_sequence_files(output_dir, prefix)
        if stale and dialogs.question(
            self, "输出目录已有旧序列",
            f"输出目录已有同前缀旧序列 {len(stale)} 个文件，"
            "是否删除以避免新旧帧混杂？",
        ):
            for path in stale:
                try:
                    os.remove(path)
                except OSError as e:
                    logger.warning("删除旧序列文件失败 %s: %s", path, e)

        # 保存转换上下文快照，供完成回调使用（完成后不读用户可能已改动的 UI）
        self._last_output_dir = raw_output_dir
        self._last_prefix = prefix
        self._last_padding = padding

        total = self._video_info.total_frames

        # 切换到转换中状态
        self._convert_btn.hide()
        self._cancel_btn.show()

        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(0)
        self._status_label.setVisible(True)
        self._status_label.setText(
            f"正在提取 {total} 帧 (质量: {quality}%, 起始帧: {start_frame})..."
        )
        self._status_label.setStyleSheet("color: #0d6399; font-size: 12px;")

        self._extract_worker = _ExtractWorker(
            self._current_video_path, output_dir, ffmpeg_path,
            quality, start_frame, padding, prefix,
        )
        self._extract_worker.progress.connect(self._on_extract_progress)
        self._extract_worker.done.connect(self._on_extract_finished)
        self._extract_worker.error.connect(self._on_extract_error)
        self._extract_worker.start()

    def _on_cancel(self):
        extract_running = bool(
            self._extract_worker is not None
            and self._extract_worker.isRunning()
        )
        if extract_running:
            self._extract_worker.cancel()
        if self._probe_worker and self._probe_worker.isRunning():
            self._probe_worker.cancel()
        if not extract_running:
            # 无提取在跑（防御路径）：直接复位空闲
            self._reset_convert_ui()
            self._status_label.setVisible(True)
            self._status_label.setText("已取消")
            self._status_label.setStyleSheet("color: #d1283e; font-size: 12px;")
            return
        # 「停止中」：不立即复位按钮（对齐 Automation 的停止语义）——
        # ffmpeg 收尾（terminate + 有界等待）需要时间，此刻二次 Start 会
        # 被旧 worker 迟到的收尾信号打翻状态；等 done/error 到达再复位
        self._convert_btn.setEnabled(False)
        self._cancel_btn.setText("停止中…")
        self._cancel_btn.setEnabled(False)
        self._status_label.setVisible(True)
        self._status_label.setText("停止中…（正在结束 ffmpeg）")
        self._status_label.setStyleSheet("color: #d1283e; font-size: 12px;")

    # ── Probe Callbacks ──────────────────────────────────────────────

    def _probe_video(self, filepath):
        from houtools.videoseq.ffmpeg import find_ffmpeg
        ffmpeg_path = find_ffmpeg()
        if not ffmpeg_path:
            self._status_label.setVisible(True)
            self._status_label.setText("错误: 未找到 ffmpeg")
            self._status_label.setStyleSheet("color: #d1283e; font-size: 12px;")
            return

        # 取消之前的探测任务：协作式置标志后不等待——探测线程阻塞在不可
        # 中断的子进程上（ffprobe 30s / ffmpeg 120s 超时），wait 白等；
        # 旧 worker 挂入保留列表防运行中被 GC 析构，迟到的信号由槽内
        # sender 过滤丢弃（当前有效 sender 已切换为新 worker）
        old = self._probe_worker
        if old is not None and old.isRunning():
            old.cancel()
            self._retire_worker(old)

        self._status_label.setVisible(True)
        self._status_label.setText("正在分析视频信息...")
        self._status_label.setStyleSheet("color: #e0cb56; font-size: 12px;")

        self._probe_worker = _ProbeWorker(filepath, ffmpeg_path)
        self._probe_worker.info_ready.connect(self._on_probe_ready)
        self._probe_worker.error.connect(self._on_probe_error)
        self._probe_worker.start()

    def _on_probe_ready(self, info):
        # 迟到的旧探测（快速换片后）不应用：sender 已不是当前 worker，
        # 或探测结果不属于当前视频
        if self.sender() is not self._probe_worker:
            return
        if info.filepath != self._current_video_path:
            return
        self._video_info = info

        self._info_labels["filename"].setText(info.filename)
        self._info_labels["resolution"].setText(
            f"{info.width} x {info.height}" if info.width else "-"
        )
        self._info_labels["fps"].setText(
            f"{info.fps:.3f}" if info.fps > 0 else "-"
        )
        self._info_labels["frames"].setText(
            str(info.total_frames) if info.total_frames > 0 else "-"
        )

        if info.duration > 0:
            h = int(info.duration // 3600)
            m = int((info.duration % 3600) // 60)
            s = info.duration % 60
            self._info_labels["duration"].setText(
                f"{h:02d}:{m:02d}:{s:06.3f}"
            )
        else:
            self._info_labels["duration"].setText("-")

        self._info_labels["codec"].setText(info.codec or "-")

        self._status_label.setVisible(True)
        self._status_label.setText("视频信息已获取")
        self._status_label.setStyleSheet("color: #87cc8e; font-size: 12px;")

    def _on_probe_error(self, error_msg):
        if self.sender() is not self._probe_worker:
            return  # 迟到的旧探测错误——丢弃
        self._status_label.setVisible(True)
        self._status_label.setText(f"分析失败: {error_msg}")
        self._status_label.setStyleSheet("color: #d1283e; font-size: 12px;")
        logger.error("Video probe failed: %s", error_msg)

    # ── Extract Callbacks ────────────────────────────────────────────

    def _on_extract_progress(self, current, total):
        if self.sender() is not self._extract_worker:
            return  # 迟到的旧 worker 进度——丢弃
        self._progress_bar.setValue(current)
        self._status_label.setVisible(True)
        self._status_label.setText(
            f"正在提取: {current} / {total} 帧"
        )

    def _on_extract_finished(self, total_frames, output_dir):
        if self.sender() is not self._extract_worker:
            return  # 迟到的旧 worker 完成信号——丢弃
        self._reset_convert_ui()
        self._progress_bar.setValue(total_frames)
        self._status_label.setVisible(True)
        self._status_label.setText(
            f"转换完成！共提取 {total_frames} 帧"
        )
        self._status_label.setStyleSheet("color: #87cc8e; font-size: 12px;")

        # 若选择了相机，设置其 Background Image 参数
        cam_path = self._camera_edit.text().strip()
        if cam_path:
            self._set_camera_background(cam_path, output_dir)

        dialogs.info(
            self, "完成",
            f"成功提取 {total_frames} 帧序列图\n\n输出目录:\n{output_dir}",
        )

    def _set_camera_background(self, cam_path, output_dir):
        """设置相机的 Background Image 参数"""
        try:
            import hou
            cam = hou.node(cam_path)
            if not cam:
                return

            prefix = getattr(self, "_last_prefix", "cam")
            padding = getattr(self, "_last_padding", 4)

            # 用开工时快照的原始目录文本构建路径（保留 $HIP 等变量，渲染
            # 时展开）——完成后用户可能已改动 UI 里的目录
            raw_dir = (getattr(self, "_last_output_dir", "")
                       or self._output_dir_edit.text().strip())
            if not raw_dir:
                return
            if not raw_dir.endswith("/"):
                raw_dir += "/"
            bg_path = f"{raw_dir}{prefix}.$F{padding}.jpg"

            # 设置 background 参数
            bg_parm = cam.parm("vm_background")
            if bg_parm:
                bg_parm.set(bg_path)

            # 设置 vm_bgenable 为 0（禁用）
            use_bg_parm = cam.parm("vm_bgenable")
            if use_bg_parm:
                use_bg_parm.set(0)

        except ImportError:
            pass

    def _on_extract_error(self, error_msg):
        if self.sender() is not self._extract_worker:
            return  # 迟到的旧 worker 错误——丢弃
        self._reset_convert_ui()
        self._status_label.setVisible(True)
        if error_msg == "转换已取消":
            # 取消收尾：报告已写帧数与保留位置（帧数为 worker 内的快照）
            worker = self.sender()
            frames = getattr(worker, "frames_written", 0)
            if frames > 0:
                self._status_label.setText(
                    f"转换已取消（已写入 {frames} 帧，"
                    f"保留在 {worker.output_dir}）"
                )
            else:
                self._status_label.setText("转换已取消")
        else:
            self._status_label.setText(f"转换失败: {error_msg}")
            dialogs.warn(self, "错误", f"转换失败:\n{error_msg}")
        self._status_label.setStyleSheet("color: #d1283e; font-size: 12px;")

    # ── Helpers ──────────────────────────────────────────────────────

    def _get_rfstart(self) -> int:
        """从 Houdini 的 $RFSTART 表达式获取默认起始帧号"""
        try:
            import hou
            val = hou.getenv("RFSTART")
            if val is not None and str(val).strip():
                return int(float(str(val)))
            val = hou.text.expandString("$RFSTART")
            if val and val != "$RFSTART":
                return int(float(val))
        except (ImportError, ValueError, TypeError, AttributeError):
            pass
        return 1001

    def _find_scene_cameras(self) -> list:
        """查找当前场景中的所有相机节点"""
        try:
            import hou
            cameras = []
            obj = hou.node("/obj")
            if obj:
                for node in obj.allSubChildren():
                    if node.type().name() == "cam":
                        cameras.append(node.path())
            return cameras
        except ImportError:
            return []

    def _auto_select_camera(self):
        """根据场景中的相机数量自动选择"""
        cameras = self._find_scene_cameras()
        if len(cameras) == 1:
            self._camera_edit.setText(cameras[0])

    def _on_pick_camera(self):
        """打开相机选择对话框"""
        try:
            import hou  # noqa: F401 — 仅探测 hou 可用性
        except ImportError:
            dialogs.warn(self, "提示", "此功能仅在 Houdini 中可用。")
            return

        cameras = self._find_scene_cameras()
        if not cameras:
            dialogs.info(self, "提示", "当前场景中没有找到相机。")
            return

        cam_names = [c.split("/")[-1] for c in cameras]
        current = self._camera_edit.text()
        current_idx = cameras.index(current) if current in cameras else 0

        # 实例化调用：静态便利函数 getItem 拿不到内部按钮（英文系统出
        # 英文按钮），无法中文化
        dlg = QInputDialog(self)
        dlg.setWindowTitle("选择相机")
        dlg.setLabelText("场景中的相机:")
        dlg.setComboBoxItems(cam_names)
        dlg.setComboBoxEditable(False)
        dlg.setTextValue(cam_names[current_idx])
        dialogs.localize_buttons(dlg)
        ok = dlg.exec_() == QDialog.Accepted
        name = dlg.textValue()
        dlg.deleteLater()
        if ok and name:
            idx = cam_names.index(name)
            self._camera_edit.setText(cameras[idx])

    def _reset_info_labels(self):
        for lbl in self._info_labels.values():
            lbl.setText("-")

    def _reset_convert_ui(self):
        """转换相关控件复位为空闲态（完成/失败/取消收尾共用）。"""
        self._convert_btn.setEnabled(True)
        self._convert_btn.show()
        self._cancel_btn.setEnabled(True)
        self._cancel_btn.setText("取消")
        self._cancel_btn.hide()

    @staticmethod
    def _find_stale_sequence_files(output_dir, prefix):
        """列出输出目录中与本次输出同模式的既有序列文件（前缀.数字.jpg）。"""
        pat = re.compile(re.escape(prefix) + r"\.(\d+)\.jpg$")
        try:
            names = os.listdir(output_dir)
        except OSError as e:
            logger.debug("扫描输出目录失败 %s: %s", output_dir, e)
            return []
        return [
            os.path.join(output_dir, n)
            for n in names
            if pat.fullmatch(n) and os.path.isfile(os.path.join(output_dir, n))
        ]

    def _retire_worker(self, worker):
        """给仍在运行的旧 worker 保引用，直到其线程真正退出。

        引用一旦无人持有，Python 回收会析构运行中的 QThread（Qt6 下
        qFatal 崩溃）；worker 的内建 finished 到达后自动移出保留列表。
        """
        if worker is None or worker in self._retired_workers:
            return
        self._retired_workers.add(worker)
        worker.finished.connect(
            lambda w=worker: self._retired_workers.discard(w))

    # ── Lifecycle ────────────────────────────────────────────────────

    def is_close_deferred(self):
        """window_manager.close_all 用：closeEvent 是否被推迟（转换进行中走隐藏+轮询）。"""
        return bool(getattr(self, "_pending_close", False))

    def showEvent(self, event):  # noqa: N802 — Qt 命名约定
        # 重开窗口即撤销"取消并关闭"的决定（转换在进入 pending 时已请求
        # 取消）：清推迟标记、停轮询，窗口保持打开
        if getattr(self, "_pending_close", False):
            self._pending_close = False
            if self._close_poll is not None:
                self._close_poll.stop()
        super().showEvent(event)

    def closeEvent(self, event):  # noqa: N802 — Qt 命名约定
        if self._probe_worker and self._probe_worker.isRunning():
            # 置取消但不等待：探测线程阻塞在不可中断的子进程上，wait 必然
            # 白等（最长可达 ffprobe 30s + ffmpeg 计数 300s）；其迟到信号
            # 由槽内 sender 过滤或随窗口销毁被 Qt 丢弃
            self._probe_worker.cancel()
        if self._extract_worker and self._extract_worker.isRunning():
            if self._pending_close:
                # 已处于延迟关闭中（隐藏窗口再被 close_all 触发）：
                # 不重复弹确认，维持推迟
                event.ignore()
                return
            if not dialogs.question(
                self, "转换进行中",
                "视频转换仍在进行，取消并关闭窗口？",
            ):
                event.ignore()
                return
            # 非阻塞关闭:取消后先隐藏窗口,QTimer 轮询线程退出再真正关 ——
            # 直接 wait() 会卡 UI,线程对象也不能先于运行中的线程销毁
            self._extract_worker.cancel()
            self._pending_close = True
            self.hide()
            event.ignore()
            if self._close_poll is None:
                self._close_poll = QTimer(self)
                self._close_poll.timeout.connect(self._poll_worker_done)
            self._close_poll.start(200)
            return
        super().closeEvent(event)

    def _poll_worker_done(self):
        """转换线程退出后完成延迟关闭。"""
        if self._extract_worker is not None and self._extract_worker.isRunning():
            return
        if getattr(self, "_pending_close", False):
            self._pending_close = False
            if self._close_poll is not None:
                self._close_poll.stop()
            self.close()
            # 延迟关闭的窗口必须自删：window_manager.close_all 对声明了
            # is_close_deferred 的窗口移入 _closing 持引用、不再兜底
            # deleteLater（防 GC 析构运行中的 QThread），收尾在这里销毁
            self.deleteLater()


# ─── Entry Point ─────────────────────────────────────────────────────

def run(kwargs=None):
    """工具入口函数（由 shelf 调用；kwargs 为 Houdini 传入的上下文，允许无参调用）"""
    show_video_to_sequence_window()
