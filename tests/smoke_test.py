"""Headless smoke test: menu XML, package imports, hot reload.

Run with Houdini's Python (no GUI, no Houdini session needed):

    "C:\\Program Files\\Side Effects Software\\Houdini 22.0.429\\python313\\python.exe" tests\\smoke_test.py
"""

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python3.13libs"))

# Houdini's PySide6 lives in the "forced" site-packages and links against
# the Qt DLLs in $HFS/bin; a plain python.exe has neither on its search
# path, so add both (inside Houdini neither line is needed).
HOUDINI_ROOT = Path(r"C:\Program Files\Side Effects Software\Houdini 22.0.429")
_pyside_dir = HOUDINI_ROOT / "python313" / "lib" / "site-packages-forced"
if _pyside_dir.exists():
    sys.path.append(str(_pyside_dir))
_qt_bin = HOUDINI_ROOT / "bin"
if _qt_bin.exists():
    os.add_dll_directory(str(_qt_bin))


def main():
    for menu_file in ("MainMenuCommon.xml", "NetworkViewMenu.xml"):
        ET.parse(ROOT / menu_file)
    print("menu XMLs: well-formed")

    ET.parse(ROOT / "python_panels" / "MA_Automation.pypanel")
    print("MA_Automation.pypanel: well-formed")

    import mahx
    import mahx.core.constants
    import mahx.core.settings
    import mahx.dev.dispatcher  # noqa: F401
    import mahx.automation.window  # noqa: F401
    import mahx.videoseq.window  # noqa: F401
    import mahx.videoseq.ffmpeg
    from mahx.automation import task_types
    from mahx.dev import reloader

    print("imports OK, mahx", mahx.__version__)
    assert mahx.core.constants.PROJECT_ROOT == ROOT, PROJECT_ROOT_MESSAGE

    # TaskItem 序列化 / 反序列化闭环
    item = task_types.TaskItem(
        task_type=task_types.TaskType.BUTTON_CLICK,
        params=task_types.ButtonClickParams(
            node_path="/obj/grid1", parm_name="execute"),
        enabled=True,
    )
    restored = task_types.TaskItem.from_dict(item.to_dict())
    assert restored.params.node_path == "/obj/grid1"
    assert restored.params.parm_name == "execute"
    print("task_types round-trip OK")

    # ffmpeg 查找函数可执行（无头环境找不到也不算失败）
    print("find_ffmpeg ->", mahx.videoseq.ffmpeg.find_ffmpeg())

    # 真实实例化 MA Automation 界面（捕获 __init__ 结构损伤）
    from PySide6.QtWidgets import QApplication, QPushButton

    app = QApplication.instance() or QApplication([])
    from mahx.automation.window import AutomationWindow

    win = AutomationWindow()
    for name in ("startBtn", "autoFillBtn", "clearBtn", "settingsBtn"):
        assert win.findChild(QPushButton, name) is not None, f"missing {name}"
    assert win._slot_widgets, "slot state not initialized"
    assert win._selected_index is None
    win._remove_slot(0)  # 槽管理冒烟
    print("AutomationWindow instantiation OK")

    summary = reloader.reload_all()
    print("reload_all ->", summary)
    assert "FAILED" not in summary, summary

    # Settings round-trip against the real settings/ directory.
    store = mahx.core.settings.JsonStore("_smoke_test.json", defaults={"n": 1})
    store.set("n", 2, save=True)
    assert store.get("n") == 2
    store.path.unlink(missing_ok=True)
    print("settings round-trip OK")

    print("SMOKE TEST OK")


PROJECT_ROOT_MESSAGE = (
    "PROJECT_ROOT mismatch - check parents[3] in core/constants.py"
)

if __name__ == "__main__":
    main()
