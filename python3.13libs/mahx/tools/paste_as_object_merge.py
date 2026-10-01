"""粘贴为 Object Merge（Ctrl+Shift+V）
=================================
在网络编辑器中 Ctrl+C 复制节点后，按 Ctrl+Shift+V 在鼠标位置按
"目标上下文 × 源节点类别"创建引用节点（XXX 表示源节点名）：

  规则 1    SOP → SOP   object_merge ``Merge_XXX``（objpath1，同网络相对路径）
  规则 2    SOP → OBJ   geo ``XXX``，内含 object_merge ``Merge_XXX``
  规则 3    SOP → LOP   sopimport ``SOP_XXX``（soppath）
  规则 4    LOP → ROP   usdrender ``XXX``（loppath）
  规则 5    LOP → SOP   lopimport ``LOP_XXX``（loppath）
  规则 6    SOP → DOP   staticobject ``Object_XXX``（soppath）
  规则 7    LOP → LOP   fetch ``LOP_XXX``（loppath）
  规则 8    SOP → ROP   fetch ``SOP_XXX``（source）
  规则 9    SOP → COP   sopimport ``SOP_XXX``（soppath；仅新 COP，且需置 usesoppath=1）
  规则 10   OBJ → LOP   sopimport ``SOP_XXX``（soppath，可填 obj 路径）

前置规则：粘贴出的节点颜色与源节点一致（规则 2 的 geo 容器同样着色）。
未列出的"目标 × 源"组合一律跳过，未覆盖的上下文静默不动。每个引用节点
横向偏移 n*3 排开；首个新节点带 clear_all_selected 选中。整次操作包在
``hou.undos.group`` 里，占用单个 undo 槽。

源节点解析分两层（稳定性增强）：
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
    """按"目标上下文 × 源节点类别"创建引用节点（规则 1-10）。"""
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
    """上下文分派主流程：规则 1-10 覆盖的上下文，其余一律静默不动。"""
    import hou

    if context_type == hou.sopNodeTypeCategory():
        _paste_into_sop(context, position)
    elif context_type == hou.objNodeTypeCategory():
        _paste_into_obj(context, position)
    else:
        rules = _reference_rules().get(context_type)
        if rules is not None:
            _paste_reference_nodes(context, position, rules)


def _reference_rules() -> dict:
    """目标网络类别 → {源节点类别: (节点类型, 路径参数, 名称前缀, 附加参数)}。

    覆盖命名规则 3/4/6/7/8/9/10；规则 1/2 在 _paste_into_sop /
    _paste_into_obj 里单独处理（SOP 网络有内部剪贴板兜底、OBJ 网络要建
    geo 容器）。名称前缀为空串表示直接用源节点名；附加参数在路径参数
    之前设置。
    """
    import hou

    sop = hou.sopNodeTypeCategory()
    lop = hou.lopNodeTypeCategory()
    rules = {
        # 规则 3 SOP→LOP / 规则 10 OBJ→LOP / 规则 7 LOP→LOP
        lop: {
            sop: ("sopimport", "soppath", "SOP_", {}),
            hou.objNodeTypeCategory(): ("sopimport", "soppath", "SOP_", {}),
            lop: ("fetch", "loppath", "LOP_", {}),
        },
        # 规则 4 LOP→ROP / 规则 8 SOP→ROP
        hou.ropNodeTypeCategory(): {
            lop: ("usdrender", "loppath", "", {}),  # /out 的 USD Render ROP 内部名（usdrender_rop 是 LOP 内版本）
            sop: ("fetch", "source", "SOP_", {}),
        },
        # 规则 6 SOP→DOP
        hou.dopNodeTypeCategory(): {
            sop: ("staticobject", "soppath", "Object_", {}),
        },
        # 规则 9 SOP→COP：仅新 COP（copnet）；sopimport 的 usesoppath 默认 0，须先置 1
        hou.copNodeTypeCategory(): {
            sop: ("sopimport", "soppath", "SOP_", {"usesoppath": 1}),
        },
    }
    return rules


def _paste_into_sop(context, position):
    """SOP 网络：SOP 源 → object_merge（规则 1），LOP 源 → lopimport（规则 5）。"""
    import hou

    src_items = _source_items(context, position)
    if not src_items:
        _status("剪贴板中没有可粘贴的节点（先在网络编辑器 Ctrl+C 复制节点）")
        return

    n = 0
    for item in src_items:
        src = hou.node(item)
        if src is None:
            continue
        basename = item.rsplit("/", 1)[-1]
        if src.type().category() == hou.lopNodeTypeCategory():
            merge = context.createNode("lopimport", "LOP_" + basename)
            merge.parm("loppath").set(str(item))
        elif src.type().category() == hou.sopNodeTypeCategory():
            merge = context.createNode("object_merge", "Merge_" + basename)
            merge.parm("objpath1").set(_objpath_for(merge, item))
        else:
            continue  # 未定义的"目标 × 源"组合，跳过
        _place(merge, position, n, src.color())
        merge.setSelected(True, clear_all_selected=(n == 0))
        n += 1
    _report(context.path(), src_items, n)


def _paste_into_obj(context, position):
    """OBJ 网络（规则 2）：SOP 源 → 每源一个 geo ``XXX``，内含 ``Merge_XXX``。"""
    import hou

    src_items = _clipboard_node_paths()
    if not src_items:
        _status("剪贴板中没有可粘贴的节点（先在网络编辑器 Ctrl+C 复制节点）")
        return

    n = 0
    for item in src_items:
        src = hou.node(item)
        if src is None or src.type().category() != hou.sopNodeTypeCategory():
            continue
        basename = item.rsplit("/", 1)[-1]
        color = src.color()
        geo = context.createNode("geo", basename)
        _place(geo, position, n, color)
        merge = geo.createNode("object_merge", "Merge_" + basename)
        merge.parm("objpath1").set(_objpath_for(merge, item))
        merge.setColor(color)
        geo.setSelected(True, clear_all_selected=(n == 0))
        n += 1
    _report(context.path(), src_items, n)


def _paste_reference_nodes(context, position, rules):
    """通用引用粘贴（命名规则 3/4/6/7/8/9/10）：按源类别查表创建引用节点。"""
    import hou

    src_items = _clipboard_node_paths()
    if not src_items:
        _status("剪贴板中没有可粘贴的节点（先在网络编辑器 Ctrl+C 复制节点）")
        return

    n = 0
    for item in src_items:
        src = hou.node(item)
        if src is None:
            continue
        rule = rules.get(src.type().category())
        if rule is None:
            continue  # 未定义的"目标 × 源"组合，跳过
        node_type, parm_name, prefix, extra_parms = rule
        node = context.createNode(node_type, prefix + item.rsplit("/", 1)[-1])
        for extra_name, extra_value in extra_parms.items():
            node.parm(extra_name).set(extra_value)
        node.parm(parm_name).set(str(item))
        _place(node, position, n, src.color())
        node.setSelected(True, clear_all_selected=(n == 0))
        n += 1
    _report(context.path(), src_items, n)


# ── 源解析 ────────────────────────────────────────────────────

def _source_items(context, position) -> list[str]:
    """返回剪贴板源节点路径列表（已验证存在）。

    文本层失效时，在目标网络内用 Houdini 内部节点剪贴板真实粘贴兜底，
    引用粘贴出的副本。
    """
    paths = _clipboard_node_paths()
    if paths:
        return paths

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
    """merge 与源同网络时用相对路径，否则保持绝对路径。"""
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


def _report(target_path, src_items, n):
    """统一的完成状态提示；n=0 表示没有任何源匹配当前上下文的规则。"""
    if n:
        _status(f"已粘贴 → {target_path}"
                f"（{src_items[0].rsplit('/', 1)[-1]} 等 {n} 项）")
    else:
        _status("剪贴板节点类型在当前上下文没有对应的粘贴规则")


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
