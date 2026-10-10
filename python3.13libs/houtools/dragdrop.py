"""外部文件拖放导入（官方 externaldragdrop 钩子）
=============================================
Houdini 对每一次文件拖放（落到主窗口任意面板）都会查找 HOUDINI_PATH 上的
``scripts/externaldragdrop.py``，以 ``exec(<stdin>)`` 方式执行它（因此该
文件没有 ``__file__``，也不应在其中写逻辑），然后调用其 ``dropAccept``
并传入拖入的文件路径列表：返回 ``True`` 表示本次拖放已被接管，返回
``False`` 表示交还 Houdini 原生处理。本模块承接仓库根钩子文件的分发，
实现 .abc 的导入分支：

  - 拖入列表不含 .abc → False，其他文件类型的原生行为完全不受影响；
  - 落点不是网络编辑器 → False（拖到参数框 fileName 填路径等照旧）；
  - 落点网络不是 Object 层级 → False（交给原生处理，如 SOP 网络弹导入菜单）；
  - Object 层级 → 逐文件创建 ``alembicarchive`` 并触发其 ``buildHierarchy``
    按钮参数（等价 File > Import > Alembic Scene... 的 obj 层级导入），
    首个节点落在鼠标处，其余按节点高度纵向错开；整批一个 undo 槽。

实测要点（H22.0.429，hython 无头 + GUI 实拖）：
  - 节点名只接受 ``[A-Za-z0-9._-]``，中文等字符 createNode 直接报错，须清洗；
  - ``buildHierarchy`` 是按钮参数而非 Python 方法，须 ``pressButton()`` 触发；
  - 坏 abc 的 build 静默失败（不抛异常、不建子节点），按 children 数给反馈；
  - 钩子内裸抛异常时 Houdini 会把 .abc 当 hip 文件打开（弹错误框），因此
    drop_accept 全程兜底：记日志 + 状态栏提示 + return True。
"""

import os
import re
import traceback
from urllib.parse import urlparse
from urllib.request import url2pathname

from houtools.core.log import get_logger

logger = get_logger("dragdrop.abc")

_ABC_EXT = ".abc"

# 多文件纵向错开时相邻节点的间距（网络单位）
_STACK_GAP = 0.5

_UNDO_GROUP = "Import ABC Drag"


def drop_accept(file_list):
    """externaldragdrop 钩子入口：接管 .abc 拖放，其余一律交还原生。"""
    files = _extract_abc_files(file_list or [])
    if not files:
        return False
    try:
        return _import_into_network_under_cursor(files)
    except Exception:
        # 钩子裸抛会让 Houdini 把 .abc 当 hip 打开（错误框），必须吞掉
        logger.warning("abc drag import failed:\n%s", traceback.format_exc())
        _status(".abc 拖放导入出错，详见 Houdini Console 日志")
        return True


def _extract_abc_files(file_list):
    """过滤出 .abc 条目（大小写不敏感），file:// URL 解码为本地路径。"""
    files = []
    for item in file_list:
        if not isinstance(item, str) or not item:
            continue
        path = _to_local_path(item)
        if os.path.splitext(path)[1].lower() == _ABC_EXT:
            files.append(path)
    return files


def _to_local_path(item):
    """file:///C:/lib/a%20b.abc 之类 URL 形态的拖放条目转本地路径。"""
    parsed = urlparse(item)
    if parsed.scheme == "file":
        return url2pathname(parsed.path)
    return item


def _clean_name(path):
    """文件名 stem → 合法节点名：非法字符折叠为 _，全非法回退 abc_import。

    Houdini 节点名只接受 [A-Za-z0-9._-]，中文/重音字符/首尾空格直接
    OperationFailed（不会自动清洗）；createNode 撞名自动加数字后缀，
    同批同名文件不冲突。
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    name = re.sub(r"[^A-Za-z0-9._-]", "_", stem)
    name = re.sub(r"_+", "_", name).strip("_") or "abc_import"
    if not re.match(r"[A-Za-z]", name[0]):
        name = "abc_" + name
    return name


def _import_into_network_under_cursor(files):
    """在鼠标下方网络编辑器创建 Alembic Archive；非目标落点交还原生。"""
    import hou

    pane = hou.ui.paneTabUnderCursor()
    if not isinstance(pane, hou.NetworkEditor):
        logger.debug("drop target is not a network editor: %r", pane)
        return False
    context = pane.pwd()
    if context.childTypeCategory().name() != "Object":
        # SOP 等其他层级交还原生（原生会按上下文弹导入菜单）
        logger.debug("drop network %s is not Object level", context.path())
        return False

    position = pane.cursorPosition()
    created, failed = [], []
    with hou.undos.group(_UNDO_GROUP):
        for i, path in enumerate(files):
            node = context.createNode(
                "alembicarchive", node_name=_clean_name(path))
            _width, height = node.size()
            node.setPosition(
                hou.Vector2(position.x(), position.y() - i * (height + _STACK_GAP)))
            node.parm("fileName").set(path)
            node.parm("buildHierarchy").pressButton()
            # 坏 abc 的 build 静默失败：不抛异常、不建子节点
            (created if node.children() else failed).append(node)

    if created:
        _status("已导入 %d 个 Alembic Archive → %s"
                % (len(created), context.path()))
    for node in failed:
        logger.warning("abc import produced no hierarchy: %s (%s)",
                       node.path(), node.parm("fileName").unexpandedString())
    if failed:
        _status("%d 个 .abc 未能导入（文件损坏或为空），详见日志" % len(failed))
    return True


def _status(message):
    """状态栏提示（不弹窗，保持拖放操作流不被打断）。"""
    try:
        import hou
        hou.ui.setStatusMessage(message, hou.severityType.ImportantMessage)
    except Exception as exc:
        logger.debug("setStatusMessage failed: %s", exc)
