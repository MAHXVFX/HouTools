"""Recipe Library 用户元数据层：标签 / 收藏 / 缩略图 / Markdown 文档。

键一律是 recipe 内部名（HDA definition 的 node type 名，如
``houtools::pyro::my_setup``），它也是 hou.data.applyXxxRecipe 的调用名，
在官方 Recipe Manager 里重命名前保持稳定。

存储位置（均 gitignored，随机器各自保存）：
- settings/recipelib.json —— 标签、收藏、缩略图路径（JsonStore）
- settings/recipe_thumbs/  —— 缩略图文件（从用户选择的原图复制而来，
  支持常规图片与 GIF；GIF 网格里取首帧、预览区和文档里动起来）
- settings/recipe_docs/<名>/ —— 每个配方一个目录：doc.md + assets/
  （文档里插入的图片/视频复制进 assets，正文用相对路径引用）

不把元数据写进 recipe 的 HDA section：官方出厂 recipe（OPlibRecipe.hda
在 $HFS 下只读）也允许收藏/打标签，且元数据应与"recipe 存在哪个库文
件"解耦；将来做"分享打包"再考虑导出聚合。

本模块不 import hou，无头环境（smoke test）可完整测试。
"""

import os
import re
import shutil

from houtools.core.constants import PROJECT_ROOT, SETTINGS_DIR
from houtools.core.log import get_logger
from houtools.core.settings import JsonStore

log = get_logger("recipelib.metadata")

_DEFAULTS = {
    "lib_dirs": [],        # recipe 库文件夹（可多个；递归扫描其中的 .hda）
    "favorites": [],       # 收藏的 recipe 内部名列表
    "tags": {},            # 内部名 -> [标签...]
    "thumbs": {},          # 内部名 -> 缩略图绝对路径
    "display_names": {},   # 内部名 -> 用户自定义显示名（支持中文）
    "colors": {},          # 内部名 -> "#rrggbb"（卡片名字旁的颜色框）
}

_SETTINGS = JsonStore("recipelib.json", defaults=_DEFAULTS)

THUMBS_DIR = SETTINGS_DIR / "recipe_thumbs"
DOCS_DIR = SETTINGS_DIR / "recipe_docs"

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
VIDEO_EXTS = (".mp4", ".mov", ".avi", ".webm", ".mkv")
MEDIA_FILTER = "媒体文件 (*.png *.jpg *.jpeg *.gif *.webp *.bmp *.mp4 *.mov *.avi *.webm *.mkv);;图片 (*.png *.jpg *.jpeg *.gif *.webp *.bmp);;视频 (*.mp4 *.mov *.avi *.webm *.mkv);;全部文件 (*.*)"


def safe_name(name):
    """内部名转文件名安全串：非字母数字下划线全部折成 __。

    内部名含 :: 与 /（如 houtools::pyro::my_setup、cop/file::sidefx::...），
    保留全名可保证不同命名空间下同名 label 的缩略图/文档不互撞。
    """
    return re.sub(r"[^0-9a-zA-Z_]+", "__", name).strip("_") or "recipe"


# --------------------------------------------------------------------------
# 路径存储形式：缩略图等持久化路径一律存"相对插件根"的相对路径，
# 项目目录整体挪动（换盘符/换机器/换 Houdini 版本目录）后仍然有效。
# 读取侧 _to_abs 兼容历史绝对路径；跨盘等无法相对化的退回绝对。
# --------------------------------------------------------------------------

def _to_stored(path):
    """绝对路径 → 存储形式：插件根内转相对，根外（跨盘等）保留绝对原样。"""
    try:
        rel = os.path.relpath(path, PROJECT_ROOT)
    except (OSError, ValueError):
        return os.path.abspath(path)
    if rel.startswith(".."):
        return os.path.abspath(path)
    return rel


def _to_abs(stored):
    """存储路径 → 可用的绝对路径（相对的按插件根拼回）。"""
    if os.path.isabs(stored):
        return stored
    return os.path.join(str(PROJECT_ROOT), stored)


def migrate_legacy_thumb_paths():
    """历史版本存的绝对缩略图路径迁成相对插件根（幂等，低频时机调用）。

    只改能相对化（插件根内）的条目；根外的跨盘绝对路径原样保留。
    """
    thumbs = dict(_SETTINGS.get("thumbs") or {})
    changed = False
    for name, stored in list(thumbs.items()):
        if not stored or not os.path.isabs(stored):
            continue
        rel = _to_stored(stored)
        if not os.path.isabs(rel) and rel != stored:
            thumbs[name] = rel
            changed = True
    if changed:
        _SETTINGS.set("thumbs", thumbs)
        log.info("migrated %d thumb paths to relative", len(thumbs))
    return changed


