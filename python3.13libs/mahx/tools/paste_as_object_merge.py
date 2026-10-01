"""粘贴为 Object Merge（Ctrl+Shift+V）
=================================
在网络编辑器中 Ctrl+C 复制节点后，按 Ctrl+Shift+V 在鼠标位置创建
引用节点。行为对齐 OD 工具集的 ``pasteNodesAsObjectMerge``（反汇编
其 shelftools.pyc 还原），按目标网络的上下文分派：

  SOP 网络    VopNode → ``material``（shop_materialpath1）；
              其它 → ``object_merge``（objpath1，同网络用相对路径）
  VOP 网络    redshift_vopnet / rs_usd_material_builder → redshift::shaderMerge；
              octane_vopnet → octane::ShaderMerge；其它 → 询问后 copyItems 通道引用
  OBJ 网络    源全是 ObjNode → 单个 geo 容器内 N 个 object_merge + merge 汇总
              （display/render flag + layoutChildren）；否则每个源一个 geo 容器
  LOP 网络    sopimport（soppath）
  TOP 网络    源 TOP → topfetch；源 SOP → geometryimport
  COP2 网络   源 COP2 → fetch（oppath）；源 SOP → sopimport
  DOP 网络    询问 Sop Geo / Static Object → sopgeo / staticobject（soppath）

每个源节点生成一个引用节点，横向偏移 n*3 排开，继承源节点颜色；首个
新节点带 clear_all_selected 选中。整次操作包在 ``hou.undos.group`` 里，
占用单个 undo 槽。未覆盖的上下文静默不动（OD 同款）。

源节点解析分两层（我们自己的稳定性增强，OD 只读文本层）：
  1. OS 剪贴板文本 —— Houdini Ctrl+C 时自写的节点路径（引用语义必须
     有"原件路径"，内部节点剪贴板无法反查原件）。
  2. Houdini 内部节点剪贴板兜底（仅 SOP 网络）—— 文本层失效时用
     ``hou.Node.pasteItemsFromClipboard`` 真实粘贴出副本，再引用副本：
     内部剪贴板不受其它程序复制覆盖，还能救回"原件已被删除"的场景。

入口 ``run()`` 由 NetworkViewMenu.xml 的 scriptItem（含菜单点击与热键
触发）经 ``mahx.dev.dispatcher`` 调用；默认键位由 ``python3.13libs/uiready.py``
在会话启动时通过 ``hou.hotkeys.addAssignment`` 分配。
"""

import re

from mahx.core.log import get_logger

logger = get_logger("net.paste_merge")

# Houdini 网络视图拖放/剪贴板共享的节点路径 mime 格式
# （见 houpythonportion/qt/__init__.py 中的格式清单）
_MIME_NODE_PATH = "application/sidefx-houdini-node.path"

_HOU_NODE_RE = re.compile(
    r"""hou\s*\.\s*node\s*\(\s*['"](.+?)['"]\s*\)\s*$"""
)


def run(**kwargs):
    """按目标网络上下文创建引用节点（OD pasteNodesAsObjectMerge 对齐）。"""
    import hou

    editor = _target_editor(kwargs)
    if editor is None:
        _status("未找到网络编辑器，无法粘贴")
        return

    context = editor.pwd()
    context_type = context.type().childTypeCategory()
    position = _paste_position(editor)
    try:
        with hou.undos.group("pasteAsObjectMerge"):
            _paste_as_merge(context, context_type, position)
    except Exception as e:
        _status(f"粘贴失败：{e}")
        logger.warning("paste_as_object_merge failed in %s: %s", context.path(), e)


