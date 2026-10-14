"""Paste Expressions [HT] 菜单入口（薄分发）。

OPmenu.xml「Paste Bounding Box [HT]」子菜单的双模式子项之一：把源节点
包围盒的 bbox()/centroid() 表达式写入右键目标节点的 size / t（同网络
相对路径、跨层级绝对路径）。逻辑全在 paste_bbox.py，此处 run() 内懒
import（同 delete_viewport_capture 手法），reloader 的插入序依赖
（先被 import 的是依赖）不被打破。
"""


def run(kwargs=None):
    from houtools.tools import paste_bbox
    paste_bbox.run_expressions(kwargs)
