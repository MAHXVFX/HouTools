"""Delete the viewport capture attached to the right-clicked node.

The OPmenu.xml at the repo root hangs "Delete Viewport Capture [HT]" on
nodes carrying the capture marker (capture_viewport_embed.MARKER_KEY); the
menu scriptCode is dispatched via dev.dispatcher, landing at this module's
run(kwargs).  All logic lives in capture_viewport_embed.delete_capture()
(kept there so the marker/路径 codec/回调 helpers stay in one place);
this module imports it lazily inside run() so the reloader's
insertion-order dependency (先被 import 的是依赖) is never violated.
"""

from houtools.core.log import get_logger

log = get_logger("tools.delete_viewport_capture")


def run(kwargs=None):
    """OPmenu 菜单入口；kwargs 为菜单脚本上下文（含 node）。"""
    import hou

    node = (kwargs or {}).get("node")
    if node is None:
        return
    from houtools.tools import capture_viewport_embed as cve
    try:
        removed_entry, removed_file = cve.delete_capture(node)
    except RuntimeError as exc:
        log.warning("%s", exc)
        hou.ui.setStatusMessage(
            "HouTools: {}".format(exc), severity=hou.severityType.Error)
        return
    except Exception:
        log.exception("delete_viewport_capture crashed")
        hou.ui.setStatusMessage(
            "HouTools: 删除截图失败（详见日志）",
            severity=hou.severityType.Error)
        return
    if removed_entry or removed_file:
        hou.ui.setStatusMessage("HouTools: 已删除节点截图（{}）".format(
            "、".join(filter(None, (
                "背景图条目" if removed_entry else None,
                "磁盘文件" if removed_file else None)))))
    else:
        # 标记在但条目/文件都不在（如手动删过图）：只清标记与回调
        hou.ui.setStatusMessage(
            "HouTools: 未找到可删除的截图，已清理残留标记")
