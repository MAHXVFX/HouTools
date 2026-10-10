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

from houtools.core.constants import PROJECT_ROOT


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
    """从 ffmpeg 路径推导同目录 ffprobe 路径（兼容 hffmpeg -> hffprobe）。

    同目录没有时回退借用 ``$HFS/bin`` 与 PATH 上的 hffprobe/ffprobe
    （hffprobe 优先，与 find_ffmpeg 的 h 优先惯例一致）——例如项目根只放
    ffmpeg.exe 的场景：探测读的是容器元数据，对构建版本不敏感，借用
    hffprobe 比退回"正则解析 ffmpeg -i 输出"更准。
    """
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

    if os.name == "nt":
        probe_names = ("hffprobe.exe", "ffprobe.exe")
    else:
        probe_names = ("hffprobe", "ffprobe")
    hfs = os.environ.get("HFS", "")
    if hfs:
        for name in probe_names:
            candidate = os.path.join(hfs, "bin", name)
            if os.path.exists(candidate):
                return candidate
    for name in probe_names:
        found = shutil.which(name)
        if found:
            return found
    return None


def get_startup_kwargs() -> dict:
    """Windows 下隐藏 ffmpeg 子进程的控制台窗口。"""
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs
