"""Paste Absolute Values [HT] 菜单入口（薄分发）。

OPmenu.xml「Paste Bounding Box [HT]」子菜单的双模式子项之一：把剪贴板
源节点的包围盒尺寸/中心求值后写入右键目标节点的 size / t。逻辑全在
paste_bbox.py，此处 run() 内懒 import（同 delete_viewport_capture 手法），
reloader 的插入序依赖（先被 import 的是依赖）不被打破。
"""


def run(kwargs=None):
    from houtools.tools import paste_bbox
    paste_bbox.run_absolute(kwargs)
