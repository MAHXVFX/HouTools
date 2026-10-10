"""Generate real-window screenshots for the About page carousel (offline).

Runs headless (QT_QPA_PLATFORM=offscreen) with Houdini's bundled Python.
Instantiates the four tool windows with stubbed demo data and grabs each
to docs/about_shots/*.png. Never touches the real settings/ (all stores
are patched to temp dirs); the user's HDR library is only READ (files
copied out to a temp dir). Re-run whenever a tool window's UI changes.

Usage:
    "C:/.../python313/python.exe" tests/gen_about_shots.py
"""

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python3.13libs"))


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
    raise SystemExit("Houdini 22.0 python313 not found")


HOUDINI_ROOT = _find_houdini_root()
_pyside_dir = HOUDINI_ROOT / "python313" / "lib" / "site-packages-forced"
if _pyside_dir.exists():
    sys.path.append(str(_pyside_dir))
_qt_bin = HOUDINI_ROOT / "bin"
if _qt_bin.exists():
    os.add_dll_directory(str(_qt_bin))

OUT_DIR = ROOT / "docs" / "about_shots"
OUT_DIR.mkdir(parents=True, exist_ok=True)
SHOT_W, SHOT_H = 1080, 620

from PySide6 import QtGui, QtWidgets  # noqa: E402
from unittest.mock import patch  # noqa: E402

import houtools.core.settings  # noqa: E402


def _settle(win, ms=350):
    """Pump the event loop so the window lays out and paints fully."""
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.02)
    QtWidgets.QApplication.processEvents()


def _grab(win, name):
    _settle(win)
    pm = win.grab()
    out = OUT_DIR / name
    ok = pm.save(str(out), "PNG")
    print("shot %-14s %sx%s -> %s (%s)" % (
        name, pm.width(), pm.height(), out, "ok" if ok else "FAIL"))
    return ok


# -------------------------------------------------------------------
# 1) Automation — temp config with three demo tasks
# -------------------------------------------------------------------
def shot_automation(tmp: Path):
    from houtools.automation import data_manager as dm
    from houtools.automation.window import AutomationWindow

    cfg_dir = tmp / "HouTools_cfg" / "Automation_json"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    tasks = {
        "tasks": [
            {"type": "BUTTON_CLICK",
             "params": {"node_path": "/obj/cache_ropnet1",
                        "parm_name": "execute"},
             "enabled": True},
            {"type": "FLIPBOOK",
             "params": {"start_frame": "1001", "end_frame": "1080",
                        "output_path": "$HIP/flipbook/$HIPNAME.$F4.jpg",
                        "save_to_disk": True},
             "enabled": True},
            {"type": "OPEN_DW", "params": {}, "enabled": False},
        ]
    }
    (cfg_dir / "Automation.json").write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")

    with patch.object(dm.AutomationDataManager, "_hip_base",
                      classmethod(lambda cls: str(tmp))):
        win = AutomationWindow()
        win.resize(SHOT_W, SHOT_H)
        win.show()
        _settle(win, 500)
        ok = _grab(win, "automation.png")
        win.deleteLater()
    return ok


# -------------------------------------------------------------------
# 2) Video to Sequence — plain instantiation
# -------------------------------------------------------------------
def shot_videoseq(tmp: Path):
    from houtools.videoseq.window import _VideoToSequenceWindow

    win = _VideoToSequenceWindow()
    win.resize(SHOT_W, SHOT_H)
    win.show()
    _settle(win, 400)
    ok = _grab(win, "videoseq.png")
    win.deleteLater()
    return ok


# -------------------------------------------------------------------
# 3) HDR Library — copies from the user's real lib (read-only) into a
#    temp lib, real hoiiotool thumbnails generated in-process
# -------------------------------------------------------------------
def _collect_user_hdrs():
    try:
        cfg = json.loads((ROOT / "settings" / "hdr_library.json")
                         .read_text(encoding="utf-8"))
    except OSError:
        return []
    lib = Path(cfg.get("lib_dir") or "")
    if not lib.is_dir():
        return []
    found = []
    for p in sorted(lib.rglob("*")):
        if p.suffix.lower() in (".hdr", ".hdri", ".exr") and p.is_file():
            rel = p.relative_to(lib)
            cat = rel.parts[0] if len(rel.parts) > 1 else ""
            found.append((p, cat))
    return found


def shot_hdr(tmp: Path):
    from houtools.core.settings import JsonStore
    from houtools.hdrlight import browser as hdr_browser

    picks = _collect_user_hdrs()
    if not picks:
        print("shot hdr SKIP: no HDR files in user lib")
        return False
    # 组间轮转取 6 个,保证侧栏出现多个分类
    by_cat = {}
    for p, c in picks:
        by_cat.setdefault(c, []).append(p)
    ordered, cats = [], list(by_cat)
    while len(ordered) < 6 and any(by_cat[c] for c in cats):
        for c in cats:
            if by_cat[c] and len(ordered) < 6:
                ordered.append((c, by_cat[c].pop(0)))
    lib = tmp / "hdr_lib"
    for c, p in ordered:
        dest = lib / c / p.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)

    store = JsonStore("_about_shots_hdr.json", defaults={
        "lib_dir": str(lib), "favorites": [], "thumb_size": 151,
        "pin_on_top": False})
    # offscreen 裸环境缺 qjpeg 插件(与 smoke_test 同一已知限制),
    # 让缩略图缓存改走 PNG——OIIO 按扩展名选输出写入器,Qt 原生可读
    _orig_thumb_path = hdr_browser.thumb_path

    def _png_thumb(lib_dir, hdr_path):
        return _orig_thumb_path(lib_dir, hdr_path)[:-4] + ".png"

    with patch.object(hdr_browser, "_houdini_bin",
                      lambda: str(HOUDINI_ROOT / "bin")), \
         patch.object(hdr_browser, "thumb_path", _png_thumb), \
         patch.object(hdr_browser, "_SETTINGS", store):
        win = hdr_browser._HdrLibraryWindow(str(lib))
        win.resize(SHOT_W, SHOT_H)
        win.show()
        win.dir_label.setText("当前目录: C:/HDR_Library")
        win.reload()
        deadline = time.time() + 90
        while time.time() < deadline:
            QtWidgets.QApplication.processEvents()
            th = win._thread
            if th is None or not th.isRunning():
                break
            time.sleep(0.05)
        # 缩略图就绪后还有 ~400ms 的批量 setIcon 延迟,多沉淀一轮确保上屏
        _settle(win, 1800)
        ok = _grab(win, "hdr.png")
        win.deleteLater()
    store.path.unlink(missing_ok=True)
    return ok