# --------------------------------------------------------------------------
# 批量读写（导出打包 / 导入合并用，见 transfer.py）
# --------------------------------------------------------------------------

def all_metadata():
    """五个内容键的副本（导出打包用；不含 lib_dirs——机器相关不带）。"""
    return {
        "favorites": list(_SETTINGS.get("favorites") or []),
        "tags": dict(_SETTINGS.get("tags") or {}),
        "thumbs": dict(_SETTINGS.get("thumbs") or {}),
        "display_names": dict(_SETTINGS.get("display_names") or {}),
        "colors": dict(_SETTINGS.get("colors") or {}),
    }


def bulk_set(favorites=None, tags=None, thumbs=None, display_names=None,
             colors=None):
    """按键整体覆盖写入（导入合并的结果一次落盘），None 的键不动。

    thumbs 的值须已是"存储形式"（相对插件根，同 _to_stored 的产出）。
    逐键 setter 一条一存，导入几百条会写盘几百次，故走这里。
    """
    if favorites is not None:
        _SETTINGS.set("favorites", list(favorites), save=False)
    if tags is not None:
        _SETTINGS.set("tags", dict(tags), save=False)
    if thumbs is not None:
        _SETTINGS.set("thumbs", dict(thumbs), save=False)
    if display_names is not None:
        _SETTINGS.set("display_names", dict(display_names), save=False)
    if colors is not None:
        _SETTINGS.set("colors", dict(colors), save=False)
    _SETTINGS.save()


# --------------------------------------------------------------------------
# 库文件夹
# --------------------------------------------------------------------------

def get_lib_dirs():
    """库文件夹列表：转绝对路径、去重、保序；不存在的目录保留
    （用户可能后挂载盘符），扫描时才告警。"""
    out = []
    for d in _SETTINGS.get("lib_dirs") or []:
        d = os.path.abspath(d)
        if d and d not in out:
            out.append(d)
    return out


def set_lib_dirs(dirs):
    clean = []
    for d in dirs or []:
        d = os.path.abspath(str(d).strip())
        if d and d not in clean:
            clean.append(d)
    _SETTINGS.set("lib_dirs", clean)
    return clean


# --------------------------------------------------------------------------
# 自定义显示名
# --------------------------------------------------------------------------

def get_display_name(name):
    """用户自定义的显示名（支持中文）；未设置返回空串。"""
    return (_SETTINGS.get("display_names") or {}).get(name) or ""


def set_display_name(name, title):
    """写入自定义显示名；传空串/None 清除（恢复默认显示链）。

    只改展示层，recipe 内部名与官方 label 均不动。
    """
    all_names = dict(_SETTINGS.get("display_names") or {})
    title = (str(title) if title is not None else "").strip()
    if title:
        all_names[name] = title
    else:
        all_names.pop(name, None)
    _SETTINGS.set("display_names", all_names)
    return title


# --------------------------------------------------------------------------
# 卡片颜色框
# --------------------------------------------------------------------------

def get_color(name):
    """用户自定义的卡片颜色（"#rrggbb"）；未设置返回空串。"""
    return (_SETTINGS.get("colors") or {}).get(name) or ""


def set_color(name, color):
    """写入卡片颜色；传空串/None 清除（恢复默认灰条）。

    只接受 #rrggbb 形式的值（QColorDialog 产出），其余一律视为清除。
    """
    all_colors = dict(_SETTINGS.get("colors") or {})
    color = (str(color).strip() if color else "")
    if re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        all_colors[name] = color
    else:
        color = ""
        all_colors.pop(name, None)
    _SETTINGS.set("colors", all_colors)
    return color


# --------------------------------------------------------------------------
# 收藏
# --------------------------------------------------------------------------

def is_favorite(name):
    return name in (_SETTINGS.get("favorites") or [])


def set_favorite(name, fav):
    favs = list(_SETTINGS.get("favorites") or [])
    if fav and name not in favs:
        favs.append(name)
    elif not fav and name in favs:
        favs.remove(name)
    _SETTINGS.set("favorites", favs)
    return fav


# --------------------------------------------------------------------------
# 标签
# --------------------------------------------------------------------------

def get_tags(name):
    return list((_SETTINGS.get("tags") or {}).get(name) or [])


def set_tags(name, tags):
    """写入标签（自动去重、去空）；传空列表则删除该条目。"""
    clean = []
    for t in tags or []:
        t = str(t).strip()
        if t and t not in clean:
            clean.append(t)
    all_tags = dict(_SETTINGS.get("tags") or {})
    if clean:
        all_tags[name] = clean
    else:
        all_tags.pop(name, None)
    _SETTINGS.set("tags", all_tags)
    return clean


