"""外部文件拖放导入（官方 externaldragdrop 钩子）
=============================================
Houdini 对每一次文件拖放（落到主窗口任意面板）都会查找 HOUDINI_PATH 上的
``scripts/externaldragdrop.py``，以 ``exec(<stdin>)`` 方式执行它（因此该
文件没有 ``__file__``，也不应在其中写逻辑），然后调用其 ``dropAccept``
并传入拖入的文件路径列表：返回 ``True`` 表示本次拖放已被接管，返回
``False`` 表示交还 Houdini 原生处理。本模块承接仓库根钩子文件的分发，
实现 .abc / .fbx 的导入分支：

  - 拖入列表不含 .abc/.fbx → False，其他文件类型的原生行为完全不受影响；
  - 落点不是网络编辑器 → False（拖到参数框 fileName 填路径等照旧）；
  - Object 层级：
      .abc → 逐文件创建 ``alembicarchive`` 并触发其 ``buildHierarchy``
             按钮参数（等价 File > Import > Alembic Scene...）；
      .fbx → 逐文件调官方 ``hou.hipFile.importFBX``（等价 File > Import >
             Filmbox FBX...，恒定导入到 /obj 根），再把返回的 subnet 挪到
             落点处（官方 API 无落点参数，建完重摆），最后解冻整棵导入
             子树——官方导入默认给 file 节点加 Lock 旗标（网络编辑器黄橙
             冻结样式、参数不可改），按用户需求导入即解锁；
  - Sop 层级：
      .abc → 逐文件创建 SOP ``alembic`` 节点（等价 Tab 菜单 alembic）；
      .fbx → 逐文件创建 SOP ``file`` 节点（file SOP 原生可读 .fbx）；
  - 其余层级 → False 交还原生。
  混合拖放时各类型共用一套纵向错开序；首个节点落在鼠标处。abc 整批一个
  undo 槽；importFBX 不参与 undo（与官方导入一致），该分支不加 undo 组。

实测要点（H22.0.429，hython 无头 + GUI 实拖）：
  - 节点名只接受 ``[A-Za-z0-9._-]``，中文等字符 createNode 直接报错，须清洗；
  - ``buildHierarchy`` 是按钮参数而非 Python 方法，须 ``pressButton()`` 触发；
  - 坏 abc 的 build 静默失败（不抛异常、不建子节点），按 children 数给反馈；
  - importFBX（无头实测）：坏文件**不抛异常**，返回 ``(None, 错误信息串)``
    且不污染场景；文件缺失才抛 OperationFailed；subnet 以文件自动命名、
    重名自动加后缀；无论当前 pwd 是什么都导入到 /obj 根（嵌套 Object 网络
    里落点摆不了，留官方默认位置）；suppress_save_prompt=True 避免拖放流
    中途弹"保存场景"模态框；官方导入会把子树里的 file 节点打上
    ``hou.nodeFlag.Lock`` 旗标（冻结样式），经 ``setGenericFlag`` 解开、
    存盘重开不回锁；
  - 钩子内裸抛异常时 Houdini 会把拖入文件当 hip 文件打开（弹错误框），因此
    drop_accept 全程兜底：记日志 + 状态栏提示 + return True。
"""

import os
import re
import traceback
from urllib.parse import urlparse
from urllib.request import url2pathname

from houtools.core.log import get_logger

logger = get_logger("dragdrop.import")

_ABC_EXT = ".abc"
_FBX_EXT = ".fbx"

# 多文件纵向错开时相邻节点的间距（网络单位）
_STACK_GAP = 0.5

_UNDO_GROUP_ABC = "Import ABC Drag"
_UNDO_GROUP_FBX = "Import FBX Drag"