def _paste_as_merge(context, context_type, position):
    """上下文分派主流程，结构逐分支对齐 OD shelftools.pasteNodesAsObjectMerge。"""
    import hou

    src_items = _source_items(context, context_type, position)
    if not src_items:
        _status("剪贴板中没有可粘贴的节点（先在网络编辑器 Ctrl+C 复制节点）")
        return

    n = 0
    if context_type == hou.sopNodeTypeCategory():
        for item in src_items:
            src = hou.node(item)
            if src is None:
                continue
            color = src.color()
            basename = item.rsplit("/", 1)[-1]
            if type(src) == hou.VopNode:
                # 粘贴 shader 进 SOP 网络 → material 节点引用材质
                merge = context.createNode("material", "merge_" + basename)
                merge.parm("shop_materialpath1").set(str(item))
            else:
                merge = context.createNode("object_merge", "merge_" + basename)
                merge.parm("objpath1").set(_objpath_for(merge, item))
            _place(merge, position, n, color)
            merge.setSelected(True, clear_all_selected=(n == 0))
            n += 1

    elif context_type == hou.vopNodeTypeCategory():
        net_name = context.type().name()
        if net_name in ("redshift_vopnet", "rs_usd_material_builder"):
            for item in src_items:
                src = hou.node(item)
                if src is None or type(src) != hou.VopNode:
                    continue
                merge = context.createNode(
                    "redshift::shaderMerge", "merge_" + item.rsplit("/", 1)[-1])
                merge.parm("RS_vopPath").set(str(item))
                _place(merge, position, n, src.color())
                n += 1
        elif net_name == "octane_vopnet":
            for item in src_items:
                src = hou.node(item)
                if src is None or type(src) != hou.VopNode:
                    continue
                merge = context.createNode(
                    "octane::ShaderMerge", "merge_" + item.rsplit("/", 1)[-1])
                merge.parm("Octane_shaderPath").set(str(item))
                _place(merge, position, n, src.color())
                n += 1
        else:
            # 通用 VOP 网：询问绝对/相对，用 copyItems 建通道引用副本
            res = hou.ui.displayMessage(
                "Reference Type?",
                buttons=("Absolute", "Relative", "Cancel"),
                default_choice=1,
            )
            if res == 2:
                return
            for item in src_items:
                src = hou.node(item)
                if src is None or type(src) != hou.VopNode:
                    continue
                context.copyItems(
                    [src],
                    channel_reference_originals=True,
                    relative_references=(res == 1),
                )

    elif context_type == hou.objNodeTypeCategory():
        _paste_into_obj(context, src_items, position)

    elif context_type == hou.lopNodeTypeCategory():
        for item in src_items:
            src = hou.node(item)
            if src is None:
                continue
            merge = context.createNode(
                "sopimport", "import_" + item.rsplit("/", 1)[-1])
            merge.parm("soppath").set(str(item))
            _place(merge, position, n, src.color())
            n += 1

    elif context_type == hou.topNodeTypeCategory():
        for item in src_items:
            src = hou.node(item)
            if src is None:
                continue
            merge = None
            if src.type().category() == hou.topNodeTypeCategory():
                merge = context.createNode(
                    "topfetch", "fetch_" + item.rsplit("/", 1)[-1])
                merge.parm("toppath").set(str(item))
            elif src.type().category() == hou.sopNodeTypeCategory():
                merge = context.createNode(
                    "geometryimport", "import_" + item.rsplit("/", 1)[-1])
                merge.parm("geometrysource").set(0)
                merge.parm("soppath").set(str(item))
            if merge is not None:
                _place(merge, position, n, src.color())
                n += 1

    elif context_type in (hou.cop2NodeTypeCategory(), hou.cop2NetNodeTypeCategory()):
        for item in src_items:
            src = hou.node(item)
            if src is None:
                continue
            merge = None
            if src.type().category() == hou.cop2NodeTypeCategory():
                merge = context.createNode(
                    "fetch", "fetch_" + item.rsplit("/", 1)[-1])
                merge.parm("oppath").set(str(item))
            elif src.type().category() == hou.sopNodeTypeCategory():
                merge = context.createNode(
                    "sopimport", "import_" + item.rsplit("/", 1)[-1])
                merge.parm("soppath").set(str(item))
            if merge is not None:
                _place(merge, position, n, src.color())
                n += 1

    elif context_type == hou.dopNodeTypeCategory():
        for item in src_items:
            src = hou.node(item)
            if src is None:
                continue
            color = src.color()
            res = hou.ui.displayMessage(
                "Type of Node.",
                buttons=("Sop Geo", "Static Object", "Cancel"),
                default_choice=1,
            )
            if res == 2:
                return
            merge = None
            if res == 0:
                merge = context.createNode(
                    "sopgeo", "import_" + item.rsplit("/", 1)[-1])
            elif res == 1:
                merge = context.createNode(
                    "staticobject", "import_" + item.rsplit("/", 1)[-1])
            if merge is not None:
                merge.parm("soppath").set(str(item))
                _place(merge, position, n, color)
                n += 1

    else:
        return  # 未覆盖的上下文：静默不动（OD 同款）

    _status(f"已粘贴 → {context.path()}"
            f"（{src_items[0].rsplit('/', 1)[-1]} 等 {len(src_items)} 项）")


