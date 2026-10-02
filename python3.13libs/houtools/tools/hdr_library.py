"""Hdr Library - HDR 环境贴图浏览器工具入口。

浏览 HDR 库（缩略图网格），选中 envlight 等灯光节点后双击缩略图，
把 HDR 路径写入灯光的环境贴图参数。HDR 素材库不入库，路径在
窗口内"更换目录"指定（存项目 settings/）。
"""


def run():
    from houtools.hdrlight.browser import show_hdr_library
    show_hdr_library()
