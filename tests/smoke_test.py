"""Headless smoke test: menu XML, package imports, hot reload.

Run with Houdini's Python (no GUI, no Houdini session needed):

    "C:\\Program Files\\Side Effects Software\\Houdini 22.0.429\\python313\\python.exe" tests\\smoke_test.py
"""

import json
import os
import shutil
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

    # Recipe Library:两份菜单均注册薄分发器入口
    assert 'id="houtools.recipe_library"' in main_xml
    assert 'id="houtools.networkview.recipe_library"' in nv_xml
    print("recipe_library menu wiring: consistent")

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
    import houtools.ui.fonts  # noqa: F401
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

    # settings/ 下的应用级配置可读且含有效 dw_exe_path 字段
    # (gitignored 运行时数据;缺失时 ensure 按默认值兜底补建)
    dm.AutomationDataManager.ensure_dw_config()
    shipped = json.loads(
        Path(dm.AutomationDataManager.get_app_config_path()).read_text(
            encoding="utf-8"))
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
            # 打开不自动扫描/生成缩略图：列表为空，手动 reload（=点「刷新」）才开始
            assert win.list.count() == 0, win.list.count()
            win.reload()
            assert win.list.count() == 6, win.list.count()
            # 侧栏：全部 / 收藏 / 未分类(根目录文件) / day / night
            keys = [win.sidebar.item(i).data(QtCore.Qt.UserRole)
                    for i in range(win.sidebar.count())]
            assert keys[0] == hdr_browser.KEY_ALL, keys
            assert keys[1] == hdr_browser.KEY_FAV, keys
            assert sorted(keys[2:]) == ["", "day", "night"], keys
            # 收藏项无名字前缀，角标合成在图标里（_item_icon）；
            # 重建式过滤：条目按当前分类重建，切换视图后旧 item 已失效
            fav_item = next(it for it in (win.list.item(i) for i in range(6))
                            if it.data(QtCore.Qt.UserRole) == target)
            assert fav_item.text() == "a.hdr", fav_item.text()
            base_pm = win._item_icon(target, False).pixmap(1024)
            fav_pm = fav_item.icon().pixmap(1024)
            assert fav_pm.toImage() != base_pm.toImage(), \
                "收藏角标未合成（图标内容与底图一致）"
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

            # 未配置库目录：路径显示为空，刷新只提示不扫描
            empty = hdr_browser._HdrLibraryWindow("")
            assert empty.dir_label.text() == "", empty.dir_label.text()
            empty.reload()
            assert empty.list.count() == 0, empty.list.count()
            assert "更换目录" in empty.status.text(), empty.status.text()
            empty.deleteLater()
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

    # ------------------------------------------------------------------
    # Recipe Library：元数据/文档存储闭环 + 打桩浏览器窗口回归
    # （store 依赖 hou/recipeutils，无头不可调用；窗口 __init__ 不触 hou）
    # ------------------------------------------------------------------
    from PySide6 import QtWidgets
    import houtools.recipelib.metadata as rl_meta
    import houtools.recipelib.store as rl_store
    import houtools.recipelib.docs as rl_docs  # noqa: F401
    import houtools.recipelib.crop as rl_crop
    import houtools.recipelib.browser as rl_browser
    import houtools.tools.recipe_library  # noqa: F401

    rl_store_ps = houtools.core.settings.JsonStore(
        "_smoke_recipelib.json", defaults=dict(rl_meta._DEFAULTS))
    # 上次异常中断可能残留旧状态（文件持久），先清干净
    rl_store_ps.set("favorites", [])
    rl_store_ps.set("tags", {})
    rl_store_ps.set("display_names", {})
    rl_store_ps.set("thumbs", {})
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(rl_meta, "_SETTINGS", rl_store_ps), \
             patch.object(rl_meta, "THUMBS_DIR", Path(tmp) / "thumbs"), \
             patch.object(rl_meta, "DOCS_DIR", Path(tmp) / "docs"):
            name = "houtools::pyro::my_setup"

            # 库文件夹：绝对路径化、去重、保序
            assert rl_meta.set_lib_dirs(
                [" D:/lib_a ", str(Path(tmp) / "lib_b"), "D:/lib_a"]) \
                == [os.path.abspath("D:/lib_a"),
                    os.path.abspath(str(Path(tmp) / "lib_b"))], \
                rl_meta.get_lib_dirs()
            assert rl_meta.get_lib_dirs()[0] == os.path.abspath("D:/lib_a")
            rl_meta.set_lib_dirs([])
            assert rl_meta.get_lib_dirs() == []

            # 库扫描：递归收集 .hda，跳过隐藏目录与 backup 目录、非 .hda
            libtree = Path(tmp) / "scanlib"
            (libtree / "sub").mkdir(parents=True)
            (libtree / "a.hda").touch()
            (libtree / "note.txt").touch()
            (libtree / "sub" / "b.hda").touch()
            (libtree / ".hidden").mkdir()
            (libtree / ".hidden" / "c.hda").touch()
            # saveToolRecipe 会在库文件夹里写 backup/HouToolsRecipes_bakN.hda，
            # 备份里的旧定义不得被加载（已删除的 recipe 会"复活"）
            (libtree / "backup").mkdir()
            (libtree / "backup" / "HouToolsRecipes_bak1.hda").touch()
            found = rl_store.scan_library_files([str(libtree)])
            assert len(found) == 2, found
            assert all(f.endswith(".hda") for f in found)
            assert not any(".hidden" in f or "backup" in f for f in found)

            # 收藏：添加幂等、可移除
            assert not rl_meta.is_favorite(name)
            assert rl_meta.set_favorite(name, True) is True
            assert rl_meta.set_favorite(name, True) is True  # 幂等
            assert rl_meta.is_favorite(name)
            rl_meta.set_favorite(name, False)
            assert not rl_meta.is_favorite(name)

            # 标签：去空白去重，空列表清空条目
            assert rl_meta.set_tags(name, [" pyro ", "常用", "pyro"]) \
                == ["pyro", "常用"]
            assert rl_meta.get_tags(name) == ["pyro", "常用"]
            assert "常用" in rl_meta.all_tags()
            rl_meta.set_tags(name, [])
            assert rl_meta.get_tags(name) == []

            # 自定义显示名（支持中文）：设置/覆盖/清除
            assert rl_meta.get_display_name(name) == ""
            rl_meta.set_display_name(name, "我的 拷贝工具")
            assert rl_meta.get_display_name(name) == "我的 拷贝工具"
            rl_meta.set_display_name(name, "另一个名")
            assert rl_meta.get_display_name(name) == "另一个名"
            rl_meta.set_display_name(name, None)
            assert rl_meta.get_display_name(name) == ""

            # 显示链：官方 label 优先；无 label 回退内部名末段
            # （官方保存时 label 常为空，如 mahx::my_copy_test → my_copy_test）
            assert rl_store.RecipeInfo(
                name="mahx::my_copy_test").display_label == "my_copy_test"
            assert rl_store.RecipeInfo(
                name="houtools::a", label="My Label").display_label \
                == "My Label"

            # 缩略图：按原扩展名落盘，get 返回可用绝对路径，清除同步删文件。
            # 此处缩略图目录被 patch 在插件根外（临时目录）：无法相对化，
            # 存储退回绝对路径
            src = Path(tmp) / "t.gif"
            src.write_bytes(b"GIF89a fake bytes")
            stored = rl_meta.set_thumb_from_file(name, str(src))
            assert Path(stored).exists() and stored.endswith(".gif")
            assert os.path.isabs(rl_meta._SETTINGS.get("thumbs")[name])
            assert rl_meta.get_thumb(name) == stored
            rl_meta.clear_thumb(name)
            assert rl_meta.get_thumb(name) == ""

            # 插件根内：存储相对路径（项目整体挪动后仍有效），get 拼回
            # 绝对；旧版绝对路径迁移成相对且幂等；clear 删相对解析的文件
            with patch.object(rl_meta, "PROJECT_ROOT", Path(tmp)), \
                 patch.object(rl_meta, "THUMBS_DIR",
                              Path(tmp) / "thumbs_in_root"):
                stored = rl_meta.set_thumb_from_file(name, str(src))
                raw = rl_meta._SETTINGS.get("thumbs")[name]
                assert not os.path.isabs(raw) and raw.endswith(".gif")
                assert rl_meta.get_thumb(name) == stored
                legacy = Path(tmp) / "thumbs_in_root" / "legacy.gif"
                shutil.copyfile(str(src), str(legacy))
                rl_meta._SETTINGS.get("thumbs")["legacy"] = str(legacy)
                rl_meta.migrate_legacy_thumb_paths()
                raw2 = rl_meta._SETTINGS.get("thumbs")["legacy"]
                assert not os.path.isabs(raw2) and raw2.endswith(".gif")
                assert rl_meta.get_thumb("legacy")
                rl_meta.clear_thumb("legacy")
                assert not legacy.exists()
                # 裁剪流程：QPixmap 落盘为 PNG（相对路径存储；比例由
                # crop 对话框锁定，存储层只管收图）
                from PySide6 import QtGui
                pm = QtGui.QPixmap(150, 99)
                pm.fill(QtGui.QColor("#36c8b7"))
                stored_pm = rl_meta.set_thumb_from_pixmap(name, pm)
                raw_pm = rl_meta._SETTINGS.get("thumbs")[name]
                assert not os.path.isabs(raw_pm) \
                    and raw_pm.endswith(".png"), raw_pm
                assert Path(stored_pm).exists()
                rl_meta.clear_thumb(name)
                assert not Path(stored_pm).exists()
            rl_meta.clear_thumb(name)
            assert rl_meta.get_thumb(name) == ""

            # 文档：无文档读取为空（不自动建模板）+ 相对引用素材（同名
            # 不覆盖）+ 保存读取 + 目录清理
            assert rl_meta.read_doc(name) == ""
            assert not rl_meta.doc_exists(name)
            # 空白编辑器保存落 0 字节文件：按"没写内容=没文档"处理，
            # 主面板回退官方备注
            rl_meta.write_doc(name, "")
            assert os.path.exists(rl_meta.doc_path(name))
            assert not rl_meta.doc_exists(name)
            rl_meta.write_doc(name, "   \n")
            assert not rl_meta.doc_exists(name)
            asset_src = Path(tmp) / "pic.png"
            asset_src.write_bytes(b"png")
            rel = rl_meta.insert_asset(name, str(asset_src))
            assert rel.startswith("assets/")
            assert (Path(rl_meta.doc_dir(name)) / rel).exists()
            rel2 = rl_meta.insert_asset(name, str(asset_src))
            assert rel2 != rel, "同名素材被覆盖"
            rl_meta.write_doc(name, "# My Setup\n\n用法正文\n")
            assert rl_meta.read_doc(name).startswith("# My Setup")
            rl_meta.delete_doc_dir(name)
            assert not rl_meta.doc_exists(name)

            # 浏览器窗口：list_recipes / selected_nodes / 网络编辑器全部打桩
            infos = [
                rl_store.RecipeInfo(name="houtools::pyro::a", label="Pyro A",
                                    category="tool", submenu="HouTools",
                                    net_category="Sop"),
                rl_store.RecipeInfo(name="houtools::light::b", label="Light B",
                                    category="node", submenu="Lighting",
                                    net_category="Lop"),
                rl_store.RecipeInfo(name="houtools::plain::c", label="Plain C",
                                    category="tool", submenu=""),
            ]
            with patch.object(rl_meta, "get_lib_dirs",
                              return_value=[str(Path(tmp) / "scanlib")]), \
                 patch.object(rl_browser.store, "list_recipes",
                              side_effect=lambda lib_dirs=None:
                                  list(infos) if lib_dirs else []):
                win = rl_browser._RecipeLibraryWindow()
                win.reload()
                # 工具统一字体已应用到窗口树（子控件经继承生效）
                assert win.font().family() == "Alimama ShuHeiTi", \
                    win.font().family()
                assert win.list.count() == 3, win.list.count()

                def _sidebar_keys():
                    # 树侧栏：先序遍历取全部条目的 UserRole 键
                    keys = []

                    def walk(it):
                        keys.append(it.data(0, QtCore.Qt.UserRole))
                        for i in range(it.childCount()):
                            walk(it.child(i))

                    for t in range(win.sidebar.topLevelItemCount()):
                        walk(win.sidebar.topLevelItem(t))
                    return keys

                # 侧栏树结构：全部 / 收藏 / 节点参数 / 子菜单头 / 分类x2
                # （此时尚无标签，不出现标签段头）
                keys = _sidebar_keys()
                assert keys[0] == rl_browser.KEY_ALL, keys
                assert keys[1] == rl_browser.KEY_FAV, keys
                assert "cat::" in keys, keys   # 无 submenu 的归「节点参数」
                assert sorted(k for k in keys
                              if k and k.startswith("cat::")
                              and k != "cat::") \
                    == ["cat::HouTools", "cat::Lighting"], keys
                # 分类过滤
                win._select_category("cat::Lighting")
                assert win.list.count() == 1
                assert win.list.item(0).data(QtCore.Qt.UserRole) \
                    == "houtools::light::b"
                # 节点参数过滤：无 submenu 的 recipe（cat:: 空键）
                win._select_category("cat::")
                assert win.list.count() == 1
                assert win.list.item(0).data(QtCore.Qt.UserRole) \
                    == "houtools::plain::c"
                assert win._category_label() == "节点参数"
                win._select_category(rl_browser.KEY_ALL)
                # 层级过滤：按库内实际出现的 net_category 动态生成
                assert "net::Sop" in keys, keys
                assert "net::Lop" in keys, keys
                win._select_category("net::Sop")
                assert win.list.count() == 1
                assert win.list.item(0).data(QtCore.Qt.UserRole) \
                    == "houtools::pyro::a"
                assert win._category_label() == "Sop"
                win._select_category("net::Lop")
                assert win.list.count() == 1
                win._select_category(rl_browser.KEY_ALL)
                # 收藏过滤：收藏视图只剩 1 条
                rl_meta.set_favorite("houtools::pyro::a", True)
                win._rebuild_sidebar()
                win._select_category(rl_browser.KEY_FAV)
                assert win.list.count() == 1, win.list.count()
                # 搜索命中标签（重建式过滤后须重新取 item）
                rl_meta.set_tags("houtools::light::b", ["夜灯"])
                win._select_category(rl_browser.KEY_ALL)
                win.search.setText("夜灯")
                win._apply_filter()
                assert win.list.count() == 1, win.list.count()
                assert win.list.item(0).data(QtCore.Qt.UserRole) \
                    == "houtools::light::b"
                win.search.setText("")
                win._apply_filter()
                assert win.list.count() == 3
                # 搜索：# 前缀只搜标签（按内容点命中，名称含该词也不算）；
                # 普通词全字段子串；混合词 AND
                rl_meta.set_tags("houtools::light::b", [])   # 清掉旧测试标签
                rl_meta.set_tags("houtools::pyro::a", ["夜灯"])
                win.search.setText("#夜灯")
                win._apply_filter()
                assert win.list.count() == 1, win.list.count()
                assert win.list.item(0).data(QtCore.Qt.UserRole) \
                    == "houtools::pyro::a"
                win.search.setText("#pyro")   # 名称含 pyro，但不是标签
                win._apply_filter()
                assert win.list.count() == 0, win.list.count()
                win.search.setText("pyro #夜灯")   # 混合：全字段 AND 标签
                win._apply_filter()
                assert win.list.count() == 1, win.list.count()
                win.search.setText("#夜灯 常用")   # 标签 AND 全字段（无匹配）
                win._apply_filter()
                assert win.list.count() == 0, win.list.count()
                rl_meta.set_tags("houtools::pyro::a", [])
                win.search.setText("")
                win._apply_filter()
                assert win.list.count() == 3
                # 预览面板联动：选中后名称/元信息/按钮就绪；
                # 名称走显示链，元信息第一行是内部名称
                win.list.setCurrentRow(0)
                assert win.preview_name.text() == "Pyro A", win.preview_name.text()
                assert win.preview_meta.text().startswith(
                    "内部名称: houtools::pyro::a"), win.preview_meta.text()
                assert win.tags_apply_btn.isEnabled()
                # 自定义显示名（中文）覆盖网格文本与搜索；
                # 收藏不再加名字前缀，角标合成在图标里（_icon_for）
                rl_meta.set_display_name("houtools::pyro::a", "火焰·常用")
                win._apply_filter()
                assert win.list.item(0).text() == "火焰·常用", \
                    win.list.item(0).text()
                assert not win.list.item(0).text().startswith("★")
                win.search.setText("火焰")
                win._apply_filter()
                assert win.list.count() == 1
                win.search.setText("")
                win._apply_filter()
                # 重命名流程（_NameDialog 打桩——面板用自建输入框以便
                # 按钮中文化，QInputDialog 会在显示时重置按钮文字）：
                # 改名进元数据并刷新预览，清空输入恢复默认
                win.list.setCurrentRow(1)
                with patch("houtools.recipelib.browser._PromptDialog.exec_",
                           return_value=QtWidgets.QDialog.Accepted), \
                     patch("houtools.recipelib.browser._PromptDialog.text_value",
                           return_value="拷贝神器"):
                    win._rename_selected()
                assert rl_meta.get_display_name("houtools::light::b") \
                    == "拷贝神器"
                assert win.preview_name.text() == "拷贝神器"
                with patch("houtools.recipelib.browser._PromptDialog.exec_",
                           return_value=QtWidgets.QDialog.Accepted), \
                     patch("houtools.recipelib.browser._PromptDialog.text_value",
                           return_value=""):
                    win._rename_selected()
                assert rl_meta.get_display_name("houtools::light::b") == ""
                assert win.preview_name.text() == "Light B"
                # 点空白处取消选中：预览面板复位为空态（meta 卡片保留
                # 版式、值留空——空态与选中态同格式；comment 恢复显示
                # ——否则布局无 stretch 项、控件被拉伸错位）
                win.list.setCurrentRow(0)
                assert win.preview_name.text()
                win.list.emptyClicked.emit()
                assert win.preview_name.text() == "", win.preview_name.text()
                assert win.meta_panel.isVisibleTo(win)
                assert win.preview_meta.text().startswith("内部名称: ")
                assert win.meta_cell_category.text() == "子菜单: "
                assert win.meta_cell_network.text() == "层级: "
                assert win.meta_cell_version.text() == "版本: "
                assert win.meta_cell_targets.text() == "焦点: "
                assert win.preview_comment.isVisibleTo(win)
                assert not win.tags_apply_btn.isEnabled()
                win.list.setCurrentRow(0)
                assert win.tags_apply_btn.isEnabled()
                rl_meta.write_doc("houtools::pyro::a", "# 标题甲\n\n正文乙\n")
                win.list.setCurrentRow(-1)  # 行未变化不触发选中信号，先清再选
                win.list.setCurrentRow(0)
                assert win.preview_doc.isVisibleTo(win), "doc view hidden"
                assert "标题甲" in win.preview_doc.toPlainText()
                assert not win.preview_comment.isVisibleTo(win)
                # 标签设置弹窗：确认后 _apply_filter 须恢复选中态——
                # 否则 _selected_info() 变 None，按钮第二次点击"失灵"
                with patch("houtools.recipelib.browser._PromptDialog.exec_",
                           side_effect=lambda *_a, **_k:
                               QtWidgets.QDialog.Accepted), \
                     patch("houtools.recipelib.browser._PromptDialog.text_value",
                           return_value="夜灯, 常用"):
                    win.tags_apply_btn.click()
                    assert rl_meta.get_tags("houtools::pyro::a") \
                        == ["夜灯", "常用"]
                    win.tags_apply_btn.click()   # 第二次须仍能打开
                assert rl_meta.get_tags("houtools::pyro::a") == ["夜灯", "常用"]
                assert win.tags_view.text() == "#夜灯 #常用"
                assert win.list.currentItem() is not None, "选中态须保留"
                win.list.setCurrentRow(1)
                assert win.preview_comment.isVisibleTo(win)
                assert not win.preview_doc.isVisibleTo(win)
                rl_meta.set_tags("houtools::pyro::a", [])
                rl_meta.delete_doc_dir("houtools::pyro::a")
                rl_meta.set_display_name("houtools::pyro::a", None)
                # 回归：切分类后仍存活的选中必须与预览面板同步——重建
                # 网格时 clear() 发 currentItemChanged(None) 清掉预览，
                # 而选中恢复若不发信号，就出现"卡片高亮但预览显示未选
                # 中、再点同一卡片无效"（current 未变不发信号，只能先
                # 点空白再选）的脱节态
                win._select_category("cat::Lighting")  # light::b 仍在其中
                assert win.list.currentItem() is not None, "存活选中被弄丢"
                assert win.preview_name.text() == "Light B", \
                    win.preview_name.text()
                assert win.tags_apply_btn.isEnabled()
                win._select_category("cat::")  # 选中被过滤掉 → 预览复位
                assert win.list.currentItem() is None
                assert win.preview_name.text() == "", win.preview_name.text()
                win._select_category(rl_browser.KEY_ALL)
                win.list.setCurrentRow(1)
                # reload 重建 _info_by_name（info 全是新对象），恢复选中
                # 后预览须按新数据重刷而不是停在旧内容
                win.reload()
                assert win.list.currentItem() is not None, "reload 后选中丢失"
                assert win.preview_name.text() == "Light B", \
                    win.preview_name.text()
                # 清除缩略图带确认（防误触）：选 No 保留，选 Yes 才真清
                thumb_pm = QtGui.QPixmap(64, 42)
                thumb_pm.fill(QtGui.QColor("#3a7bd5"))
                rl_meta.set_thumb_from_pixmap("houtools::light::b", thumb_pm)
                assert rl_meta.get_thumb("houtools::light::b")
                thumb_item = next(
                    it for it in (win.list.item(i)
                                  for i in range(win.list.count()))
                    if it.data(QtCore.Qt.UserRole) == "houtools::light::b")
                with patch("PySide6.QtWidgets.QMessageBox.exec_",
                           return_value=QtWidgets.QMessageBox.No):
                    win._clear_thumb(infos[1], thumb_item)
                assert rl_meta.get_thumb("houtools::light::b"), \
                    "选「取消」应保留缩略图"
                with patch("PySide6.QtWidgets.QMessageBox.exec_",
                           return_value=QtWidgets.QMessageBox.Yes):
                    win._clear_thumb(infos[1], thumb_item)
                assert not rl_meta.get_thumb("houtools::light::b"), \
                    "选「确认」应清除缩略图"
                assert "houtools::light::b" not in win._thumb_cache
                # 「节点参数」卡片水印：滑块图标铺在卡片最底层（文字区
                # 透出，缩略图盖住上半）；其他类型卡片不画
                def _render_card(info_obj):
                    row = next(
                        i for i in range(win.list.count())
                        if win.list.item(i).data(QtCore.Qt.UserRole)
                        == info_obj.name)
                    gs = win.list.gridSize()
                    img = QtGui.QImage(gs.width(), gs.height(),
                                       QtGui.QImage.Format_ARGB32_Premultiplied)
                    img.fill(QtGui.QColor("#26262b"))
                    p = QtGui.QPainter(img)
                    opt = QtWidgets.QStyleOptionViewItem()
                    opt.rect = QtCore.QRect(0, 0, gs.width(), gs.height())
                    win._card_delegate.paint(
                        p, opt, win.list.model().index(row, 0))
                    p.end()
                    return img

                parm_img = _render_card(infos[2])    # plain::c 无 submenu
                tool_img = _render_card(infos[0])    # pyro::a 有 submenu
                with patch.object(rl_browser._CardDelegate,
                                  "_draw_watermark",
                                  lambda *a, **k: None):
                    parm_nowm = _render_card(infos[2])
                    tool_nowm = _render_card(infos[0])
                assert parm_img != parm_nowm, "节点参数卡应带水印"
                assert tool_img == tool_nowm, "非节点参数卡不应有水印"
                # 应用分发：node 预设无选中 → 引导文案（不触 hou）
                with patch.object(rl_browser.store, "selected_nodes",
                                  return_value=[]):
                    win._apply_recipe(infos[1])
                assert "选中" in win.status.text(), win.status.text()
                # tool 无网络编辑器 → 引导文案
                with patch.object(rl_browser.store, "current_network_editor",
                                  return_value=None):
                    win._apply_recipe(infos[0])
                assert "网络编辑器" in win.status.text(), win.status.text()
                win.deleteLater()

                # 未配置库文件夹：列表为空，状态栏给「库目录」引导
                # （外层 get_lib_dirs 补丁仍在生效，这里覆盖为空）
                rl_meta.set_lib_dirs([])
                with patch.object(rl_meta, "get_lib_dirs", return_value=[]):
                    win2 = rl_browser._RecipeLibraryWindow()
                    win2.reload()
                assert win2.list.count() == 0, win2.list.count()
                assert "库目录" in win2.status.text(), win2.status.text()
                win2.deleteLater()
        rl_store_ps.path.unlink(missing_ok=True)
    print("RecipeLibrary metadata + window OK")

    # 文档编辑器：实例化（无文档=空白）+ 确认/取消模式 + 实时预览渲染
    # （纯 Qt，不触 hou）
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(rl_meta, "DOCS_DIR", Path(tmp) / "docs"):
            info = rl_store.RecipeInfo(name="houtools::x::doc_test",
                                       label="Doc Test", category="tool")
            dlg = rl_docs.DocEditorDialog(None, info)
            assert dlg.editor.toPlainText() == ""  # 无文档 → 空白编辑器
            # 确认/取消按钮中文化（新加按钮条曾漏 localize，固化回归）
            from PySide6 import QtWidgets as _qw
            bb = dlg.findChild(_qw.QDialogButtonBox)
            assert bb.button(_qw.QDialogButtonBox.Ok).text() == "确认", \
                bb.button(_qw.QDialogButtonBox.Ok).text()
            assert bb.button(_qw.QDialogButtonBox.Cancel).text() == "取消"
            dlg.editor.setPlainText("# 标题\n\n正文 **粗体**\n")
            dlg._render_preview()
            assert "标题" in dlg.preview.toPlainText()
            # 确认/取消模式：编辑期间不落盘，确认才写 doc.md
            assert not rl_meta.doc_exists(info.name)
            dlg._confirm_save()
            assert rl_meta.read_doc(info.name) == "# 标题\n\n正文 **粗体**\n"
            dlg.deleteLater()
    print("RecipeLibrary doc editor OK")

    # 裁剪框四角拖动方向（模拟鼠标事件）：往外拉变宽、往里推变窄，
    # 宽高比始终锁定卡片缩略图区比例——方向符号曾整体写反，固化回归
    pm = QtGui.QPixmap(800, 600)
    pm.fill(QtGui.QColor("#204060"))
    canvas = rl_crop._CropCanvas(pm, rl_crop.TARGET_RATIO)
    canvas._sel = QtCore.QRect(150, 100, 300, 198)

    def _drag(corner, to):
        canvas.mousePressEvent(QtGui.QMouseEvent(
            QtCore.QEvent.MouseButtonPress, QtCore.QPointF(corner),
            QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
        canvas.mouseMoveEvent(QtGui.QMouseEvent(
            QtCore.QEvent.MouseMove, QtCore.QPointF(to),
            QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
        canvas.mouseReleaseEvent(QtGui.QMouseEvent(
            QtCore.QEvent.MouseButtonRelease, QtCore.QPointF(to),
            QtCore.Qt.LeftButton, QtCore.Qt.NoButton, QtCore.Qt.NoModifier))

    s = canvas._sel.normalized()
    _drag(QtCore.QPoint(s.left(), s.top()),
          QtCore.QPoint(s.left() - 40, s.top() - 40))
    assert canvas._sel.normalized().width() > s.width(), "tl 往外拉应变宽"
    s = canvas._sel.normalized()
    _drag(QtCore.QPoint(s.right(), s.bottom()),
          QtCore.QPoint(s.right() - 50, s.bottom() - 50))
    assert canvas._sel.normalized().width() < s.width(), "br 往里推应变窄"
    ratio = canvas._sel.normalized().width() / canvas._sel.normalized().height()
    assert abs(ratio - rl_crop.TARGET_RATIO) < 0.02, ratio
    canvas.deleteLater()
    print("RecipeLibrary crop resize direction OK")

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
