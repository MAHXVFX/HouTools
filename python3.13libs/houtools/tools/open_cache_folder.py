"""Open the output folder of a File Cache node (node right-click menu item).

The OPmenu.xml at the repo root (loaded via HOUDINI_PATH) hangs "Open Cache Folder"
on nodes with the sopoutput parameter; the menu scriptCode is dispatched via
dev.dispatcher, landing at this module's run(kwargs).
"""

import os

from houtools.core.log import get_logger

log = get_logger("tools.open_cache_folder")


def resolve_folder(node):
    """Take the evaluated sopoutput value and return the folder to open.

    Normally returns the folder containing the cache file; if that folder does not
    exist (cache not written out yet), walks up to the nearest existing ancestor
    directory (drive root always exists, so this always returns).
    Raises RuntimeError when the node has no sopoutput parameter or its value is empty.
    """
    parm = node.parm("sopoutput")
    if parm is None:
        raise RuntimeError("节点 {} 没有 sopoutput 参数".format(node.path()))
    path = parm.evalAsString().strip()
    if not path:
        raise RuntimeError("节点 {} 的 sopoutput 为空".format(node.path()))
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