def drop_accept(file_list):
    """externaldragdrop 钩子入口：接管 .abc/.fbx 拖放，其余一律交还原生。"""
    items = list(file_list or [])
    abc_files = _extract_ext_files(items, _ABC_EXT)
    fbx_files = _extract_ext_files(items, _FBX_EXT)
    if not abc_files and not fbx_files:
        return False
    try:
        return _import_files_under_cursor(abc_files, fbx_files)
    except Exception:
        # 钩子裸抛会让 Houdini 把拖入文件当 hip 打开（错误框），必须吞掉
        logger.warning("drag import failed:\n%s", traceback.format_exc())
        _status("拖放导入出错，详见 Houdini Console 日志")
        return True


def _extract_ext_files(file_list, ext):
    """过滤出指定扩展名条目（大小写不敏感），file:// URL 解码为本地路径。"""
    files = []
    for item in file_list:
        if not isinstance(item, str) or not item:
            continue
        path = _to_local_path(item)
        if os.path.splitext(path)[1].lower() == ext:
            files.append(path)
    return files


def _to_local_path(item):
    """file:///C:/lib/a%20b.abc 之类 URL 形态的拖放条目转本地路径。"""
    parsed = urlparse(item)
    if parsed.scheme == "file":
        return url2pathname(parsed.path)
    return item


def _clean_name(path):
    """文件名 stem → 合法节点名：非法字符折叠为 _，全非法回退 <ext>_import。

    Houdini 节点名只接受 [A-Za-z0-9._-]，中文/重音字符/首尾空格直接
    OperationFailed（不会自动清洗）；createNode 撞名自动加数字后缀，
    同批同名文件不冲突。数字开头补 <ext>_ 前缀（回退名同理取扩展名，
    避免 .fbx 文件得到 abc_* 的误导性节点名）。
    """
    stem, ext = os.path.splitext(os.path.basename(path))
    ext = ext.lower().lstrip(".") or "file"
    name = re.sub(r"[^A-Za-z0-9._-]", "_", stem)
    name = re.sub(r"_+", "_", name).strip("_")
    if not name:
        name = ext + "_import"
    if not re.match(r"[A-Za-z]", name[0]):
        name = ext + "_" + name
    return name


def _import_files_under_cursor(abc_files, fbx_files):
    """在鼠标下方网络编辑器导入；非目标层级交还原生。"""
    import hou

    pane = hou.ui.paneTabUnderCursor()
    if not isinstance(pane, hou.NetworkEditor):
        logger.debug("drop target is not a network editor: %r", pane)
        return False
    context = pane.pwd()
    position = pane.cursorPosition()
    category = context.childTypeCategory().name()
    if category == "Object":
        handled = False
        if abc_files:
            handled = _import_nodes(
                context, position, abc_files,
                node_type="alembicarchive", parm_name="fileName",
                label="Alembic Archive", build_hierarchy=True,
                succeeded=lambda n: bool(n.children()),
                undo_group=_UNDO_GROUP_ABC) or handled
        if fbx_files:
            handled = _import_fbx_scenes(context, position, fbx_files,
                                         stack_from=len(abc_files)) or handled
        return handled
    if category == "Sop":
        handled = False
        if abc_files:
            handled = _import_nodes(
                context, position, abc_files,
                node_type="alembic", parm_name="fileName", label="Alembic",
                undo_group=_UNDO_GROUP_ABC) or handled
        if fbx_files:
            handled = _import_nodes(
                context, position, fbx_files,
                node_type="file", parm_name="file", label="FBX",
                undo_group=_UNDO_GROUP_FBX) or handled
        return handled
    # 其他层级（Dop/Lop...）交还原生
    logger.debug("drop network %s category %s not handled",
                 context.path(), category)
    return False