def _paste_into_obj(context, src_items, position):
    """OBJ 网络分支：源全是 ObjNode 时收敛进单个 geo 容器，否则每源一容器。"""
    new_nodes = []

    if type(hou.node(src_items[0])) == hou.ObjNode:
        geo = context.createNode(
            "geo", "merge_" + src_items[0].rsplit("/", 1)[-1])
        new_nodes.append(geo)
        geo.setPosition(position)
        merged = []
        for item in src_items:
            src = hou.node(item)
            if src is None:
                continue
            color = src.color()
            geo.setColor(color)
            merge = geo.createNode(
                "object_merge", "merge_" + item.rsplit("/", 1)[-1])
            merge.parm("objpath1").set(_objpath_for(merge, item))
            merge.setColor(color)
            merged.append(merge)
        sink = geo.createNode("merge")
        for idx, merge in enumerate(merged):
            sink.setInput(idx, merge)
        sink.setDisplayFlag(True)
        sink.setRenderFlag(True)
        geo.layoutChildren()
    else:
        # 非 OBJ 源（如 SOP）粘到 /obj：每个源一个 geo 容器
        for n, item in enumerate(src_items):
            src = hou.node(item)
            if src is None:
                continue
            color = src.color()
            geo = context.createNode(
                "geo", "merge_" + item.rsplit("/", 1)[-1])
            new_nodes.append(geo)
            geo.setColor(color)
            _place(geo, position, n)
            merge = geo.createNode(
                "object_merge", "merge_" + item.rsplit("/", 1)[-1])
            merge.parm("objpath1").set(_objpath_for(merge, item))
            merge.setColor(color)

    if new_nodes:
        new_nodes[0].setSelected(True, clear_all_selected=True)
        for node in new_nodes[1:]:
            node.setSelected(True, clear_all_selected=False)


# ── 源解析 ────────────────────────────────────────────────────

def _source_items(context, context_type, position) -> list[str]:
    """返回剪贴板源节点路径列表（已验证存在）。

    文本层失效时，在 SOP 网络内用 Houdini 内部节点剪贴板真实粘贴兜底，
    引用粘贴出的副本。
    """
    import hou

    paths = _clipboard_node_paths()
    if paths:
        return paths

    if context_type == hou.sopNodeTypeCategory():
        pasted = _paste_internal_clipboard(context, position)
        if pasted:
            _status("剪贴板文本失效，已从 Houdini 内部剪贴板粘贴副本")
            return [node.path() for node in pasted]
    return []


def _clipboard_node_paths() -> list[str]:
    """从 OS 剪贴板提取被复制节点路径，返回验证存在的路径列表。"""
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


def _paste_internal_clipboard(parent, position) -> list:
    """用 Houdini 内部节点剪贴板真实粘贴，返回粘贴出的节点列表。

    ``pasteItemsFromClipboard`` 可能粘贴 network box 等非节点 item，
    用粘贴前后的 children 差集只取节点。
    """
    before = set(parent.children())
    try:
        parent.pasteItemsFromClipboard(position)
    except Exception as e:
        logger.debug("internal node clipboard paste failed: %s", e)
        return []
    return [child for child in parent.children() if child not in before]


# ── 通用 helper ───────────────────────────────────────────────

def _objpath_for(merge, src_path: str) -> str:
    """merge 与源同网络时用相对路径（OD 对齐），否则保持绝对路径。"""
    import hou
    src = hou.node(src_path)
    if src is not None and merge.parent() == src.parent():
        return merge.relativePathTo(src)
    return src_path


def _place(node, position, n, color=None):
    """放在鼠标位置并按序号横向偏移 n*3，继承源节点颜色。"""
    node.setPosition(position)
    node.move([n * 3.0, 0])
    if color is not None:
        node.setColor(color)


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


def _status(message: str):
    """状态栏提示（不弹窗、不打控制台，保持操作流不被打断）。"""
    try:
        import hou
        hou.ui.setStatusMessage(message, hou.severityType.ImportantMessage)
    except Exception:
        pass