def all_tags():
    """全局标签全集（排序去重），供侧栏标签区展示。"""
    seen = set()
    for tags in (_SETTINGS.get("tags") or {}).values():
        seen.update(tags or [])
    return sorted(seen)


# --------------------------------------------------------------------------
# 缩略图
# --------------------------------------------------------------------------

def get_thumb(name):
    """返回缩略图的可用绝对路径（存储侧是相对插件根的相对路径）。"""
    stored = (_SETTINGS.get("thumbs") or {}).get(name) or ""
    if not stored:
        return ""
    path = _to_abs(stored)
    return path if os.path.exists(path) else ""


def set_thumb_from_file(name, src):
    """把用户选中的图片/GIF 复制进缩略图目录并记录，返回绝对路径。

    保留原扩展名（GIF 靠它动起来，QImageReader 靠它选解码器）。
    settings 里存的是相对插件根的相对路径（见 _to_stored）。
    """
    if not os.path.exists(src):
        raise RuntimeError("文件不存在: {}".format(src))
    ext = os.path.splitext(src)[1].lower() or ".png"
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    dst = str(THUMBS_DIR / (safe_name(name) + ext))
    shutil.copyfile(src, dst)
    thumbs = dict(_SETTINGS.get("thumbs") or {})
    thumbs[name] = _to_stored(dst)
    _SETTINGS.set("thumbs", thumbs)
    return dst


def set_thumb_from_pixmap(name, pixmap):
    """把裁剪结果（QPixmap）存为缩略图（PNG 无损），返回绝对路径。

    裁剪比例与卡片缩略图区一致（crop.TARGET_RATIO），铺满显示无灰边；
    GIF 动图不经此路径（Qt 无 GIF 编码器，走 set_thumb_from_file 原样复制）。
    """
    if pixmap is None or pixmap.isNull():
        raise RuntimeError("缩略图内容为空")
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    dst = str(THUMBS_DIR / (safe_name(name) + ".png"))
    if not pixmap.save(dst, "PNG"):
        raise RuntimeError("缩略图保存失败: {}".format(dst))
    thumbs = dict(_SETTINGS.get("thumbs") or {})
    thumbs[name] = _to_stored(dst)
    _SETTINGS.set("thumbs", thumbs)
    return dst


def clear_thumb(name):
    thumbs = dict(_SETTINGS.get("thumbs") or {})
    old = thumbs.pop(name, None)
    _SETTINGS.set("thumbs", thumbs)
    if old:
        old_abs = _to_abs(old)
        if os.path.exists(old_abs):
            try:
                os.remove(old_abs)
            except OSError as exc:
                log.warning("cannot remove thumb %s: %s", old_abs, exc)


# --------------------------------------------------------------------------
# Markdown 文档
# --------------------------------------------------------------------------

def doc_dir(name):
    return str(DOCS_DIR / safe_name(name))


def doc_path(name):
    return os.path.join(doc_dir(name), "doc.md")


def doc_exists(name):
    """文档存在且有正文；空文件视为无文档（主面板回退官方备注）。

    空白编辑器直接保存会落一个 0 字节 doc.md——按"没写内容=没文档"处理。
    """
    path = doc_path(name)
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            return bool(f.read().strip())
    except OSError as exc:
        log.warning("cannot read doc %s: %s", path, exc)
        return False


def read_doc(name):
    path = doc_path(name)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError as exc:
        log.warning("cannot read doc %s: %s", path, exc)
        return ""


def write_doc(name, text):
    path = doc_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".~tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)  # 原子落盘，编辑器异常中断不留半截文件


def assets_dir(name):
    return os.path.join(doc_dir(name), "assets")


def insert_asset(name, src):
    """把图片/视频复制进该配方的 assets/，返回正文里用的相对引用。

    同名文件追加序号（a.mp4、a_2.mp4），不覆盖历史素材。
    """
    if not os.path.exists(src):
        raise RuntimeError("文件不存在: {}".format(src))
    adir = assets_dir(name)
    os.makedirs(adir, exist_ok=True)
    base = re.sub(r"[^0-9a-zA-Z_-]+", "_",
                  os.path.splitext(os.path.basename(src))[0]).strip("_") or "asset"
    ext = os.path.splitext(src)[1].lower()
    dst = os.path.join(adir, base + ext)
    i = 2
    while os.path.exists(dst):
        dst = os.path.join(adir, "{}_{}{}".format(base, i, ext))
        i += 1
    shutil.copyfile(src, dst)
    return "assets/" + os.path.basename(dst)


def delete_doc_dir(name):
    """删除配方文档目录（配方被删除时清理残留）。"""
    d = doc_dir(name)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
