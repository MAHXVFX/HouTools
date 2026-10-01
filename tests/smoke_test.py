"""Headless smoke test: menu XML, package imports, hot reload.

Run with Houdini's Python (no GUI, no Houdini session needed):

    "C:\\Program Files\\Side Effects Software\\Houdini 22.0.429\\python313\\python.exe" tests\\smoke_test.py
"""

import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python3.13libs"))

# Houdini's PySide6 lives in the "forced" site-packages and links against
# the Qt DLLs in $HFS/bin; a plain python.exe has neither on its search
# path, so add both (inside Houdini neither line is needed).
def _find_houdini_root() -> Path:
    env = os.environ.get("HFS")
    if env and (Path(env) / "python313").exists():
        return Path(env)
    for base in (Path(r"C:\Program Files\Side Effects Software"),
                 Path(r"D:\Program Files\Side Effects Software")):
        if base.is_dir():
            for candidate in sorted(base.glob("Houdini 22.0*"), reverse=True):
                if (candidate / "python313").exists():
                    return candidate
    return Path(r"C:\Program Files\Side Effects Software\Houdini 22.0.429")


HOUDINI_ROOT = _find_houdini_root()
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

    # 粘贴为 Object Merge：XML 里的 item id 与热键符号约定必须一一对应
    nv_xml = (ROOT / "NetworkViewMenu.xml").read_text(encoding="utf-8")
    assert 'id="pane.wsheet.mahx_paste_as_object_merge"' in nv_xml
    compile((ROOT / "python3.13libs" / "uiready.py").read_text(encoding="utf-8"),
            "uiready.py", "exec")
    print("paste_as_object_merge wiring: consistent")

    ET.parse(ROOT / "python_panels" / "MA_Automation.pypanel")
    print("MA_Automation.pypanel: well-formed")

    import mahx
    import mahx.core.constants
    import mahx.core.settings
    import mahx.core.hotkeys
    import mahx.dev.dispatcher  # noqa: F401
    import mahx.tools.paste_as_object_merge
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

    # 粘贴为 Object Merge：路径/表达式解析
    from mahx.tools import paste_as_object_merge as pam
    assert pam._HOU_NODE_RE.match('hou.node("/obj/grid1")').group(1) \
        == "/obj/grid1"
    assert pam._HOU_NODE_RE.match("hou.parm('/obj/x')") is None
    print("paste_as_object_merge parsing OK")

    # 打开DW：任务序列化 + 应用级配置文件读写
    dw_item = task_types.TaskItem.from_dict(
        {"type": "OPEN_DW", "params": {}, "enabled": True})
    assert dw_item.task_type is task_types.TaskType.OPEN_DW
    assert dw_item.to_dict() == {"type": "OPEN_DW", "params": {}, "enabled": True}

    from unittest.mock import patch
    from mahx.automation import data_manager as dm

    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "MA_Automation_Config.json"
        with patch.object(dm.MA_Automation_DataManager, "get_app_config_path",
                          return_value=str(cfg)):
            assert dm.MA_Automation_DataManager.load_dw_exe_path() \
                == dm.DW_EXE_PATH_DEFAULT
            dm.MA_Automation_DataManager.ensure_dw_config()
            assert cfg.exists(), "ensure_dw_config did not create the file"
            assert dm.MA_Automation_DataManager.load_dw_exe_path() \
                == dm.DW_EXE_PATH_DEFAULT
            cfg.write_text(json.dumps({"dw_exe_path": "D:/tools/dw.exe"}),
                           encoding="utf-8")
            assert dm.MA_Automation_DataManager.load_dw_exe_path() \
                == "D:/tools/dw.exe"
            # 缺字段时 ensure 只补齐,不覆盖用户已有键
            cfg.write_text(json.dumps({"other": 1}), encoding="utf-8")
            dm.MA_Automation_DataManager.ensure_dw_config()
            data = json.loads(cfg.read_text(encoding="utf-8"))
            assert data["other"] == 1
            assert data["dw_exe_path"] == dm.DW_EXE_PATH_DEFAULT

    # 随项目发布的配置文件存在且含有效 dw_exe_path 字段
    shipped = json.loads(
        (ROOT / "MA_Automation_Config.json").read_text(encoding="utf-8"))
    assert isinstance(shipped.get("dw_exe_path"), str)
    assert shipped["dw_exe_path"].strip()
    print("OPEN_DW task + app config OK")

    # ffmpeg 查找函数可执行（无头环境找不到也不算失败）
    print("find_ffmpeg ->", mahx.videoseq.ffmpeg.find_ffmpeg())

    # 真实实例化 MA Automation 界面（捕获 __init__ 结构损伤）
    from PySide6.QtWidgets import (
        QApplication, QComboBox, QLineEdit, QPushButton, QStackedWidget)

    app = QApplication.instance() or QApplication([])
    from mahx.automation.window import AutomationWindow

    win = AutomationWindow()
    for name in ("startBtn", "autoFillBtn", "clearBtn", "settingsBtn"):
        assert win.findChild(QPushButton, name) is not None, f"missing {name}"
    assert win._slot_widgets, "slot state not initialized"
    assert win._selected_index is None

    # 任务类型下拉与 stacked 页对齐 + 打开DW 收集
    slot0 = win._slot_widgets[0]
    combo = slot0.findChild(QComboBox, "taskType")
    assert combo is not None and combo.count() == 4, combo
    stacked = slot0.findChild(QStackedWidget, "paramsStacked")
    assert stacked is not None and stacked.count() == 4, stacked
    combo.setCurrentIndex(3)  # 打开DW
    collected = win._collect_data()
    assert collected[0]["type"] == "OPEN_DW"
    assert collected[0]["params"] == {}

    # 回归：Flipbook 参数在 stacked 之外,收集必须仍取到编辑值
    combo.setCurrentIndex(1)
    sf_le = slot0.findChild(QLineEdit, "flipbookStartFrame")
    assert sf_le is not None
    sf_le.setText("101")
    assert win._collect_data()[0]["params"]["start_frame"] == "101"

    # 显示/隐藏生命周期:事件过滤器随可见性装卸(不抛异常即通过)
    win.show()
    win.hide()

    # 单实例注册表:create_panel_widget 登记弱引用,失效引用被清理
    from mahx.automation import window as aw
    w2 = aw.create_panel_widget()
    assert any(r() is w2 for r in aw._PANEL_REFS)
    assert w2 in aw._iter_live_panels()
    w2.deleteLater()
    aw._PANEL_REFS.clear()

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