# -------------------------------------------------------------------
# 4) Recipe Library — stubbed store, gradient thumbnails, metadata
# -------------------------------------------------------------------
def shot_recipe(tmp: Path):
    from houtools.core.settings import JsonStore
    from houtools.recipelib import metadata as rl_meta
    from houtools.recipelib import browser as rl_browser
    from houtools.recipelib import store as rl_store

    store = JsonStore("_about_shots_rl.json", defaults=dict(rl_meta._DEFAULTS))
    thumbs = tmp / "rl_thumbs"
    thumbs.mkdir(parents=True, exist_ok=True)

    infos = [
        rl_store.RecipeInfo(name="mahx::karma_pyro_setup",
                            label="Karma Pyro Setup", category="tool",
                            submenu="HouTools", net_category="Sop"),
        rl_store.RecipeInfo(name="mahx::dome_light_rig",
                            label="Dome Light Rig", category="tool",
                            submenu="HouTools", net_category="Lop"),
        rl_store.RecipeInfo(name="mahx::flipbook_cam",
                            label="Flipbook Cam", category="tool",
                            submenu="", net_category="Sop"),
        rl_store.RecipeInfo(name="mahx::vend_frost_material",
                            label="Frost Material", category="node",
                            submenu="Materials", net_category="Sop"),
        rl_store.RecipeInfo(name="mahx::usd_layer_stack",
                            label="USD Layer Stack", category="tool",
                            submenu="USD", net_category="Lop"),
        rl_store.RecipeInfo(name="mahx::cache_wedge",
                            label="Cache Wedge", category="tool",
                            submenu="HouTools", net_category="Sop"),
    ]
    palette = ["#c8102e", "#e2571b", "#f5a01c", "#8a2438", "#d97a2a",
               "#a83a4a"]

    with patch.object(rl_meta, "_SETTINGS", store), \
         patch.object(rl_meta, "THUMBS_DIR", thumbs), \
         patch.object(rl_meta, "get_lib_dirs",
                      return_value=["X:/demo_lib"]), \
         patch.object(rl_browser.store, "list_recipes",
                      side_effect=lambda lib_dirs=None:
                          list(infos) if lib_dirs else []):
        for info, color in zip(infos, palette):
            pm = QtGui.QPixmap(256, 169)
            pm.fill(QtGui.QColor(color))
            painter = QtGui.QPainter(pm)
            painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 70)))
            for i in range(6):
                painter.setBrush(QtGui.QBrush(QtGui.QColor(
                    255, 255, 255, 18 + i * 6)))
                painter.drawEllipse(i * 40 - 20, 40 + i * 12, 120, 90)
            painter.end()
            rl_meta.set_thumb_from_file(info.name, _save_tmp_png(tmp, pm))
            rl_meta.set_color(info.name, color)
            if info.name.endswith(("pyro_setup", "frost_material")):
                rl_meta.set_favorite(info.name, True)
        rl_meta.set_display_name(infos[1].name, "穹顶灯光装备")
        rl_meta.set_tags(infos[0].name, ["pyro", "常用"])
        rl_meta.set_tags(infos[3].name, ["材质", "预览"])

        win = rl_browser._RecipeLibraryWindow()
        win.resize(SHOT_W, SHOT_H)
        win.show()
        win.reload()
        _settle(win, 500)
        ok = _grab(win, "recipe.png")
        win.deleteLater()
    store.path.unlink(missing_ok=True)
    return ok


def _save_tmp_png(tmp: Path, pm: QtGui.QPixmap) -> str:
    p = tmp / ("thumb_%d.png" % int(time.time() * 1000))
    pm.save(str(p), "PNG")
    return str(p)


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    # 离屏环境没有 Houdini 的全局暗色样式,Qt 默认调色板是浅色的——
    # 未显式铺底的窗口(如 Automation 主背景)会发白,这里模拟暗色主题
    # (暖黑,与 about.html 的 --bg/--card 色系一致)
    pal = app.palette()
    pal.setColor(QtGui.QPalette.Window, QtGui.QColor("#201a19"))
    pal.setColor(QtGui.QPalette.WindowText, QtGui.QColor("#ece5df"))
    pal.setColor(QtGui.QPalette.Base, QtGui.QColor("#241d1c"))
    pal.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor("#282020"))
    pal.setColor(QtGui.QPalette.Text, QtGui.QColor("#ece5df"))
    pal.setColor(QtGui.QPalette.Button, QtGui.QColor("#322826"))
    pal.setColor(QtGui.QPalette.ButtonText, QtGui.QColor("#ece5df"))
    pal.setColor(QtGui.QPalette.PlaceholderText, QtGui.QColor("#6a5c55"))
    app.setPalette(pal)
    with tempfile.TemporaryDirectory(prefix="about_shots_") as td:
        tmp = Path(td)
        results = [
            shot_automation(tmp),
            shot_videoseq(tmp),
            shot_hdr(tmp),
            shot_recipe(tmp),
        ]
    print("done:", ["ok" if r else "MISS" for r in results])


if __name__ == "__main__":
    main()
