"""Open the output folder of cache/render nodes (node right-click menu item).

The OPmenu.xml at the repo root (loaded via HOUDINI_PATH) hangs "Open Cache Folder"
on nodes carrying any of the output-path parameters in _OUTPUT_PARMS; the menu
scriptCode is dispatched via dev.dispatcher, landing at this module's run(kwargs).
"""

import os

from houtools.core.log import get_logger

log = get_logger("tools.open_cache_folder")

# 输出路径参数候选（与 OPmenu.xml 的 context 表达式保持同步；实测各节点参数名
# 互不重叠，通常命中其一）：filecache::2.0 / rop geometry / fbx 的 sopoutput，
# mantra 的 vm_picture，karma 的 picture，USD Render 的 outputimage
_OUTPUT_PARMS = ("sopoutput", "vm_picture", "picture", "outputimage")


def _pick_output_path(node):
    """按候选序取节点的输出路径字符串。

    USD Render 特例：outputimage 为空（未设置输出路径）时回退读 loppath 指向的
    LOP 节点（一般为 lop/Karma Render Settings）的 picture 参数；outputimage
    非空时永远以 outputimage 为准、不再看 loppath。
    其余候选参数存在但值为空视为配置错误，直接报错（与旧版 sopoutput 行为一致）。
    Raises RuntimeError when no usable output path is found.
    """
    where = node.path()
    for name in _OUTPUT_PARMS:
        parm = node.parm(name)
        if parm is None:
            continue
        path = parm.evalAsString().strip()
        if name == "outputimage" and not path:
            lop_parm = node.parm("loppath")
            if lop_parm is None:
                raise RuntimeError(
                    "节点 {} 的 outputimage 为空且没有 loppath 参数".format(where))
            target = lop_parm.evalAsNode()
            if target is None:
                raise RuntimeError(
                    "节点 {} 的 outputimage 为空且 loppath 没有指向有效节点".format(where))
            pic = target.parm("picture")
            if pic is None:
                raise RuntimeError(
                    "节点 {} 的 outputimage 为空，loppath 指向的节点 {} 没有 picture 参数"
                    .format(where, target.path()))
            path = pic.evalAsString().strip()
            if not path:
                raise RuntimeError(
                    "节点 {} 的 outputimage 为空，loppath 指向的节点 {} 的 picture 也为空"
                    .format(where, target.path()))
        elif not path:
            raise RuntimeError("节点 {} 的 {} 为空".format(where, name))
        return path
    raise RuntimeError("节点 {} 没有输出路径参数（{}）".format(
        where, "/".join(_OUTPUT_PARMS)))


def resolve_folder(node):
    """Take the evaluated output path and return the folder to open.

    Normally returns the folder containing the output file; if that folder does not
    exist (cache not written out yet), walks up to the nearest existing ancestor
    directory (drive root always exists, so this always returns).
    Raises RuntimeError when the node has no usable output path.
    """
    path = _pick_output_path(node)
    if "$" in path:
        # 参数值是表达式：先整体展开（$HIP/$OS/$F4 等），否则 "$HIP/..." 形态
        # 会被 isabs 判否、再 join 出 "工程/$HIP/..." 的错路径（渲染参数默认值
        # 几乎全是该形态）。帧号等变量被展开成具体值不影响取目录。
        import hou
        path = hou.text.expandString(path)
    if not os.path.isabs(path):
        # 相对路径按 $HIP 解析，与 Houdini 读文件参数的语义一致
        import hou
        path = os.path.join(hou.text.expandString("$HIP"), path)
    folder = os.path.dirname(os.path.normpath(path))
    while not os.path.isdir(folder):
        parent = os.path.dirname(folder)
        if parent == folder:  # 已到盘根
            break
        folder = parent
    return folder


def run(kwargs=None):
    """OPmenu 菜单入口；kwargs 为菜单脚本上下文（含 node）。"""
    import hou

    node = (kwargs or {}).get("node")
    if node is None:
        return
    try:
        folder = resolve_folder(node)
    except RuntimeError as exc:
        log.warning("%s", exc)
        hou.ui.setStatusMessage(
            "HouTools: {}".format(exc), severity=hou.severityType.Error
        )
        return
    os.startfile(folder)
    hou.ui.setStatusMessage("HouTools: 已打开 {}".format(folder))
