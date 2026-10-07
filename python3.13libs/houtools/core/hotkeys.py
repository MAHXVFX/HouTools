"""HouTools 热键:默认分配 + 用户自定义
=====================================
会话启动时（`python3.13libs/uiready.py` 调用 `install_defaults`）把
HouTools 菜单项的键位写进 Houdini 热键系统。

键位来源优先级:
  1. `settings/hotkeys.json` 里的用户自定义（主菜单 `Paste Hotkey
     Settings` 界面写入）—— 每次启动都会应用(addAssignment 覆盖);
  2. 用户在 Houdini Hotkey Manager 里的手动自定义 —— 已有键位时不覆盖;
  3. 内置默认值。

符号约定:NetworkViewMenu.xml 里 id 为 ``pane.wsheet.<name>`` 的
scriptItem,其热键符号为 ``h.pane.wsheet.<name>``(网络编辑器上下文)。
"""

from houtools.core.log import get_logger

logger = get_logger("core.hotkeys")

# (context, hotkey_symbol, default_key)
DEFAULT_ASSIGNMENTS = (
    ("h.pane.wsheet", "h.pane.wsheet.houtools_paste_as_object_merge",
     "Ctrl+Shift+V"),
)

_STORE = None


def _store():
    """热键自定义存储(懒加载,避免 import 期副作用)。"""
    global _STORE
    if _STORE is None:
        from houtools.core.settings import JsonStore

        _STORE = JsonStore("hotkeys.json", defaults={})
    return _STORE


def get_custom_key(symbol: str) -> str | None:
    """返回用户通过 Paste Hotkey Settings 设置的自定义键位。"""
    value = _store().get(symbol)
    return value if isinstance(value, str) and value.strip() else None


def set_custom_key(symbol: str, key: str) -> None:
    """持久化用户自定义键位(JsonStore 自动落盘)。"""
    _store().set(symbol, key)


def clear_custom_key(symbol: str) -> None:
    """删除用户自定义键位(回退 Hotkey Manager 已有键位 / 默认值)。"""
    _store().remove(symbol)


def install_defaults() -> list[str]:
    """分配所有默认键位,返回失败的条目描述列表(空 = 全部成功或已跳过)。"""
    import hou

    failed: list[str] = []
    for context, symbol, default_key in DEFAULT_ASSIGNMENTS:
        try:
            custom = get_custom_key(symbol)
            key = custom or default_key
            if not custom:
                # 无自定义时尊重 Houdini Hotkey Manager 的已有键位
                try:
                    if hou.hotkeys.assignments(context, symbol):
                        continue  # 已有键位(含用户自定义),不覆盖
                except Exception as e:
                    # 查询失败不阻塞分配,由 addAssignment 的返回值兜底
                    logger.debug("assignments query failed for %s: %s",
                                 symbol, e)

            if not hou.hotkeys.addAssignment(context, symbol, key):
                failed.append(f"{symbol} <- {key}(符号未注册或键位无效)")
        except Exception as e:
            failed.append(f"{symbol} <- {key}: {e}")

    if failed:
        logger.warning("默认键位分配失败: %s", "; ".join(failed))
    return failed
