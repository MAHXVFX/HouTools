# HouTools: 官方 externaldragdrop 钩子分发器（逻辑在 houtools.dragdrop，
# 走 Reload 热加载）。Houdini 以 exec(<stdin>) 方式执行本文件：没有
# __file__，不要在此文件写任何逻辑。
import houtools.dragdrop


def dropAccept(file_list):
    return houtools.dragdrop.drop_accept(file_list)
