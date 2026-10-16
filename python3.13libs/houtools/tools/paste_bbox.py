"""粘贴边界框（节点右键菜单，双模式子项）
=========================================
网络编辑器 Ctrl+C 复制源节点 A 后，右键目标节点 B →「Paste Bounding Box
[HT]」子菜单（OPmenu.xml；仅同时带三分量 size 与 t 向量参数的节点显示，
如 box）：把 A 的包围盒尺寸/中心粘贴到 B 的 size / t——

  sizex/y/z ← bbox(A, D_XSIZE/YSIZE/ZSIZE)
  tx/ty/tz  ← centroid(A, D_X/Y/Z)

  Paste Absolute Values [HT]   绝对值：求值后直接写入（B 上已有表达式/
                               关键帧清除替换，锁定参数跳过并提示）
  Paste Expressions [HT]       表达式：写入 bbox()/centroid() 表达式；
                               A 与 B 同网络用相对路径（../A），跨层级
                               用绝对路径

实现要点（H22.0.429 无头实测）：
  - 绝对值与表达式走同一套表达式函数求值（hou.hscriptExpression），两种
    模式结果天然一致（连"无几何节点 bbox 返 1"的退化情形都一致）。
  - parm.set() 不会覆盖已有表达式/关键帧——表达式存活并继续生效；写入前
    必须 deleteAllKeyframes()（对干净参数是无害 no-op）。setExpression
    以单表达式键帧整体替换旧表达式/关键帧；两模式统一先清再写。
  - cook 出错的源节点上 hscriptExpression 不抛异常、静默返 0——求值前用
    src.errors() 预检，有错不写。
  - 锁定参数 set/setExpression 抛 PermissionError——逐分量 isLocked 预检。

源节点解析复用 paste_as_object_merge 的 OS 剪贴板双层读取（Qt MIME →
文本剪贴板）；点击时才懒 import，避免 reloader 插入序上的旧引用（同
delete_viewport_capture 手法）。

OPmenu.xml 子菜单的 context expression 与 is_bbox_target 双处同步：必须
all()（两个元组都是三分量）而非 any()——grid 的 size 是二向量
（sizex/sizey），凭 t 是三分量而误命中不可接受。
"""

from houtools.core.log import get_logger

logger = get_logger("tools.paste_bbox")

_SIZE_PARM = "size"
_T_PARM = "t"

# (元组参数名, 分量下标, 表达式函数, 表达式轴常量)
_COMPONENT_SPECS = (
    (_SIZE_PARM, 0, "bbox", "D_XSIZE"),
    (_SIZE_PARM, 1, "bbox", "D_YSIZE"),
    (_SIZE_PARM, 2, "bbox", "D_ZSIZE"),
    (_T_PARM, 0, "centroid", "D_X"),
    (_T_PARM, 1, "centroid", "D_Y"),
    (_T_PARM, 2, "centroid", "D_Z"),
)

_UNDO_GROUP = "pasteBoundingBox"


def is_bbox_target(node):
    """目标节点判定：同时带三分量 size 与 t 向量参数。

    与 OPmenu.xml houtools_paste_bbox_menu 的 context expression 双处同步；
    不碰 hou，smoke test 用假节点直测本函数与 XML 表达式的同步一致性。
    """
    for name in (_SIZE_PARM, _T_PARM):
        tup = node.parmTuple(name)
        if tup is None or len(tup) != 3:
            return False
    return True


def run_absolute(kwargs=None):
    """Paste Absolute Values [HT] 菜单入口。"""
    _apply(kwargs, expression_mode=False)


def run_expressions(kwargs=None):
    """Paste Expressions [HT] 菜单入口。"""
    _apply(kwargs, expression_mode=True)


def _apply(kwargs, expression_mode):
    import hou

    target = (kwargs or {}).get("node")
    if target is None:
        return
    if not is_bbox_target(target):
        _status("目标节点须同时带三分量 size 与 t 向量参数", error=True)
        return

    src = _resolve_source()
    if src is None:
        return
    if src.path() == target.path():
        _status("源与目标是同一节点，未粘贴")
        return
    if not _source_ok(src):
        return

    path = src.path()
    if expression_mode and target.parent() == src.parent():
        path = target.relativePathTo(src)

    values = None
    if not expression_mode:
        try:
            values = _eval_source(src)
        except Exception as exc:
            logger.warning("bbox eval failed on %s: %s", src.path(), exc)
            _status("无法求值源节点包围盒：{}".format(exc), error=True)
            return

    skipped = []
    try:
        with hou.undos.group(_UNDO_GROUP):
            for tuple_name, index, func, axis in _COMPONENT_SPECS:
                comp = target.parmTuple(tuple_name)[index]
                if comp.isLocked():
                    skipped.append(comp.name())
                    continue
                try:
                    comp.deleteAllKeyframes()
                    if expression_mode:
                        comp.setExpression(
                            '{}("{}", {})'.format(func, path, axis),
                            hou.exprLanguage.Hscript)
                    else:
                        comp.set(values[(tuple_name, index)])
                except hou.PermissionError:
                    # 节点级/定义级锁定时 isLocked() 预检不到（那查的是
                    # 参数级锁）：按跳过处理并如实汇报，循环继续——中途
                    # 抛出会留下"size 写了一半 t 没写"的半应用状态
                    skipped.append(comp.name())
    except Exception as exc:
        logger.warning("paste_bbox failed on %s: %s", target.path(), exc)
        _status("粘贴边界框失败：{}".format(exc), error=True)
        return

    message = "已将 {} 的边界框粘贴到 {}（{}）".format(
        src.name(), target.name(),
        "表达式" if expression_mode else "绝对值")
    if skipped:
        message += "；已跳过锁定参数 " + "、".join(skipped)
    _status(message)


def _resolve_source():
    """从 OS 剪贴板解析源节点（复用 paste_as_object_merge 的双层读取）。

    懒 import：点击时才加载，保证拿到 reloader 重载后的最新实现。
    """
    import hou
    from houtools.tools.paste_as_object_merge import _clipboard_node_paths

    paths = _clipboard_node_paths()
    if not paths:
        _status("剪贴板中没有可用的源节点（先在网络编辑器 Ctrl+C 复制节点）")
        return None
    src = hou.node(paths[0])
    if src is None:
        _status("剪贴板源节点已不存在（{}）".format(paths[0]))
    return src


def _source_ok(src):
    """源节点可求值性预检：cook 出错时 hscriptExpression 静默返 0，先挡下。"""
    try:
        errors = src.errors()
    except Exception as exc:
        logger.debug("errors() probe failed on %s: %s", src.path(), exc)
        return True
    if errors:
        brief = str(errors).strip().splitlines()[0][:80]
        _status("源节点 {} 存在错误，未粘贴：{}".format(src.name(), brief),
                error=True)
        return False
    return True


def _eval_source(src):
    """hscriptExpression 求包围盒尺寸+中心，键 (元组名, 分量下标)。"""
    import hou
    path = src.path()
    return {
        (tuple_name, index): hou.hscriptExpression(
            '{}("{}", {})'.format(func, path, axis))
        for tuple_name, index, func, axis in _COMPONENT_SPECS
    }


def _status(message, error=False):
    try:
        import hou
        hou.ui.setStatusMessage(
            "HouTools: " + message,
            hou.severityType.Error if error else hou.severityType.ImportantMessage)
    except Exception as exc:
        logger.debug("setStatusMessage failed: %s", exc)
