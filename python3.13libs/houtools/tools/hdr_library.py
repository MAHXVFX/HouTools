"""Hdr Library - HDR 环境贴图浏览器工具入口。

浏览 HDR 库（缩略图网格），选中 envlight 等灯光节点后双击缩略图，
把 HDR 路径写入灯光的环境贴图参数。库按"总目录 / 一级分类子文件夹"
组织（递归扫描，侧栏按分类切换），支持右键收藏。HDR 素材库不入库，
路径在窗口内"更换目录"指定（存项目 settings/）。
"""


def run():
    from houtools.hdrlight.browser import show_hdr_library
    show_hdr_library()
