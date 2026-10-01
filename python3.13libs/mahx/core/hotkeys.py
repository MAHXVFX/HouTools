"""MAHX 默认热键分配
====================
会话启动时（`python3.13libs/uiready.py` 调用 `install_defaults`）把
MAHX 菜单项的默认键位写进 Houdini 热键系统。只写会话内的默认值，
用户在 Hotkey Manager 里的自定义始终优先（已有键位时不覆盖）。

符号约定：NetworkViewMenu.xml 里 id 为 ``pane.wsheet.<name>`` 的
scriptItem，其热键符号为 ``h.pane.wsheet.<name>``（网络编辑器上下文）。
"""

from mahx.core.log import get_logger

logger = get_logger("core.hotkeys")

# (context, hotkey_symbol, key)
DEFAULT_ASSIGNMENTS = (
    (
        "h.pane.wsheet",
        "h.pane.wsheet.mahx_paste_as_object_merge",
        "Ctrl+Shift+V",
    ),
)


def install_defaults() -> list[str]:
    """分配所有默认键位，返回失败的条目描述列表（空 = 全部成功或已跳过）。"""
    import hou

    failed: list[str] = []
    for context, symbol, key in DEFAULT_ASSIGNMENTS:
        try:
            try:
                if hou.hotkeys.assignments(context, symbol):
                    continue  # 已有键位（含用户自定义），不覆盖
            except Exception:
                pass  # 查询失败不阻塞分配，由 addAssignment 的返回值兜底

            if not hou.hotkeys.addAssignment(context, symbol, key):
                failed.append(f"{symbol} <- {key}（符号未注册或键位无效）")
        except Exception as e:
            failed.append(f"{symbol} <- {key}: {e}")

    if failed:
        logger.warning("默认键位分配失败: %s", "; ".join(failed))
    return failed