def _import_fbx_scenes(context, position, files, stack_from=0):
    """Object 层级 FBX：官方 importFBX（等价 File > Import > Filmbox FBX...）。

    官方 API 无落点参数、恒定导入到 /obj 根，建完把返回的 subnet 挪到落点
    （嵌套 Object 网络落点摆不到，留官方默认位置）。importFBX 不参与 undo，
    故不加 undo 组；坏文件返回 (None, 错误串)，按 None 计失败继续下一文件。
    导入后解冻整棵子树（官方默认锁定 file 节点，用户要求导入即可编辑）。
    """
    import hou

    created, failed = [], []
    # importFBX 恒定落到 /obj 根：落点定位仅在 /obj 下生效，其余情境照官方
    # 默认位置摆放，状态栏也如实汇报实际落点
    landing = context.path() if context.path() == "/obj" else "/obj"
    for i, path in enumerate(files):
        try:
            parent, messages = hou.hipFile.importFBX(path,
                                                     suppress_save_prompt=True)
        except hou.OperationFailed as exc:
            failed.append((path, str(exc)))
            continue
        if parent is None:
            failed.append((path, messages))
            continue
        if messages:
            logger.warning("fbx import messages (%s):\n%s", path, messages)
        _unlock_imported(parent)
        if context.path() == "/obj":
            _width, height = parent.size()
            parent.setPosition(hou.Vector2(
                position.x(),
                position.y() - (stack_from + i) * (height + _STACK_GAP)))
        created.append(parent)

    if created:
        _status("已导入 %d 个 FBX → %s" % (len(created), landing))
    for path, why in failed:
        logger.warning("fbx import failed: %s\n%s", path, why)
    if failed:
        _status("%d 个 .fbx 未能导入（文件损坏或不受支持），详见日志" % len(failed))
    return True


def _unlock_imported(parent):
    """解冻 importFBX 产出的整棵子树。

    官方导入默认给子树里的 file 节点打 ``hou.nodeFlag.Lock`` 旗标（网络
    编辑器黄橙冻结样式、参数不可改），这里逐个解开；无锁节点零开销跳过。
    解锁后节点正常 cook，存盘重开不回锁（H22.0.429 无头实测）。
    """
    import hou

    # allSubChildren 不含 parent 自身，subnet 本体一并纳入
    for node in [parent] + list(parent.allSubChildren()):
        if node.isGenericFlagSet(hou.nodeFlag.Lock):
            node.setGenericFlag(hou.nodeFlag.Lock, False)


def _import_nodes(context, position, files, node_type, parm_name, label,
                  build_hierarchy=False, succeeded=None, undo_group=None,
                  stack_from=0):
    """逐文件创建导入节点：首个在鼠标处、其余按节点高度纵向错开。

    ``succeeded`` 为 None 表示创建即成功（SOP alembic/file 坏文件由节点
    错误旗标反馈）；alembicarchive 的 build 静默失败，须按 children 数判定。
    """
    import hou

    created, failed = [], []
    with hou.undos.group(undo_group or _UNDO_GROUP_ABC):
        for i, path in enumerate(files):
            node = context.createNode(node_type, node_name=_clean_name(path))
            _width, height = node.size()
            node.setPosition(
                hou.Vector2(position.x(),
                            position.y() - (stack_from + i)
                            * (height + _STACK_GAP)))
            node.parm(parm_name).set(path)
            if build_hierarchy:
                node.parm("buildHierarchy").pressButton()
            (created if succeeded is None or succeeded(node)
             else failed).append(node)

    if created:
        _status("已导入 %d 个 %s → %s" % (len(created), label, context.path()))
    for node in failed:
        logger.warning("import produced no hierarchy: %s (%s)",
                       node.path(), node.parm(parm_name).unexpandedString())
    if failed:
        _status("%d 个文件未能导入（文件损坏或为空），详见日志" % len(failed))
    return True


def _status(message):
    """状态栏提示（不弹窗，保持拖放操作流不被打断）。"""
    try:
        import hou
        hou.ui.setStatusMessage(message, hou.severityType.ImportantMessage)
    except Exception as exc:
        logger.debug("setStatusMessage failed: %s", exc)
