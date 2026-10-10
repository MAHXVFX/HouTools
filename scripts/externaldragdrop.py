# HouTools: 官方 externaldragdrop 钩子 —— 拖入 .abc 文件到 obj 层级网络编辑器，
# 在鼠标处创建 Alembic Archive 节点并构建层级（等价 File > Import > Alembic Scene...）。
# 注意：Houdini 以 exec(<stdin>) 方式执行本文件，没有 __file__ 变量，路径一律硬编码。
# 返回 True 表示接管本次拖放；False 表示交给 Houdini 原生处理。
import os
import re
import time
import traceback

import hou

_LOG = r"C:\Users\mahx_\Documents\houdini22.0\HouTools\settings\probe_drop.log"


def _log(msg):
    try:
        os.makedirs(os.path.dirname(_LOG), exist_ok=True)
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def _clean_name(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    name = re.sub(r"[^A-Za-z0-9._-]", "_", stem)
    name = re.sub(r"_+", "_", name).strip("_") or "abc_import"
    if not re.match(r"[A-Za-z]", name[0]):
        name = "abc_" + name
    return name


def dropAccept(file_list):
    _log("dropAccept called, file_list=%r" % (file_list,))
    try:
        abcs = [f for f in (file_list or [])
                if os.path.splitext(f)[1].lower() == ".abc"]
        if not abcs:
            _log("  no .abc -> False")
            return False
        pane = hou.ui.paneTabUnderCursor()
        _log("  pane=%r isinstance_neteditor=%r" % (pane, isinstance(pane, hou.NetworkEditor)))
        if not isinstance(pane, hou.NetworkEditor):
            _log("  not network editor -> False")
            return False
        pwd = pane.pwd()
        cat = pwd.childTypeCategory()
        _log("  pwd=%s childTypeCategory=%s" % (pwd.path(), cat.name()))
        if cat.name() != "Object":
            _log("  not Object level -> False")
            return False
        pos = pane.cursorPosition()
        _log("  cursorPos=%r" % (pos,))
        created = []
        with hou.undos.group("HT Probe Import ABC"):
            for i, f in enumerate(abcs):
                n = pwd.createNode("alembicarchive", node_name=_clean_name(f))
                w, h = n.size()
                n.setPosition(hou.Vector2(pos.x(), pos.y() - i * (h + 0.5)))
                n.parm("fileName").set(f)
                n.parm("buildHierarchy").pressButton()
                created.append((n.path(), len(n.children())))
                _log("  created %s <- %r children=%d" % (n.path(), f, len(n.children())))
        hou.ui.setStatusMessage(
            "HT probe: imported %d alembic archive(s)" % len(created))
        _log("  -> True (created=%r)" % (created,))
        return True
    except Exception:
        _log("EXCEPTION:\n" + traceback.format_exc())
        return False
