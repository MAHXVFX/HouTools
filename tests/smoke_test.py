"""Headless smoke test: menu XML, package imports, hot reload.

Run with Houdini's Python (no GUI, no Houdini session needed):

    "C:\\Program Files\\Side Effects Software\\Houdini 22.0.429\\python313\\python.exe" tests\\smoke_test.py
"""

import atexit
import json
import os
import shutil
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
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
    # 注意：不要试图把 $HFS/bin 加进 PATH 或 QT_PLUGIN_PATH 来补图片编
    # 解码插件（qjpeg）——前者会劫持 PySide6 自己的 Qt DLL 解析直接
    # ImportError，后者加载 qjpeg 仍因依赖解析失败（裸环境限制，GUI
    # 会话环境完整无此问题）；JPEG 相关断言按 QImageWriter 能力分流


# 任一 assert 失败会中断 main()，行内 unlink 全部跳过 —— 统一登记 +
# atexit 兜底清理（含异常退出），防止测试残留污染真实 settings/。
_CLEANUP_FILES = []


def _track_rm(path):
    _CLEANUP_FILES.append(Path(path))


atexit.register(lambda: [p.unlink(missing_ok=True) for p in _CLEANUP_FILES])


def main():
    for menu_file in ("MainMenuCommon.xml", "NetworkViewMenu.xml", "OPmenu.xml"):
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

    # About 页面:仅主菜单注册（全局性条目，不进网络编辑器菜单）；
    # 手册离线可用（无互联网工作站）：样式内联、不加载任何外部资源
    # ——src= 一律禁止、外部样式表(<link>)禁止；指向仓库的导航超链接
    # （<a href="https://...">）不是资源加载，离线打开不受影响，允许
    assert 'id="houtools.about"' in main_xml
    assert 'id="houtools.about"' not in nv_xml
    about_html = ROOT / "docs" / "about.html"
    assert about_html.is_file()
    about_src = about_html.read_text(encoding="utf-8")
    assert "<style>" in about_src
    assert 'src="http' not in about_src
    assert "<link" not in about_src
    assert "gitcode.com/mahx-vfx/HouTools" in about_src
    assert "github.com/MAHXVFX/HouTools" in about_src
    print("about menu wiring: consistent")

    # OPmenu（节点右键菜单）：仓库根随 HOUDINI_PATH 加载；条目走两行分发器，
    # 标签英文/ASCII（H22 实测中文 label 条目行出现但文字不渲染）
    op_xml = (ROOT / "OPmenu.xml").read_text(encoding="utf-8")
    assert 'id="houtools_open_cache_folder"' in op_xml
    assert '_houtools_dispatcher.run("open_cache_folder", kwargs)' in op_xml
    assert '<label>Open Cache Folder [HT]</label>' in op_xml
    assert '<separatorItem/>' in op_xml  # 自有条目与系统项之间有分割线
    print("OPmenu wiring: consistent")

    ET.parse(ROOT / "python_panels" / "Automation.pypanel")
    print("Automation.pypanel: well-formed")

    import houtools
    import houtools.core.constants
    import houtools.core.settings
    import houtools.core.hotkeys
    import houtools.dev.dispatcher  # noqa: F401
    import houtools.tools.paste_as_object_merge
    import houtools.tools.paste_hotkey_settings  # noqa: F401
    import houtools.tools.open_cache_folder  # noqa: F401
    import houtools.tools.about_houtools  # noqa: F401
    import houtools.tools.automation  # noqa: F401
    import houtools.dragdrop  # noqa: F401
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

    # About 页面 run():打桩 os.startfile 验证打开的是 docs/about.html
    # （真开浏览器是无头环境不可接受的外部副作用）
    from unittest.mock import patch
    from houtools.tools import about_houtools as about_mod
    with patch("os.startfile") as fake_start:
        about_mod.run()
        fake_start.assert_called_once_with(str(about_mod.ABOUT_PAGE))
    # 页面缺失分支：日志+状态栏告警，不抛异常、不开浏览器
    with patch.object(about_mod, "ABOUT_PAGE",
                      Path("Z:/nope/about.html")), \
         patch("os.startfile") as fake_start:
        about_mod.run()
        fake_start.assert_not_called()
    print("about_houtools run OK")

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
    ffc = houtools.videoseq.ffmpeg
    found = ffc.find_ffmpeg()
    print("find_ffmpeg ->", found)

    # ffprobe 推导：同目录优先（ffmpeg/ffprobe 配套版本，含 hffmpeg->
    # hffprobe 名字映射），缺失时才回退 $HFS/bin 与 PATH。
    # 临时目录造空文件仿真实布局，断言不依赖环境里装了什么。
    suffix = ".exe" if os.name == "nt" else ""
    ffmpeg_name, ffprobe_name = "ffmpeg" + suffix, "ffprobe" + suffix
    hffmpeg_name, hffprobe_name = "hffmpeg" + suffix, "hffprobe" + suffix
    with tempfile.TemporaryDirectory() as td:
        # 仿 $HFS/bin 布局（hffmpeg + hffprobe，无通用 ffprobe）：
        # hffmpeg -> hffprobe 同目录名字映射命中（既有行为回归）
        hfs_like = os.path.join(td, "hfs_bin")
        os.mkdir(hfs_like)
        for name in (hffmpeg_name, hffprobe_name):
            open(os.path.join(hfs_like, name), "wb").close()
        assert ffc.find_ffprobe(
            os.path.join(hfs_like, hffmpeg_name)) == os.path.join(
                hfs_like, hffprobe_name)

        # 仿完整构建布局（ffmpeg + ffprobe）：同目录同名命中
        full_like = os.path.join(td, "full_bin")
        os.mkdir(full_like)
        for name in (ffmpeg_name, ffprobe_name):
            open(os.path.join(full_like, name), "wb").close()
        assert ffc.find_ffprobe(
            os.path.join(full_like, ffmpeg_name)) == os.path.join(
                full_like, ffprobe_name)

        # 目录里只有 ffmpeg.exe（项目根只放一个文件的场景）：
        # 回退链（$HFS/bin -> PATH）只返回真实存在的文件
        lone_dir = os.path.join(td, "lone")
        os.mkdir(lone_dir)
        open(os.path.join(lone_dir, ffmpeg_name), "wb").close()
        probe = ffc.find_ffprobe(os.path.join(lone_dir, ffmpeg_name))
        assert probe is None or os.path.isfile(probe)
    print("find_ffprobe: same-dir pairing + fallback OK")

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
    # （输入走 ui.dialogs.prompt_text 中文化实例,打桩该助手而非
    # QInputDialog 静态方法）
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(dm.AutomationDataManager, "_hip_base",
                          return_value=tmp), \
             patch("houtools.ui.dialogs.prompt_text",
                   return_value=("MAtest", True)), \
             patch("houtools.ui.dialogs.info", return_value=None):
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

    # 真实实例化 videoseq 窗口（捕获 __init__ 结构损伤；无视频、无线程）
    from houtools.videoseq.window import _VideoToSequenceWindow
    vs_win = _VideoToSequenceWindow()
    vs_win.deleteLater()
    print("VideoseqWindow instantiation OK")

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
        _track_rm(fav_store.path)
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
            # 点空白取消选中：current 连同选中一起清空
            win.list.setCurrentRow(0)
            win.list.emptyClicked.emit()
            assert win.list.currentRow() == -1, win.list.currentRow()
            assert win.list.selectedIndexes() == []
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
    _track_rm(rl_store_ps.path)
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
                # 裁剪流程：QPixmap 落盘（相对路径存储；比例由 crop
                # 对话框锁定，存储层只管收图）；格式尊重源图（jpg→
                # JPEG、png→PNG，webp 等 Qt 不可写的回退 PNG）
                from PySide6 import QtGui
                pm = QtGui.QPixmap(150, 99)
                pm.fill(QtGui.QColor("#36c8b7"))
                assert rl_meta.thumb_format_for_source("a.jpg") == "JPEG"
                assert rl_meta.thumb_format_for_source("b.JPEG") == "JPEG"
                assert rl_meta.thumb_format_for_source("c.png") == "PNG"
                assert rl_meta.thumb_format_for_source("d.webp") == "PNG"
                stored_pm = rl_meta.set_thumb_from_pixmap(name, pm)
                raw_pm = rl_meta._SETTINGS.get("thumbs")[name]
                assert not os.path.isabs(raw_pm) \
                    and raw_pm.endswith(".png"), raw_pm
                # JPEG 落盘按编解码器能力分流：有 qjpeg（GUI 会话）时
                # 断言 .jpg 落盘并清掉旧 PNG（换格式自愈）；裸 python
                # 环境缺插件则保存失败抛 RuntimeError 是预期行为
                _jpg_ok = b"jpeg" in [
                    f.data() for f in
                    QtGui.QImageWriter.supportedImageFormats()]
                if _jpg_ok:
                    stored_jpg = rl_meta.set_thumb_from_pixmap(
                        name, pm, fmt="JPEG")
                    assert stored_jpg.endswith(".jpg"), stored_jpg
                    assert Path(stored_pm).exists() is False
                    assert Path(stored_jpg).exists()
                    rl_meta.clear_thumb(name)
                    assert not Path(stored_jpg).exists()
                else:
                    try:
                        rl_meta.set_thumb_from_pixmap(name, pm, fmt="JPEG")
                        raise AssertionError("无 JPEG 编解码器却保存成功")
                    except RuntimeError:
                        pass
                    rl_meta.clear_thumb(name)
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

            # -------- 导入/导出（transfer）：交换包打包/合并往返 --------
            import houtools.recipelib.transfer as rl_transfer

            with tempfile.TemporaryDirectory() as texp:
                exp_store = houtools.core.settings.JsonStore(
                    "_smoke_transfer_src.json",
                    defaults=dict(rl_meta._DEFAULTS))
                _track_rm(exp_store.path)
                with patch.object(rl_meta, "_SETTINGS", exp_store), \
                     patch.object(rl_meta, "THUMBS_DIR",
                                  Path(texp) / "thumbs"), \
                     patch.object(rl_meta, "DOCS_DIR",
                                  Path(texp) / "docs"):
                    lib_dir = Path(texp) / "lib"
                    lib_dir.mkdir()
                    (lib_dir / "a.hda").write_bytes(b"fake-hda-a" * 10)
                    (lib_dir / "c.hda").write_bytes(b"fake-hda-c" * 10)
                    pack_infos = [
                        rl_store.RecipeInfo(name="mahx::alpha",
                                            category="tool",
                                            library=str(lib_dir / "a.hda")),
                        rl_store.RecipeInfo(name="mahx::beta",
                                            category="tool",
                                            library=str(lib_dir / "a.hda")),
                        rl_store.RecipeInfo(name="mahx::gamma",
                                            category="tool",
                                            library=str(lib_dir / "c.hda")),
                    ]
                    # 积累：收藏/标签/显示名/颜色/缩略图/文档（含 asset）
                    rl_meta.set_favorite("mahx::alpha", True)
                    rl_meta.set_tags("mahx::alpha", ["常用", "pyro"])
                    rl_meta.set_display_name("mahx::alpha", "阿尔法")
                    rl_meta.set_color("mahx::alpha", "#8a5cf5")
                    fake_img = Path(texp) / "src_img.png"
                    fake_img.write_bytes(b"\x89PNG-not-really")
                    rl_meta.set_thumb_from_file("mahx::alpha", str(fake_img))
                    rl_meta.set_tags("mahx::gamma", ["旧标签"])
                    # 陈旧条目（recipe 已不存在）：不得被导出
                    rl_meta.set_tags("mahx::ghost", ["幽灵"])
                    asset_src = Path(texp) / "pic.png"
                    asset_src.write_bytes(b"asset-bytes")
                    rl_meta.write_doc("mahx::alpha",
                                      "# 阿尔法\n![图](assets/pic.png)")
                    rl_meta.insert_asset("mahx::alpha", str(asset_src))

                    zip_path = str(Path(texp) / "out" / "pack.zip")
                    summary = rl_transfer.export_recipes(
                        pack_infos, zip_path, include_hda=True)
                    assert summary["recipes"] == 3 and summary["hda"] == 2 \
                        and summary["docs"] == 1 and summary["thumbs"] == 1, \
                        summary
                    with zipfile.ZipFile(zip_path) as zf:
                        arcs = set(zf.namelist())
                        assert "manifest.json" in arcs
                        assert "recipelib.json" in arcs
                        assert "thumbs/mahx__alpha.png" in arcs
                        assert "docs/mahx__alpha/doc.md" in arcs
                        assert "docs/mahx__alpha/assets/pic.png" in arcs
                        assert "recipes/a.hda" in arcs
                        assert "recipes/c.hda" in arcs
                        man = json.loads(zf.read("manifest.json"))
                        assert man["hda_files"]["recipes/a.hda"]["recipes"] \
                            == ["mahx::alpha", "mahx::beta"], man
                        packed = json.loads(zf.read("recipelib.json"))
                        assert packed["thumbs"]["mahx::alpha"] \
                            == "thumbs/mahx__alpha.png"
                        assert "mahx::ghost" not in packed["tags"]
                        assert packed["favorites"] == ["mahx::alpha"]
                    # 包内有 .hda 文件却不给目标目录 → 报错而非半途落盘
                    try:
                        rl_transfer.import_package(zip_path)
                        raise AssertionError("missing lib_dir should fail")
                    except rl_transfer.TransferError:
                        pass

                    # 不带 .hda 的导出：无 recipes/ 成员
                    zip_meta_only = str(Path(texp) / "meta_only.zip")
                    rl_transfer.export_recipes(pack_infos, zip_meta_only,
                                               include_hda=False)
                    with zipfile.ZipFile(zip_meta_only) as zf:
                        assert not any(n.startswith("recipes/")
                                       for n in zf.namelist())

                    # 空 recipes 拒绝导出
                    try:
                        rl_transfer.export_recipes([], zip_path)
                        raise AssertionError("empty export should fail")
                    except rl_transfer.TransferError:
                        pass

                    # 取消：抛 TransferCancelled，目标 zip 与半成品均不存在
                    cancel_path = str(Path(texp) / "cancelled.zip")
                    try:
                        rl_transfer.export_recipes(
                            pack_infos, cancel_path,
                            progress=lambda *a: True)
                        raise AssertionError("cancel not raised")
                    except rl_transfer.TransferCancelled:
                        pass
                    assert not os.path.exists(cancel_path)
                    assert not os.path.exists(cancel_path + ".~part")

                    # ---- 导入到全新环境（模拟另一台机器） ----
                    with tempfile.TemporaryDirectory() as timp:
                        dst_store = houtools.core.settings.JsonStore(
                            "_smoke_transfer_dst.json",
                            defaults=dict(rl_meta._DEFAULTS))
                        _track_rm(dst_store.path)
                        with patch.object(rl_meta, "_SETTINGS", dst_store), \
                             patch.object(rl_meta, "THUMBS_DIR",
                                          Path(timp) / "thumbs"), \
                             patch.object(rl_meta, "DOCS_DIR",
                                          Path(timp) / "docs"):
                            target_lib = Path(timp) / "newlib"
                            # 本地已有同名 gamma（c.hda 应整体跳过）与
                            # 包里没有的 delta（其颜色必须保留）
                            rl_meta.set_tags("mahx::gamma", ["本地标签"])
                            rl_meta.set_color("mahx::delta", "#111111")
                            s = rl_transfer.inspect_package(
                                zip_path, existing_names={"mahx::gamma"})
                            assert s["conflicts"] == ["mahx::gamma"]
                            assert [a for a, _o in s["hda_import"]] \
                                == ["recipes/a.hda"]
                            assert [a for a, _r in s["hda_skip"]] \
                                == ["recipes/c.hda"]
                            result = rl_transfer.import_package(
                                zip_path, lib_dir=str(target_lib),
                                existing_names={"mahx::gamma"})
                            assert result["docs"] == 1
                            assert result["thumbs"] == 1
                            assert result["hda_copied"] == ["a.hda"]
                            assert [a for a, _r in result["hda_skipped"]] \
                                == ["recipes/c.hda"]
                            # .hda：新配方所在文件已复制，冲突文件未复制
                            assert (target_lib / "a.hda").is_file()
                            assert not (target_lib / "c.hda").exists()
                            # 元数据：包内覆盖同名，本地独有保留
                            assert rl_meta.get_tags("mahx::alpha") \
                                == ["常用", "pyro"]
                            assert rl_meta.get_display_name("mahx::alpha") \
                                == "阿尔法"
                            assert rl_meta.get_color("mahx::alpha") \
                                == "#8a5cf5"
                            assert rl_meta.is_favorite("mahx::alpha")
                            assert rl_meta.get_tags("mahx::gamma") \
                                == ["旧标签"], "包内条目应覆盖本地同名"
                            assert rl_meta.get_color("mahx::delta") \
                                == "#111111", "本地独有条目不得被动"
                            # 缩略图：文件落到（patch 过的）缩略图目录，
                            # 记录为相对插件根的存储形式
                            assert (Path(timp) / "thumbs"
                                    / "mahx__alpha.png").is_file()
                            assert dst_store.get("thumbs")["mahx::alpha"] \
                                == os.path.join("settings", "recipe_thumbs",
                                                "mahx__alpha.png")
                            # 文档：整目录替换（含 asset），正文可读
                            assert rl_meta.doc_exists("mahx::alpha")
                            assert "阿尔法" in rl_meta.read_doc("mahx::alpha")
                            assert (Path(rl_meta.doc_dir("mahx::alpha"))
                                    / "assets" / "pic.png").is_file()

                    # 坏包（无 manifest 的合法 zip）拒绝并给出中文原因
                    bad = Path(texp) / "bad.zip"
                    with zipfile.ZipFile(str(bad), "w") as zf:
                        zf.writestr("hello.txt", "x")
                    try:
                        rl_transfer.inspect_package(str(bad))
                        raise AssertionError("bad package should fail")
                    except rl_transfer.TransferError as exc:
                        assert "manifest" in str(exc)

                    # zip-slip 防护：manifest 的 original 由包作者任意
                    # 伪造，../ 相对路径与绝对路径都只取文件名
                    hda_import, _skip = rl_transfer._hda_plan(
                        {"hda_files": {"recipes/a.hda": {
                            "original": "../../evil.hda",
                            "recipes": ["mahx::nope"]}}},
                        {"recipes/a.hda"}, set())
                    assert hda_import[0][1] == "evil.hda", hda_import
                    hda_import, _skip = rl_transfer._hda_plan(
                        {"hda_files": {"recipes/a.hda": {
                            "original": "C:/abs/evil2.hda",
                            "recipes": ["mahx::nope"]}}},
                        {"recipes/a.hda"}, set())
                    assert hda_import[0][1] == "evil2.hda", hda_import
                    print("transfer zip-slip guard OK")

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
                # 初始焦点在侧栏而非搜索框（用户不期望搜索框预激活；
                # 对隐藏窗口 setFocus 即设定激活时的焦点控件）
                assert win.focusWidget() is win.sidebar, win.focusWidget()
                # 首次 show（模拟打开）：焦点不得落到网格——QAbstractItemView
                # 拿键盘焦点且 current 无效时会把首行设为 current（不选中），
                # 曾表现为"预览显示第一卡但卡片无高亮"
                win.show()
                app.processEvents()
                assert win.focusWidget() is win.sidebar, win.focusWidget()
                assert win.list.currentRow() == -1, win.list.currentRow()
                assert win.list.selectedIndexes() == []
                assert win.preview_name.text() == "", win.preview_name.text()
                # 回归：网格在无选中时收到焦点建立事件（真实场景=上次
                # 关闭前点过空白即"网格持焦且 current 已清"，重开面板时
                # 窗口激活把焦点恢复给网格），Qt 会把首行设为 current
                # （不选中），曾表现为"预览显示第一卡但卡片无高亮"；
                # _Grid.focusInEvent 须把无选中的 current 回退。offscreen
                # 下首次 show 后窗口处于激活态，setFocus 即投递焦点事件
                win.list.setCurrentRow(0)
                assert win.preview_name.text()
                win.list.emptyClicked.emit()   # 点空白：current 清空
                assert win.list.currentRow() == -1
                assert win.preview_name.text() == ""
                win.list.setFocus()
                app.processEvents()
                assert win.list.currentRow() == -1, win.list.currentRow()
                assert win.list.selectedIndexes() == []
                assert win.preview_name.text() == "", win.preview_name.text()
                win.hide()
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
                # 「常规」设置：全部视图隐藏「节点参数」类型（默认不勾选；
                # 勾选即时生效——KEY_ALL 过滤 + 侧栏全部行计数同步扣减，
                # 「节点参数」等其余分组不受影响）。_UI_SETTINGS 打桩到
                # 临时 store，不写真实 settings/
                gen_store = houtools.core.settings.JsonStore(
                    "_smoke_rl_general.json",
                    defaults={"hide_nodeparm_in_all": False})
                _track_rm(gen_store.path)
                with patch.object(rl_browser, "_UI_SETTINGS", gen_store):
                    win._select_category(rl_browser.KEY_ALL)
                    assert win.list.count() == 3, win.list.count()
                    win._apply_general_settings(True)
                    assert win.list.count() == 2, win.list.count()
                    names = {win.list.item(i).data(QtCore.Qt.UserRole)
                             for i in range(win.list.count())}
                    assert "houtools::plain::c" not in names, names
                    all_item = win.sidebar.topLevelItem(0)
                    assert all_item.data(0, QtCore.Qt.UserRole) \
                        == rl_browser.KEY_ALL
                    assert all_item.data(0, rl_browser.SIDEBAR_COUNT_ROLE) \
                        == 2, all_item.data(0, rl_browser.SIDEBAR_COUNT_ROLE)
                    win._select_category("cat::")   # 节点参数分组照常显示
                    assert win.list.count() == 1, win.list.count()
                    win._select_category(rl_browser.KEY_ALL)
                    win._apply_general_settings(False)
                    assert win.list.count() == 3, win.list.count()

                    # 设置对话框：三页（库目录/常规/数据）；常规页勾选文案
                    # 带「全部」行同款 grid 图标，勾选切换即发回调
                    gen_pages = []
                    dlg = rl_browser._SettingsDialog(
                        win, on_lib_dirs_changed=lambda _d: None,
                        on_general_changed=gen_pages.append,
                        on_export=None, on_import=None)
                    assert [dlg.cats.item(i).text() for i in range(3)] \
                        == ["库目录", "常规", "数据"]
                    page = dlg.stack.widget(1)
                    assert not page.icon_lbl.pixmap().isNull()
                    page.hide_chk.setChecked(True)
                    assert gen_pages == [True], gen_pages
                    page.hide_chk.setChecked(False)
                    assert gen_pages == [True, False], gen_pages
                    dlg.deleteLater()
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
    # 白边框/三分线须真的画出来（曾因 setPen(NoPen) 后 pen() 改色样式
    # 不变，整段笔画静默不可见）；用直接设定的确定坐标渲染断言。grab
    # 输出是设备像素（真实平台下屏幕 DPR≠1），按 DPR 映射后 ±1px 窗口
    # 取最大（分数缩放下笔画覆盖像素可能不满）
    cimg = canvas.grab().toImage()
    cs = canvas._sel.normalized()
    cmid = (cs.top() + cs.bottom()) // 2
    _dpr = cimg.devicePixelRatio() or 1.0

    def _stroke_max(x, y):
        best = 0
        for ddx in (-1, 0, 1):
            for ddy in (-1, 0, 1):
                xi = int(round(x * _dpr)) + ddx
                yi = int(round(y * _dpr)) + ddy
                if 0 <= xi < cimg.width() and 0 <= yi < cimg.height():
                    best = max(best, cimg.pixelColor(xi, yi).value())
        return best

    assert _stroke_max(cs.left(), cmid) > 200, "选框左边框不可见"
    assert _stroke_max((cs.left() + cs.right()) // 2,
                       cs.top()) > 200, "选框顶边框不可见"
    assert _stroke_max(cs.left() + cs.width() // 3,
                       cmid) > 110, "三分构图线不可见"

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

    # 截取缩略图遮罩（capture.SnipOverlay）：比例锁定框选 + Enter 确认 /
    # Esc 取消，无头模拟鼠标/键盘事件。语义固化：松手不确认、无选区
    # Enter 忽略、框内拖动移动、四角手柄按比例缩放、确认裁剪 ×DPR 回
    # 物理像素且 DPR 重置 1
    from PySide6 import QtGui
    from houtools.recipelib.capture import SnipOverlay
    snip_src = QtGui.QPixmap(400, 300)
    snip_src.fill(QtGui.QColor("#306040"))
    ov = SnipOverlay(QtGui.QGuiApplication.primaryScreen(), snip_src)
    snipped, cancelled = [], []
    ov.confirmed.connect(snipped.append)
    ov.cancelled.connect(lambda: cancelled.append(1))

    def _spress(p):
        ov.mousePressEvent(QtGui.QMouseEvent(
            QtCore.QEvent.MouseButtonPress, QtCore.QPointF(p),
            QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))

    def _smove(p):
        ov.mouseMoveEvent(QtGui.QMouseEvent(
            QtCore.QEvent.MouseMove, QtCore.QPointF(p),
            QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))

    def _srel(p):
        ov.mouseReleaseEvent(QtGui.QMouseEvent(
            QtCore.QEvent.MouseButtonRelease, QtCore.QPointF(p),
            QtCore.Qt.LeftButton, QtCore.Qt.NoButton, QtCore.Qt.NoModifier))

    # 无选区：Enter 忽略、Esc 取消（遮罩不自我关闭，收尾归调用方）
    ov.keyPressEvent(QtGui.QKeyEvent(
        QtCore.QEvent.KeyPress, QtCore.Qt.Key_Return, QtCore.Qt.NoModifier))
    assert not snipped and not cancelled
    ov.keyPressEvent(QtGui.QKeyEvent(
        QtCore.QEvent.KeyPress, QtCore.Qt.Key_Escape, QtCore.Qt.NoModifier))
    assert cancelled and not snipped
    # 拖拽出锁比例选框
    _spress(QtCore.QPoint(60, 50))
    assert ov._sel is None, "框外按下即清旧选框，随 move 重新生成"
    _smove(QtCore.QPoint(260, 200))
    _srel(QtCore.QPoint(260, 200))
    sel = ov._sel
    assert sel is not None and sel.width() >= 40, sel
    assert abs(sel.width() / sel.height() - rl_crop.TARGET_RATIO) < 0.02, sel
    # 选框夹屏内
    sr = ov.rect()
    assert sr.contains(sel), (sel, sr)
    # 框内拖动 = 移动选框（对齐 ThumbCropDialog 手感）
    c = sel.center()
    _spress(c)
    _smove(c + QtCore.QPoint(30, 20))
    _srel(c + QtCore.QPoint(30, 20))
    moved = ov._sel
    assert moved.size() == sel.size(), (moved, sel)
    assert moved.topLeft() == sel.topLeft() + QtCore.QPoint(30, 20), \
        (moved, sel)
    # 四角手柄：br 往里推变窄、往外拉变宽，比例始终锁定
    _spress(QtCore.QPoint(moved.right(), moved.bottom()))
    _smove(QtCore.QPoint(moved.right() - 50, moved.bottom() - 50))
    _srel(QtCore.QPoint(moved.right() - 50, moved.bottom() - 50))
    shrunk = ov._sel
    assert shrunk.width() < moved.width(), (shrunk, moved)
    assert abs(shrunk.width() / shrunk.height()
               - rl_crop.TARGET_RATIO) < 0.02, shrunk
    _spress(QtCore.QPoint(shrunk.right(), shrunk.bottom()))
    _smove(QtCore.QPoint(shrunk.right() + 40, shrunk.bottom() + 40))
    _srel(QtCore.QPoint(shrunk.right() + 40, shrunk.bottom() + 40))
    grown = ov._sel
    assert grown.width() > shrunk.width(), (grown, shrunk)
    # 悬停光标：框内移动十字、角上缩放斜箭头
    _smove(grown.center())
    assert ov.cursor().shape() == QtCore.Qt.SizeAllCursor
    _smove(QtCore.QPoint(grown.left(), grown.top()))
    assert ov.cursor().shape() == QtCore.Qt.SizeFDiagCursor
    ov.grab()   # 完整绘制路径（压暗/边框/三分线/手柄/尺寸条/提示条）不崩溃
    # 松手不确认；Enter 才裁剪（×DPR 裁剪、DPR 重置回纯像素）
    assert not snipped
    ov.keyPressEvent(QtGui.QKeyEvent(
        QtCore.QEvent.KeyPress, QtCore.Qt.Key_Return, QtCore.Qt.NoModifier))
    assert len(snipped) == 1
    got = snipped[0]
    assert got.size() == ov._sel.size(), (got.size(), ov._sel.size())
    assert got.devicePixelRatio() == 1.0
    assert got.toImage().pixelColor(0, 0).name() == "#306040"
    ov.deleteLater()
    print("RecipeLibrary snip overlay OK")

    summary = reloader.reload_all()
    print("reload_all ->", summary)
    assert "FAILED" not in summary, summary

    # Settings round-trip against the real settings/ directory.
    store = houtools.core.settings.JsonStore("_smoke_test.json", defaults={"n": 1})
    _track_rm(store.path)
    store.set("n", 2, save=True)
    assert store.get("n") == 2
    store.path.unlink(missing_ok=True)
    # 原子写：保存后无 .~tmp 残留（写盘中途崩溃不截断原文件）
    assert not store.path.with_name(store.path.name + ".~tmp").exists()
    print("settings round-trip OK")

    # 损坏的设置文件：读取失败时留底 .bak 再回退默认（防止下次 save
    # 把尚可抢救的内容直接覆盖掉）
    corrupt = houtools.core.settings.JsonStore(
        "_smoke_corrupt.json", defaults={"n": 1})
    _track_rm(corrupt.path)
    corrupt.path.write_text("{not json", encoding="utf-8")
    corrupt.load()
    assert corrupt.get("n") == 1
    assert corrupt.path.with_name(corrupt.path.name + ".bak").exists(), \
        "损坏文件未留底 .bak"
    corrupt.path.with_name(corrupt.path.name + ".bak").unlink(missing_ok=True)
    corrupt.path.unlink(missing_ok=True)
    print("corrupt settings backup OK")

    # Automation 配置（get_data_path 打桩到临时目录）：save()/save_settings()
    # 读到损坏文件时先留底 .bak 再覆盖，原内容不随写入丢失；load()/
    # load_settings() 对合法 JSON 但错误 schema 容错——顶层非对象留底、
    # 字段类型不符按空处理、非 dict 任务条目丢弃
    from houtools.automation import data_manager as auto_dm
    auto_dir = Path(tempfile.mkdtemp(prefix="houtools_smoke_auto_"))
    atexit.register(shutil.rmtree, auto_dir, ignore_errors=True)
    auto_path = auto_dir / "Automation.json"
    with patch.object(
            auto_dm.AutomationDataManager, "get_data_path",
            staticmethod(lambda filename=None: str(auto_path))):
        # save 读损坏文件 → 留底 .bak，新文件正常写入
        auto_path.write_text('{"tasks": [', encoding="utf-8")
        assert auto_dm.AutomationDataManager.save([{"type": "BUTTON_CLICK"}]), \
            "save 应成功"
        auto_bak = auto_path.with_name(auto_path.name + ".bak")
        assert auto_bak.exists(), "损坏文件未留底 .bak"
        assert auto_bak.read_text(encoding="utf-8") == '{"tasks": [', \
            ".bak 应保留损坏原件"
        assert auto_dm.AutomationDataManager.load() == [
            {"type": "BUTTON_CLICK"}]

        # 顶层不是对象 → 留底 + 按空配置
        auto_bak.unlink()
        auto_path.write_text('["not", "dict"]', encoding="utf-8")
        assert auto_dm.AutomationDataManager.load() == []
        assert auto_bak.exists(), "顶层非对象的文件未留底"

        # tasks 字段类型错误 / 混入非对象元素 → 容错不崩
        # （上一步 load 留底已把原文件改名，这里补写新内容）
        auto_path.unlink(missing_ok=True)
        auto_path.write_text('{"tasks": "abc"}', encoding="utf-8")
        assert auto_dm.AutomationDataManager.load() == [], \
            "tasks 非列表应返回空"
        auto_path.write_text(
            '{"tasks": ["x", {"type": "BUTTON_CLICK"}, 3]}', encoding="utf-8")
        assert auto_dm.AutomationDataManager.load() == [{"type": "BUTTON_CLICK"}]

        # settings 同套语义
        auto_path.write_text('{"settings": ["bad"]}', encoding="utf-8")
        assert auto_dm.AutomationDataManager.load_settings() == {}
        auto_path.write_text('{"settings": {"log_to_disk": true}}',
                             encoding="utf-8")
        assert auto_dm.AutomationDataManager.load_settings() == {
            "log_to_disk": True}

        # save_settings 读损坏文件同样先留底
        auto_bak.unlink()
        auto_path.write_text('{"tasks": [}', encoding="utf-8")
        assert auto_dm.AutomationDataManager.save_settings(
            {"log_to_disk": True}), "save_settings 应成功"
        assert auto_bak.exists(), "save_settings 未留底损坏文件"
    print("automation corrupt backup + schema guard OK")

    # 视频转序列图：输出前缀拒绝路径分隔符等 Windows 非法字符
    from houtools.videoseq.window import prefix_error
    assert prefix_error("cam") == ""
    assert prefix_error("cam_01-2.3") == ""
    assert "路径分隔符" in prefix_error("../evil")
    assert "路径分隔符" in prefix_error("a\\b")
    assert "冒号" in prefix_error("C:evil")
    assert prefix_error('a"b') != ""
    print("videoseq prefix guard OK")

    # 打开缓存文件夹：sopoutput 求值取父目录 + 目录缺失上溯最近存在祖先
    # （fake 节点鸭子类型，无 hou 依赖；相对路径的 $HIP 分支需 hou，无头不覆盖）
    from houtools.tools import open_cache_folder as ocf

    class _FakeParmEval:
        def __init__(self, value):
            self._value = value
        def evalAsString(self):
            return self._value

    class _FakeCacheNode:
        def __init__(self, path, sopoutput=None):
            self._path = path
            self._sopoutput = sopoutput
        def path(self):
            return self._path
        def parm(self, name):
            if name == "sopoutput" and self._sopoutput is not None:
                return _FakeParmEval(self._sopoutput)
            return None

    with tempfile.TemporaryDirectory() as tmp:
        good = Path(tmp, "geo", "cache_v1")
        good.mkdir(parents=True)
        node = _FakeCacheNode("/obj/geo1/filecache1",
                              str(good / "out.$F4.bgeo.sc"))
        assert os.path.normpath(ocf.resolve_folder(node)) == str(good)
        node = _FakeCacheNode("/obj/geo1/filecache1",
                              str(Path(tmp, "no", "such") / "f.bgeo.sc"))
        assert os.path.normpath(ocf.resolve_folder(node)) \
            == os.path.normpath(str(tmp))
    try:
        ocf.resolve_folder(_FakeCacheNode("/obj/geo1/box1"))
        raise AssertionError("missing sopoutput should fail")
    except RuntimeError as exc:
        assert "sopoutput" in str(exc)
    print("open_cache_folder resolve OK")

    # 外部拖放导入（dragdrop）：.abc 过滤 / file:// URL 解码 / 节点名清洗
    # （纯函数，无 hou 依赖）；无 .abc 早退分支不触 hou；无 hou 环境（或
    # 任何异常）也不裸抛——吞掉并接管，防 .abc 被原生当 hip 文件打开
    from houtools import dragdrop as dd
    assert dd._extract_abc_files([]) == []
    assert dd._extract_abc_files(["a.txt", "b.hip"]) == []
    assert dd._extract_abc_files(["x.abc", "y.ABC", "z.txt"]) \
        == ["x.abc", "y.ABC"]
    assert dd._extract_abc_files(["file:///C:/lib/a%20b.abc"]) \
        == ["C:\\lib\\a b.abc"]
    assert dd._clean_name("cam.abc") == "cam"
    assert dd._clean_name("角色A_v2.abc") == "A_v2"
    assert dd._clean_name("my big shot!.abc") == "my_big_shot"
    assert dd._clean_name("123.abc") == "abc_123"
    assert dd._clean_name("###.abc") == "abc_import"
    assert dd.drop_accept(["a.txt"]) is False
    assert dd.drop_accept([]) is False
    assert dd.drop_accept(["x.abc"]) is True
    print("dragdrop abc import guard OK")

    # 热键自定义存储 round-trip（打桩到临时 store，不触真实 settings/；
    # 此前直接删除真实 hotkeys.json，会把用户自定义键位一起删掉）
    from houtools.core import hotkeys
    _sym = "h.pane.wsheet.houtools_paste_as_object_merge"
    _hotkey_store = houtools.core.settings.JsonStore(
        "_smoke_hotkeys.json", defaults={})
    _track_rm(_hotkey_store.path)
    with patch.object(hotkeys, "_STORE", _hotkey_store):
        hotkeys.set_custom_key(_sym, "Ctrl+Alt+P")
        assert hotkeys.get_custom_key(_sym) == "Ctrl+Alt+P"
        assert hotkeys.get_custom_key("h.houtools.nonexistent") is None
        hotkeys.clear_custom_key(_sym)
        assert hotkeys.get_custom_key(_sym) is None, "清除自定义键位失败"
    print("hotkey store round-trip OK")

    print("SMOKE TEST OK")


PROJECT_ROOT_MESSAGE = (
    "PROJECT_ROOT mismatch - check parents[3] in core/constants.py"
)

if __name__ == "__main__":
    main()
