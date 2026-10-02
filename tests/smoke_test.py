"""Headless smoke test: menu XML, package imports, hot reload.

Run with Houdini's Python (no GUI, no Houdini session needed):

    "C:\\Program Files\\Side Effects Software\\Houdini 22.0.429\\python313\\python.exe" tests\\smoke_test.py
"""

import json
import os
import sys
import tempfile
import time
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

    # 粘贴为 Object Merge:入口在网络编辑器菜单(item id 用 pane.wsheet.*
    # 前缀,热键符号随之);主菜单仅保留快捷键设置
    main_xml = (ROOT / "MainMenuCommon.xml").read_text(encoding="utf-8")
    assert 'id="houtools.paste_as_object_merge"' not in main_xml
    assert 'id="houtools.paste_hotkey_settings"' in main_xml
    nv_xml = (ROOT / "NetworkViewMenu.xml").read_text(encoding="utf-8")
    assert 'id="pane.wsheet.houtools_paste_as_object_merge"' in nv_xml
    compile((ROOT / "python3.13libs" / "uiready.py").read_text(encoding="utf-8"),
            "uiready.py", "exec")
    print("paste_as_object_merge wiring: consistent")

    # Hdr Library:两份菜单均注册薄分发器入口
    assert 'id="houtools.hdr_library"' in main_xml
    assert 'id="houtools.networkview.hdr_library"' in nv_xml
    print("hdr_library menu wiring: consistent")

    ET.parse(ROOT / "python_panels" / "Automation.pypanel")
    print("Automation.pypanel: well-formed")

    import houtools
    import houtools.core.constants
    import houtools.core.settings
    import houtools.core.hotkeys
    import houtools.dev.dispatcher  # noqa: F401
    import houtools.tools.paste_as_object_merge
    import houtools.tools.paste_hotkey_settings  # noqa: F401
    import houtools.tools.automation  # noqa: F401
    import houtools.automation.window  # noqa: F401
    import houtools.videoseq.window  # noqa: F401
    import houtools.videoseq.ffmpeg
    import houtools.hdrlight.browser  # noqa: F401
    import houtools.tools.hdr_library  # noqa: F401
    from houtools.automation import task_types
    from houtools.dev import reloader

    print("imports OK, houtools", houtools.__version__)
    assert houtools.core.constants.PROJECT_ROOT == ROOT, PROJECT_ROOT_MESSAGE

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
    from houtools.tools import paste_as_object_merge as pam
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
    from houtools.automation import data_manager as dm

    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "Automation_Config.json"
        with patch.object(dm.AutomationDataManager, "get_app_config_path",
                          return_value=str(cfg)):
            assert dm.AutomationDataManager.load_dw_exe_path() \
                == dm.DW_EXE_PATH_DEFAULT
            dm.AutomationDataManager.ensure_dw_config()
            assert cfg.exists(), "ensure_dw_config did not create the file"
            assert dm.AutomationDataManager.load_dw_exe_path() \
                == dm.DW_EXE_PATH_DEFAULT
            cfg.write_text(json.dumps({"dw_exe_path": "D:/tools/dw.exe"}),
                           encoding="utf-8")
            assert dm.AutomationDataManager.load_dw_exe_path() \
                == "D:/tools/dw.exe"
            # 缺字段时 ensure 只补齐,不覆盖用户已有键
            cfg.write_text(json.dumps({"other": 1}), encoding="utf-8")
            dm.AutomationDataManager.ensure_dw_config()
            data = json.loads(cfg.read_text(encoding="utf-8"))
            assert data["other"] == 1
            assert data["dw_exe_path"] == dm.DW_EXE_PATH_DEFAULT

    # deserialize_params：Flipbook 字段恢复 + 旧 JSON 遗留字段过滤
    flipbook = dm.AutomationDataManager.deserialize_params(
        "FLIPBOOK",
        {
            "start_frame": "101",
            "end_frame": "200",
            "output_path": "$HIP/FlipBook/$HIPNAME/$HIPNAME.$F4.jpg",
            "save_to_disk": False,
            "frame_range": (1, 240),  # 旧版遗留字段，应被忽略
        },
    )
    assert flipbook.start_frame == "101"
    assert flipbook.end_frame == "200"
    assert flipbook.output_path == "$HIP/FlipBook/$HIPNAME/$HIPNAME.$F4.jpg"
    assert flipbook.save_to_disk is False
    print("deserialize_params Flipbook OK")

    # 随项目发布的配置文件存在且含有效 dw_exe_path 字段
    shipped = json.loads(
        (ROOT / "Automation_Config.json").read_text(encoding="utf-8"))
    assert isinstance(shipped.get("dw_exe_path"), str)
    assert shipped["dw_exe_path"].strip()
    print("OPEN_DW task + app config OK")

    # ffmpeg 查找函数可执行（无头环境找不到也不算失败）
    print("find_ffmpeg ->", houtools.videoseq.ffmpeg.find_ffmpeg())

    # 真实实例化 Automation 界面（捕获 __init__ 结构损伤）
    from PySide6.QtWidgets import (
        QApplication, QComboBox, QLineEdit, QPushButton, QStackedWidget)

    app = QApplication.instance() or QApplication([])
    from houtools.automation.window import AutomationWindow

    win = AutomationWindow()
    for name in ("startBtn", "autoFillBtn", "clearBtn", "settingsBtn",
                 "configNewBtn"):
        assert win.findChild(QPushButton, name) is not None, f"missing {name}"
    assert not win._config_combo.isEditable(), "config combo 应为只读"
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
    from houtools.automation import window as aw
    w2 = aw.create_panel_widget()
    assert any(r() is w2 for r in aw._PANEL_REFS)
    assert w2 in aw._iter_live_panels()
    w2.deleteLater()
    aw._PANEL_REFS.clear()

    # 新建配置:OK 后立即创建空配置文件并切换当前配置;同名不覆盖仅切换
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(dm.AutomationDataManager, "_hip_base",
                          return_value=tmp), \
             patch("PySide6.QtWidgets.QInputDialog.getText",
                   return_value=("MAtest", True)), \
             patch("PySide6.QtWidgets.QMessageBox.information",
                   return_value=None):
            win._on_new_config()
            cfg_dir = Path(tmp) / "HouTools_cfg" / "Automation_json"
            assert (cfg_dir / "MAtest.json").is_file(), "新建配置未立即落盘"
            assert json.loads((cfg_dir / "MAtest.json").read_text(
                encoding="utf-8"))["tasks"] == []
            assert win._current_config_name == "MAtest"
            # 同名新建:已有文件不被空配置覆盖,仅切换
            (cfg_dir / "MAtest.json").write_text(
                json.dumps({"tasks": [{"type": "OPEN_DW", "params": {},
                                       "enabled": True}]}),
                encoding="utf-8")
            win._on_new_config()
            data = json.loads(
                (cfg_dir / "MAtest.json").read_text(encoding="utf-8"))
            assert data["tasks"], "已有配置被空文件覆盖"
    print("new config creation OK")

    win._remove_slot(0)  # 槽管理冒烟
    print("AutomationWindow instantiation OK")

    # ExecutionEngine 构造（回归：曾因缺 data_manager import 在此 NameError）
    from houtools.automation.execution_engine import ExecutionEngine
    engine = ExecutionEngine([])
    assert engine._dw_exe_path
    print("ExecutionEngine instantiation OK")

    # Hdr Library:子文件夹分类扫描 + 缩略图相对路径命名
    from houtools.hdrlight import browser as hdr_browser
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "sunset.hdr").touch()
        day = Path(tmp, "day"); day.mkdir()
        (day / "noon.exr").touch()
        (day / "a.hdr").touch()
        (day / "a.exr").touch()  # 同名不同扩展，缩略图缓存键不得碰撞
        (day / "nested").mkdir()
        (day / "nested" / "deep.hdr").touch()  # 嵌套子文件夹归到第一级分类
        night = Path(tmp, "night"); night.mkdir()
        (night / "a.hdr").touch()  # 与 day 下同名，验证缩略图命名不冲突
        (night / ".thumb_cache").mkdir()
        (night / ".thumb_cache" / "junk.hdr").touch()  # 隐藏目录不扫描

        results = hdr_browser.scan_hdrs(tmp)
        assert len(results) == 6, results  # 6 个 HDR，隐藏目录里的不算
        rels_cats = {(os.path.relpath(p, tmp), c) for p, c, _t in results}
        assert ("sunset.hdr", "") in rels_cats, rels_cats          # 根目录 → 未分类
        assert (os.path.join("day", "noon.exr"), "day") in rels_cats
        assert (os.path.join("day", "nested", "deep.hdr"), "day") in rels_cats  # 嵌套归第一级
        assert not any("thumb_cache" in rp for rp, _c in rels_cats), rels_cats
        # 缓存名带相对路径 + 原扩展名:同名/同名字不同扩展互不冲突
        tp_day = hdr_browser.thumb_path(tmp, str(day / "a.hdr"))
        tp_night = hdr_browser.thumb_path(tmp, str(night / "a.hdr"))
        tp_exr = hdr_browser.thumb_path(tmp, str(day / "a.exr"))
        assert len({tp_day, tp_night, tp_exr}) == 3, (tp_day, tp_night, tp_exr)
        assert os.path.basename(tp_day) == "day__a.hdr.jpg"
        assert os.path.basename(
            hdr_browser.thumb_path(tmp, str(Path(tmp) / "sunset.hdr"))) \
            == "sunset.hdr.jpg"
        # 缩略图 mtime 旧于 HDR → 视为失效（内容更新后自动重生成）
        stale_tp = hdr_browser.thumb_path(tmp, str(day / "noon.exr"))
        os.makedirs(os.path.dirname(stale_tp), exist_ok=True)
        open(stale_tp, "wb").close()
        past = time.time() - 3600
        os.utime(stale_tp, (past, past))
        scan_map = {p: t for p, _c, t in hdr_browser.scan_hdrs(tmp)}
        assert scan_map[str(day / "noon.exr")] is None, scan_map
        # .part 残片无对应 HDR → 被 clean_stale_thumbs 清理
        open(stale_tp + ".part", "wb").close()
        assert hdr_browser.clean_stale_thumbs(tmp) >= 1
        print("HdrLibrary subfolder scan OK")

        # 收藏 + 侧栏过滤（用临时 JsonStore，不污染真实 settings/；
        # defaults 需含窗口构造读取的全部键）
        from PySide6 import QtCore
        fav_store = houtools.core.settings.JsonStore(
            "_smoke_hdr.json",
            defaults={"favorites": [], "thumb_size": 128, "pin_on_top": True})
        fav_store.set("favorites", [])  # 上次异常中断可能残留旧收藏
        with patch.object(hdr_browser, "_SETTINGS", fav_store):
            assert not hdr_browser.get_favorites()
            target = str(day / "a.hdr")
            assert hdr_browser.set_favorite(target, True) is True
            assert hdr_browser.set_favorite(target, True) is True  # 幂等
            win = hdr_browser._HdrLibraryWindow(tmp)
            assert win.list.count() == 6, win.list.count()
            # 侧栏：全部 / 收藏 / 未分类(根目录文件) / day / night
            keys = [win.sidebar.item(i).data(QtCore.Qt.UserRole)
                    for i in range(win.sidebar.count())]
            assert keys[0] == hdr_browser.KEY_ALL, keys
            assert keys[1] == hdr_browser.KEY_FAV, keys
            assert sorted(keys[2:]) == ["", "day", "night"], keys
            # 收藏项有 ★ 前缀（重建式过滤：条目按当前分类重建，
            # 切换视图后旧 item 对象已失效，须重新获取）
            fav_item = next(it for it in (win.list.item(i) for i in range(6))
                            if it.data(QtCore.Qt.UserRole) == target)
            assert fav_item.text().startswith("★"), fav_item.text()
            # 收藏视图过滤：6 个里只有 1 个收藏
            win._select_category(hdr_browser.KEY_FAV)
            assert win._visible_count() == 1, win._visible_count()
            # 取消收藏后从收藏视图消失
            fav_item = win.list.item(0)
            assert fav_item is not None
            win._set_favorite(fav_item, False)
            assert win._visible_count() == 0
            assert not hdr_browser.get_favorites()
            # 全部视图恢复
            win._select_category(hdr_browser.KEY_ALL)
            assert win._visible_count() == 6
            # 生成中显示进度行;线程池并发按核数取中低档(2..4)
            assert win.progress_widget.isVisibleTo(win), \
                "generating but progress row hidden"
            assert 2 <= win._thread.workers <= 4, win._thread.workers
            # 暂停/继续:暂停后标志位可见,经按钮处理器恢复
            win._thread.pause()
            assert win._thread.is_paused()
            win._on_pause_resume()
            assert not win._thread.is_paused()
            assert win.pause_btn.text() == "暂停"
            # 后台线程不得 parent 到窗口（PR 反馈:Reload 销毁窗口会连带
            # 销毁运行中的线程导致崩溃）；6 个缺缩略图会启动线程
            assert win._thread is None or win._thread.parent() is None
            # 停止处理器:唤醒(防暂停中卡死)+中断,线程应快速退出
            win._on_stop()
            if win._thread is not None and win._thread.isRunning():
                assert win._thread.wait(5000), "thumbnail thread not stopping"
            win.deleteLater()
        fav_store.path.unlink(missing_ok=True)
    print("HdrLibrary window + favorites OK")

    # 灯光赋值目标:LOP domelight 的 punycode 参数匹配 / 过滤器排除 /
    # RenderMan 灯走通用匹配(鸭子类型 fake,无 hou 依赖)
    class _FakeParm:
        def __init__(self, name):
            self._name = name
        def name(self):
            return self._name

    class _FakeNode:
        def __init__(self, type_name, parm_names):
            self._type = type_name
            self._parms = [_FakeParm(n) for n in parm_names]
        def type(self):
            return self
        def name(self):
            return self._type
        def parm(self, name):
            return next((p for p in self._parms if p.name() == name), None)
        def parms(self):
            return self._parms

    lop_node = _FakeNode("domelight", [
        "xn__inputstexturefile_control_shbh", "xn__inputstexturefile_r3ah",
        "xn__inputstextureformat_1kbh",
        "xn__inputskarmalightrecttextureflip_krbff"])
    value, control = hdr_browser._find_lop_texture_parm(lop_node)
    assert value.name() == "xn__inputstexturefile_r3ah", value.name()
    assert control.name() == "xn__inputstexturefile_control_shbh", control.name()
    # 灯光过滤器不接收环境贴图,即便带 map 参数
    assert not hdr_browser.is_light_node(_FakeNode("pxrbarnlightfilter", ["map"]))
    assert hdr_browser.is_light_node(_FakeNode("envlight", ["env_map", "skymap_enable"]))
    assert hdr_browser.is_light_node(_FakeNode("domelight", ["xn__inputstexturefile_r3ah"]))
    assert hdr_browser.find_map_parm(
        _FakeNode("pxrstdenvmaplight", ["ri_envlight", "rman__EnvMap"])) \
        == "rman__EnvMap"
    print("HdrLibrary light mapping OK")

    summary = reloader.reload_all()
    print("reload_all ->", summary)
    assert "FAILED" not in summary, summary

    # Settings round-trip against the real settings/ directory.
    store = houtools.core.settings.JsonStore("_smoke_test.json", defaults={"n": 1})
    store.set("n", 2, save=True)
    assert store.get("n") == 2
    store.path.unlink(missing_ok=True)
    print("settings round-trip OK")

    # 热键自定义存储 round-trip(JsonStore,settings/ 目录)
    from houtools.core import hotkeys
    _sym = "h.pane.wsheet.houtools_paste_as_object_merge"
    hotkeys.set_custom_key(_sym, "Ctrl+Alt+P")
    assert hotkeys.get_custom_key(_sym) == "Ctrl+Alt+P"
    assert hotkeys.get_custom_key("h.houtools.nonexistent") is None
    hotkeys._store().path.unlink(missing_ok=True)
    print("hotkey store round-trip OK")

    print("SMOKE TEST OK")


PROJECT_ROOT_MESSAGE = (
    "PROJECT_ROOT mismatch - check parents[3] in core/constants.py"
)

if __name__ == "__main__":
    main()
