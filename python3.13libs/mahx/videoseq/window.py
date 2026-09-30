"""MA Video to Sequence - 视频转序列图工具

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
    QFileDialog, QMessageBox, QProgressBar, QInputDialog,
)
from PySide6.QtCore import Qt, QThread, Signal

from mahx.videoseq.ffmpeg import find_ffprobe as _get_ffprobe_path
from mahx.videoseq.ffmpeg import get_startup_kwargs as _get_startup_kwargs

from mahx.core.log import get_logger
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


# ─── Helper Functions（实现在 mahx/videoseq/ffmpeg.py）───────────────


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
            cmd, capture_output=True, text=True, timeout=30, **startup
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
            except (ValueError, ZeroDivisionError):
                pass

            # 帧数（优先 nb_frames）
            nb = stream.get("nb_frames")
            if nb and nb != "N/A":
                try:
                    info.total_frames = int(nb)
                except ValueError:
                    pass

            # 时长
            dur = stream.get("duration")
            if dur and dur != "N/A":
                try:
                    info.duration = float(dur)
                except ValueError:
                    pass
            break

        # format 级别的时长作为回退
        if info.duration == 0:
            fmt_dur = data.get("format", {}).get("duration")
            if fmt_dur:
                try:
                    info.duration = float(fmt_dur)
                except ValueError:
                    pass

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
            cmd, capture_output=True, text=True, timeout=120, **startup
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
            cmd, capture_output=True, text=True, timeout=300, **startup
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
    finished = Signal(int, str)       # total_frames, output_dir
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

    def cancel(self):
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
            if not self._cancelled:
                self.error.emit(str(e))

    def _extract(self):
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

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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
                    except (ValueError, IndexError):
                        pass
                    self.progress.emit(min(frames_done, total_frames), total_frames)

                # progress=end 表示 ffmpeg 完成
                if line == "progress=end":
                    break

        except (OSError, ValueError):
            # stdout 管道关闭（进程已终止）
            pass

        # 等待进程结束
        if self._process:
            self._process.wait()
            self._process = None

        if self._cancelled:
            self.error.emit("转换已取消")
            return

        # 验证实际输出的文件数
        actual_count = len([
            f for f in os.listdir(self.output_dir)
            if f.lower().endswith(".jpg") and f.startswith(self.prefix + ".")
        ])
        final_count = max(frames_done, actual_count)

        self.finished.emit(final_count, self.output_dir)

    def _get_video_info(self, startup: dict) -> VideoInfo:
        """获取视频信息（供提取帧时使用）"""
        info = VideoInfo(filepath=self.video_path,
                         filename=os.path.basename(self.video_path))

        ffprobe = _get_ffprobe_path(self.ffmpeg_path)
        if ffprobe:
            try:
                cmd = [
                    ffprobe, "-v", "quiet",
                    "-print_format", "json",
                    "-show_format", "-show_streams",
                    self.video_path,
                ]
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=30, **startup
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
                            except (ValueError, ZeroDivisionError):
                                pass
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
            except Exception:
                pass

        # 回退：ffmpeg -i
        cmd = [
            self.ffmpeg_path, "-loglevel", "info",
            "-i", self.video_path, "-f", "null", "-",
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, **startup
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
    """显示视频转序列图窗口（单例经 mahx.ui.window_manager 登记）"""
    from mahx.ui import window_manager

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
        self.setWindowTitle("MA 视频转序列图")
        self.setMinimumSize(500, 560)
        self.resize(540, 600)

        self._probe_worker = None
        self._extract_worker = None
        self._video_info = None
        self._current_video_path = ""

        self._build_ui()
        self.setStyleSheet(STYLE_SHEET)
        self._apply_window_flags()

    # ── Window flags (Windows) ───────────────────────────────────────

    def _apply_window_flags(self):
        """确保窗口在 Houdini 层级中正确显示"""
        if os.name == "nt":
            try:
                import ctypes
                hwnd = int(self.winId())
                GWL_EXSTYLE = -20
                WS_EX_APPWINDOW = 0x00040000
                ctypes.windll.user32.SetWindowLongW(
                    hwnd, GWL_EXSTYLE, WS_EX_APPWINDOW
                )
            except Exception:
                pass

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
        group = QGroupBox("视频源")
        group.setStyleSheet(_GROUPBOX_INLINE_STYLE)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 10, 8, 6)
        layout.setSpacing(6)

        file_row = QHBoxLayout()
        self._browse_btn = QPushButton("浏览...")
        self._browse_btn.setObjectName("browseBtn")
        self._browse_btn.clicked.connect(self._on_browse)
        self._path_edit = QLineEdit()
        self._path_edit.setReadOnly(True)
        self._path_edit.setPlaceholderText("请选择视频文件...")
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
        self._convert_btn.clicked.connect(self._on_convert)
        btn_row.addWidget(self._convert_btn)

        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setObjectName("cancelBtn")
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

        self._current_video_path = filepath
        self._video_info = None
        self._path_edit.setText(filepath)
        self._status_label.setText("")
        self._status_label.setVisible(False)
        self._progress_bar.setValue(0)
        self._reset_info_labels()

        # 自动填充输出目录
        video_name = os.path.splitext(os.path.basename(filepath))[0]
        self._output_dir_edit.setText(f"$HIP/images/{video_name}/")
        self._update_output_preview()

        self._probe_video(filepath)

    def _on_browse_output_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if directory:
            self._output_dir_edit.setText(directory)

    def _on_convert(self):
        if not self._current_video_path:
            QMessageBox.warning(self, "提示", "请先选择一个视频文件。")
            return

        if not self._video_info:
            QMessageBox.warning(self, "提示", "视频信息尚未获取，请稍候。")
            return

        if self._video_info.total_frames <= 0:
            QMessageBox.warning(self, "提示", "无法获取视频帧数，请检查视频文件。")
            return

        from mahx.videoseq.ffmpeg import find_ffmpeg
        ffmpeg_path = find_ffmpeg()
        if not ffmpeg_path:
            QMessageBox.critical(
                self, "错误",
                "未找到 ffmpeg，请确保 ffmpeg.exe 位于项目根目录或系统 PATH 中。",
            )
            return

        # 解析输出目录（展开 Houdini 变量）
        raw_output_dir = self._output_dir_edit.text().strip()
        if not raw_output_dir:
            QMessageBox.warning(self, "提示", "请设置输出目录。")
            return

        try:
            import hou
            output_dir = hou.text.expandString(raw_output_dir)
        except ImportError:
            output_dir = raw_output_dir

        os.makedirs(output_dir, exist_ok=True)

        quality = self._quality_spin.value()
        start_frame = self._start_frame_spin.value()
        padding = self._padding_spin.value()
        prefix = self._prefix_edit.text().strip() or "cam"

        # 保存转换上下文，供完成回调使用
        self._last_prefix = prefix
        self._last_padding = padding

        total = self._video_info.total_frames

        # 切换到转换中状态
        self._convert_btn.hide()
        self._cancel_btn.show()

        # 取消之前的提取任务
        if self._extract_worker and self._extract_worker.isRunning():
            self._extract_worker.cancel()
            self._extract_worker.wait(3000)

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
        self._extract_worker.finished.connect(self._on_extract_finished)
        self._extract_worker.error.connect(self._on_extract_error)
        self._extract_worker.start()

    def _on_cancel(self):
        if self._extract_worker and self._extract_worker.isRunning():
            self._extract_worker.cancel()
        if self._probe_worker and self._probe_worker.isRunning():
            self._probe_worker.cancel()
        self._cancel_btn.hide()
        self._convert_btn.show()
        self._status_label.setVisible(True)
        self._status_label.setText("已取消")
        self._status_label.setStyleSheet("color: #d1283e; font-size: 12px;")

    # ── Probe Callbacks ──────────────────────────────────────────────

    def _probe_video(self, filepath):
        from mahx.videoseq.ffmpeg import find_ffmpeg
        ffmpeg_path = find_ffmpeg()
        if not ffmpeg_path:
            self._status_label.setVisible(True)
            self._status_label.setText("错误: 未找到 ffmpeg")
            self._status_label.setStyleSheet("color: #d1283e; font-size: 12px;")
            return

        # 取消之前的探测任务
        if self._probe_worker and self._probe_worker.isRunning():
            self._probe_worker.cancel()
            self._probe_worker.wait(2000)

        self._status_label.setVisible(True)
        self._status_label.setText("正在分析视频信息...")
        self._status_label.setStyleSheet("color: #e0cb56; font-size: 12px;")

        self._probe_worker = _ProbeWorker(filepath, ffmpeg_path)
        self._probe_worker.info_ready.connect(self._on_probe_ready)
        self._probe_worker.error.connect(self._on_probe_error)
        self._probe_worker.start()

    def _on_probe_ready(self, info):
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
        self._status_label.setVisible(True)
        self._status_label.setText(f"分析失败: {error_msg}")
        self._status_label.setStyleSheet("color: #d1283e; font-size: 12px;")
        logger.error("Video probe failed: %s", error_msg)

    # ── Extract Callbacks ────────────────────────────────────────────

    def _on_extract_progress(self, current, total):
        self._progress_bar.setValue(current)
        self._status_label.setVisible(True)
        self._status_label.setText(
            f"正在提取: {current} / {total} 帧"
        )

    def _on_extract_finished(self, total_frames, output_dir):
        self._cancel_btn.hide()
        self._convert_btn.show()
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

        QMessageBox.information(
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

            # 从 UI 的输出目录构建路径（保留 Houdini 变量如 $HIP）
            raw_dir = self._output_dir_edit.text().strip()
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
        self._cancel_btn.hide()
        self._convert_btn.show()
        self._status_label.setVisible(True)
        self._status_label.setText(f"转换失败: {error_msg}")
        self._status_label.setStyleSheet("color: #d1283e; font-size: 12px;")
        if error_msg != "转换已取消":
            QMessageBox.warning(self, "错误", f"转换失败:\n{error_msg}")

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
            import hou
            cameras = self._find_scene_cameras()
            if not cameras:
                QMessageBox.information(self, "提示", "当前场景中没有找到相机。")
                return

            cam_names = [c.split("/")[-1] for c in cameras]
            current = self._camera_edit.text()
            current_idx = cameras.index(current) if current in cameras else 0

            name, ok = QInputDialog.getItem(
                self, "选择相机", "场景中的相机:",
                cam_names, current_idx, False,
            )
            if ok and name:
                idx = cam_names.index(name)
                self._camera_edit.setText(cameras[idx])
        except ImportError:
            QMessageBox.warning(self, "提示", "此功能仅在 Houdini 中可用。")

    def _reset_info_labels(self):
        for lbl in self._info_labels.values():
            lbl.setText("-")

    # ── Lifecycle ────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._probe_worker and self._probe_worker.isRunning():
            self._probe_worker.cancel()
            self._probe_worker.wait(2000)
        if self._extract_worker and self._extract_worker.isRunning():
            self._extract_worker.cancel()
            self._extract_worker.wait(5000)
        super().closeEvent(event)


# ─── Entry Point ─────────────────────────────────────────────────────

def run(kwargs):
    """工具入口函数（由 shelf 调用）"""
    show_video_to_sequence_window()
