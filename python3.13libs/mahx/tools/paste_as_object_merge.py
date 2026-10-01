"""粘贴为 Object Merge（Ctrl+Shift+V）
=================================
在网络编辑器中 Ctrl+C 复制节点后，按 Ctrl+Shift+V 在鼠标位置创建
``object_merge`` 节点，并把被复制节点的路径填进 ``objpath``。

入口 ``run()`` 由 NetworkViewMenu.xml 的 scriptItem（含菜单点击与热键
触发）经 ``mahx.dev.dispatcher`` 调用；默认键位由 ``scripts/123.py``
在会话启动时通过 ``hou.hotkeys.addAssignment`` 分配。

源节点路径的解析（按序探测，全部走系统剪贴板）：
  1. Houdini 拖放/跨面板共享的 mime 格式
     ``application/sidefx-houdini-node.path``
  2. 纯文本剪贴板 —— 节点路径文本，或参数框拖出的
     ``hou.node('/obj/foo')`` 包装表达式
所有候选都经 ``hou.node()`` 验证存在后才采用。
"""

import re

from mahx.core.log import get_logger

logger = get_logger("net.paste_merge")

# Houdini 网络视图拖放/剪贴板共享的节点路径 mime 格式
# （见 houpythonportion/qt/__init__.py 中的格式清单）
_MIME_NODE_PATH = "application/sidefx-houdini-node.path"

_HOU_NODE_RE = re.compile(
    r"""hou\s*\.\s*node\s*\(\s*['"](.+?)['"]\s*\)"""
)


def run(**kwargs):
    """创建 object_merge 并指向剪贴板中的节点。

    kwargs（来自菜单/热键触发，均可缺省）:
        networkeditor: 触发时所在的 hou.NetworkEditor
    """
    import hou

    src_paths = _clipboard_node_paths()
    if not src_paths:
        _status("剪贴板中没有可用的节点路径（先在网络编辑器 Ctrl+C 复制节点）")
        return

    editor = _target_editor(kwargs)
    if editor is None:
        _status("未找到网络编辑器，无法粘贴 Object Merge")
        return

    parent = editor.pwd()
    position = _paste_position(editor)
    try:
        merge = _create_merge(parent, src_paths, position)
    except Exception as e:
        _status(f"创建 Object Merge 失败（当前网络可能不支持 SOP 节点）：{e}")
        logger.warning("createNode object_merge failed in %s: %s", parent.path(), e)
        return

    merge.setSelected(True, clear_all_selected=True)
    _status(f"已粘贴 Object Merge → {merge.path()}（{', '.join(src_paths)}）")


# ── 剪贴板解析 ────────────────────────────────────────────────

def _clipboard_node_paths() -> list[str]:
    """从系统剪贴板提取被复制节点路径，返回验证存在的路径列表。"""
    import hou

    paths: list[str] = []

    try:
        from PySide6.QtWidgets import QApplication
        mime = QApplication.clipboard().mimeData()
        if mime is not None and mime.hasFormat(_MIME_NODE_PATH):
            raw = bytes(mime.data(_MIME_NODE_PATH)).decode("utf-8", "replace")
            for token in raw.replace("\r", " ").replace("\n", " ").split():
                token = token.strip()
                if token and hou.node(token) is not None:
                    paths.append(token)
    except Exception as e:
        logger.debug("mime clipboard probe failed: %s", e)

    if paths:
        return paths

    try:
        text = hou.ui.getTextFromClipboard().strip()
        if text:
            m = _HOU_NODE_RE.match(text)
            if m:
                text = m.group(1)
            for token in text.split():
                if token.startswith("/") and hou.node(token) is not None:
                    paths.append(token)
    except Exception as e:
        logger.debug("text clipboard probe failed: %s", e)

    return paths


def _target_editor(kwargs: dict):
    """确定粘贴目标网络编辑器：kwargs > 鼠标下方 > 任意一个。"""
    import hou

    editor = (kwargs or {}).get("networkeditor")
    if editor is not None:
        return editor

    try:
        under = hou.ui.paneTabUnderCursor()
        if under is not None and under.type() == hou.paneTabType.NetworkEditor:
            return under
    except Exception:
        pass

    return hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)


def _paste_position(editor):
    """返回鼠标当前所在的网络坐标（限制在可视范围内）。"""
    try:
        return editor.cursorPosition()
    except Exception:
        bounds = editor.visibleBounds()
        return ((bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0)


def _create_merge(parent, src_paths: list[str], position):
    """在 parent 下创建唯一命名的 object_merge 并填写 objpath。

    节点类型内部名是 ``object_merge``（带下划线，H16 起的现代 Object
    Merge SOP），不是旧版的 ``objectmerge``。对象路径是 multiparm：
    ``numobj`` 为数量，路径参数为 ``objpath1`` / ``objpath2`` / ...
    （与官方 crowdtoolutils/dopclothproxy 的写法一致）。
    """
    first = src_paths[0].rsplit("/", 1)[-1]
    existing = {child.name() for child in parent.children()}
    name = f"merge_{first}"
    counter = 1
    while name in existing:
        counter += 1
        name = f"merge_{first}{counter}"

    merge = parent.createNode("object_merge", node_name=name)
    merge.setPosition(position)
    if len(src_paths) > 1:
        merge.parm("numobj").set(len(src_paths))
    for idx, path in enumerate(src_paths, start=1):
        merge.parm(f"objpath{idx}").set(path)
    return merge


def _status(message: str):
    """状态栏提示（不弹窗、不打控制台，保持操作流不被打断）。"""
    try:
        import hou
        hou.ui.setStatusMessage(message, hou.severityType.ImportantMessage)
    except Exception:
        pass
