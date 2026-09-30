"""ffmpeg / ffprobe 查找与子进程辅助。

查找优先级：
1. 项目根目录的 ``ffmpeg.exe``（可选手动放置，.gitignore 已排除，不入库）
2. ``$HFS/bin/hffmpeg`` —— Houdini 自带，正常情况无需单独安装 ffmpeg
3. ``$HFS/bin/ffmpeg``
4. 系统 PATH 上的 ``hffmpeg`` / ``ffmpeg``
"""

import os
import shutil
import subprocess
from typing import Optional

from mahx.core.constants import PROJECT_ROOT


def find_ffmpeg() -> Optional[str]:
    """定位可用的 ffmpeg 可执行文件，找不到返回 None。"""
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    hexe = "hffmpeg.exe" if os.name == "nt" else "hffmpeg"

    bundled = PROJECT_ROOT / exe
    if bundled.exists():
        return str(bundled)

    hfs = os.environ.get("HFS", "")
    if hfs:
        for name in (hexe, exe):
            candidate = os.path.join(hfs, "bin", name)
            if os.path.exists(candidate):
                return candidate

    for name in (hexe, exe):
        found = shutil.which(name)
        if found:
            return found
    return None


def find_ffprobe(ffmpeg_path: str) -> Optional[str]:
    """从 ffmpeg 路径推导同目录 ffprobe 路径（兼容 hffmpeg -> hffprobe）。"""
    if not ffmpeg_path:
        return None
    directory = os.path.dirname(ffmpeg_path)
    basename = os.path.basename(ffmpeg_path)

    for name in ("ffprobe.exe", "ffprobe"):
        candidate = os.path.join(directory, name)
        if os.path.exists(candidate):
            return candidate

    lower = basename.lower()
    if lower.startswith("h") and "ffmpeg" in lower:
        probe_name = lower.replace("ffmpeg", "ffprobe")
        candidate = os.path.join(directory, probe_name)
        if os.path.exists(candidate):
            return candidate
    return None


def get_startup_kwargs() -> dict:
    """Windows 下隐藏 ffmpeg 子进程的控制台窗口。"""
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs
