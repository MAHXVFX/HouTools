"""Recipe 库数据打包：导出 / 导入（.zip 交换格式，设置面板「数据」页）。

导出把"用户积累"打成单个 zip：标签/收藏/自定义显示名/卡片颜色
（settings/recipelib.json 按当前库里的 recipe 过滤，陈旧条目不带）+
缩略图文件 + Markdown 文档（含 assets 媒体），可选连同样式工具的
.hda 库文件——"打包带走、换机/与人分享"。

导入按 recipe 内部名逐条合并：包内条目覆盖本地同名条目（标签/文档/
缩略图等），本地独有内容一律不动；.hda 只复制"其内所有 recipe 名在
本地都不存在"的文件——同名 recipe 意味着两份不同内容的同名定义，
Houdini 按库优先级二选一，行为不可预期，宁跳过不覆盖（导入摘要中
明示，删掉本地同名后可重新导入）。

zip 布局（manifest.json 的 format_version=1）：
  manifest.json            # 格式版本/导出信息/hda_files（文件→内含 recipe 名）
  recipelib.json           # 过滤后的元数据；thumbs 值改写为包内路径 thumbs/<名>
  thumbs/<safe名>.<ext>
  docs/<safe名>/doc.md + assets/*
  recipes/<原文件名>.hda     # 仅勾选「连同工具打包」时

零 hou 依赖（枚举由调用方传入 store.list_recipes 的结果；导入后的库
安装走浏览器 reload() → ensure_libraries_installed 链路）——本模块只
做文件与 JSON 搬运，无头环境（smoke test）可完整测试。

取消语义：导出全程可取消（写临时文件，成功才原子替换，半成品即清）；
导入在元数据落盘前可取消——已解出的文件留在磁盘但不改任何设置，重跑
一次导入即完整（导入幂等）。
"""

import json
import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from houtools.core.log import get_logger
from houtools.recipelib import metadata

log = get_logger("recipelib.transfer")

MANIFEST = "manifest.json"
METADATA = "recipelib.json"
FORMAT_VERSION = 1
APP_TAG = "HouTools Recipe Library"

_THUMB_ARC_DIR = "thumbs/"
_DOC_ARC_DIR = "docs/"
_HDA_ARC_DIR = "recipes/"


class TransferError(RuntimeError):
    """包不合法/版本不认识等预期内失败，UI 直接展示 message。"""


class TransferCancelled(Exception):
    """用户在进度对话框点了取消（半成品已清理，不算错误）。"""


def _norm(path):
    try:
        return os.path.normcase(os.path.realpath(path))
    except (OSError, ValueError):
        return os.path.normcase(os.path.abspath(path))


def _compress_for(arcname):
    """json/md 走 deflate，其余（png/gif/视频/hda）原样存储——媒体本身
    已压缩，deflate 只烧 CPU 不减体积。"""
    if arcname.lower().endswith((".json", ".md", ".txt")):
        return zipfile.ZIP_DEFLATED
    return zipfile.ZIP_STORED


def _report(progress, done, total, text):
    """进度回调；返回真值 = 用户请求取消。回调自身异常不中断打包。"""
    if progress is None:
        return False
    try:
        return bool(progress(done, total, text))
    except Exception as exc:
        log.debug("progress callback failed: %s", exc)
        return False


def _safe_rel(rel):
    """校验包内相对路径（防 zip 路径穿越），返回 os 路径，不合法抛错。"""
    parts = [p for p in rel.replace("\\", "/").split("/")
             if p not in ("", ".")]
    if not parts or any(p == ".." or ":" in p for p in parts):
        raise TransferError("包内路径不合法: {}".format(rel))
    return os.path.join(*parts)


def _extract_member(zf, info, dst):
    """把 zip 成员解到 dst（流式拷贝，文档里的视频可能上 GB）。"""
    dst = str(dst)
    parent = os.path.dirname(dst)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with zf.open(info) as src, open(dst, "wb") as out:
        shutil.copyfileobj(src, out, 1024 * 1024)


# --------------------------------------------------------------------------
# 导出
# --------------------------------------------------------------------------

def export_recipes(recipes, zip_path, include_hda=True, progress=None):
    """把 recipes（store.list_recipes 的结果）打包为交换 zip，返回摘要。

    只读 RecipeInfo 的内部名与 .library；元数据按现存 recipe 过滤
    （已删 recipe 的陈旧标签/缩略图不带）。progress(done, total, text)
    返回真值表示取消 → 抛 TransferCancelled，半成品已清理，目标路径
    不会留下半个 zip。
    """
    names = []
    for r in recipes or []:
        if getattr(r, "name", "") and r.name not in names:
            names.append(r.name)
    if not names:
        raise TransferError("当前库为空，没有可导出的内容")
    name_set = set(names)
    raw = metadata.all_metadata()

    # ---- 收集待打包文件（先列全，进度总数才准确） ----
    entries = []      # (包内路径, 源文件路径)
    thumbs_arc = {}   # 内部名 -> 包内缩略图路径（元数据改写用）
    for name in names:
        src = metadata.get_thumb(name)   # 含存在性检查，缺图自动跳过
        if not src:
            continue
        arc = _THUMB_ARC_DIR + os.path.basename(src)
        thumbs_arc[name] = arc
        entries.append((arc, src))

    for name in names:
        if not metadata.doc_exists(name):
            continue
        root = metadata.doc_dir(name)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for fn in sorted(filenames):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                entries.append((_DOC_ARC_DIR + metadata.safe_name(name)
                                + "/" + rel, full))

    hda_files = {}    # 包内名 -> {"original": 磁盘文件名, "recipes": [内部名]}
    if include_hda:
        by_lib = {}   # realpath -> (磁盘路径, [内部名...])，保持首见顺序
        for r in recipes:
            lib = getattr(r, "library", "") or ""
            if lib and os.path.isfile(lib):
                by_lib.setdefault(_norm(lib), (lib, []))[1].append(r.name)
        used = set()
        for lib, rec_names in by_lib.values():
            base = os.path.basename(lib)
            arc, i = base, 2
            while arc.lower() in used:   # 不同库文件夹里的同名文件
                stem, ext = os.path.splitext(base)
                arc = "{}_{}{}".format(stem, i, ext)
                i += 1
            used.add(arc.lower())
            full_arc = _HDA_ARC_DIR + arc   # manifest 键 = 完整包内路径
            hda_files[full_arc] = {"original": base, "recipes": rec_names}
            entries.append((full_arc, lib))

    packaged = {
        "favorites": [n for n in raw["favorites"] if n in name_set],
        "tags": {n: list(v) for n, v in (raw["tags"] or {}).items()
                 if n in name_set and v},
        "thumbs": dict(thumbs_arc),
        "display_names": {n: v for n, v in
                          (raw["display_names"] or {}).items()
                          if n in name_set and v},
        "colors": {n: v for n, v in (raw["colors"] or {}).items()
                   if n in name_set and v},
    }
    manifest = {
        "format_version": FORMAT_VERSION,
        "app": APP_TAG,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "recipe_count": len(names),
        "recipes": names,
        "include_hda": bool(include_hda),
        "hda_files": hda_files,
    }

    # ---- 写包：临时文件 → 成功后原子替换（取消/失败不产半截 zip） ----
    zip_path = os.path.abspath(zip_path)
    tmp = zip_path + ".~part"
    total = len(entries) + 2
    done = 0
    ok = False
    try:
        parent = os.path.dirname(zip_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with zipfile.ZipFile(tmp, "w") as zf:
            if _report(progress, done, total, "打包元数据..."):
                raise TransferCancelled()
            zf.writestr(METADATA, json.dumps(packaged, indent=2,
                                             ensure_ascii=False))
            done += 1
            for arc, src in entries:
                if _report(progress, done, total, "打包 {}".format(arc)):
                    raise TransferCancelled()
                zf.write(src, arc, compress_type=_compress_for(arc))
                done += 1
            if _report(progress, done, total, "写入清单..."):
                raise TransferCancelled()
            zf.writestr(MANIFEST, json.dumps(manifest, indent=2,
                                             ensure_ascii=False))
        os.replace(tmp, zip_path)
        ok = True
    finally:
        if not ok and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError as exc:
                log.warning("cannot remove partial export %s: %s", tmp, exc)
    return {
        "path": zip_path,
        "recipes": len(names),
        "docs": sum(1 for a, _ in entries
                    if a.startswith(_DOC_ARC_DIR) and a.endswith("/doc.md")),
        "thumbs": len(thumbs_arc),
        "hda": len(hda_files),
    }


# --------------------------------------------------------------------------
# 导入
# --------------------------------------------------------------------------

def _open_zip(zip_path):
    try:
        return zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise TransferError("无法读取 zip 包: {}".format(exc))


def _read_manifest(zf):
    try:
        raw = zf.read(MANIFEST)
    except KeyError:
        raise TransferError("不是本工具导出的包（缺少 manifest.json）")
    try:
        m = json.loads(raw)
    except ValueError as exc:
        raise TransferError("manifest.json 损坏: {}".format(exc))
    if not isinstance(m, dict) or m.get("app") != APP_TAG:
        raise TransferError("不是本工具导出的包")
    ver = m.get("format_version")
    if not isinstance(ver, int) or ver > FORMAT_VERSION:
        raise TransferError("导出包格式过新（v{}），请先升级 HouTools"
                            .format(ver))
    return m


def _read_packaged_metadata(zf):
    try:
        raw = zf.read(METADATA)
    except KeyError:
        raise TransferError("包缺少 recipelib.json，可能已损坏")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise TransferError("recipelib.json 损坏: {}".format(exc))
    if not isinstance(data, dict):
        raise TransferError("recipelib.json 内容异常")
    return {
        "favorites": [str(x) for x in (data.get("favorites") or []) if x],
        # 空列表条目不收：导入按"包内覆盖同名"合并，空标签会误删本地内容
        "tags": {str(k): [str(t) for t in (v or [])]
                 for k, v in (data.get("tags") or {}).items() if v},
        "thumbs": {str(k): str(v).replace("\\", "/")
                   for k, v in (data.get("thumbs") or {}).items() if v},
        "display_names": {str(k): str(v) for k, v in
                          (data.get("display_names") or {}).items() if v},
        "colors": {str(k): str(v) for k, v in
                   (data.get("colors") or {}).items() if v},
    }


def _doc_dirs_of(members):
    """包内含 doc.md 的文档目录名（safe 名）集合。"""
    return sorted({m.split("/")[1] for m in members
                   if m.startswith(_DOC_ARC_DIR) and m.endswith("/doc.md")
                   and "/" in m[len(_DOC_ARC_DIR):]})


def _hda_plan(manifest, members, existing):
    """决策每个 .hda 包内文件：复制（无同名冲突）或跳过（含本地同名）。"""
    hda_import, hda_skip = [], []
    for arc, info in (manifest.get("hda_files") or {}).items():
        arc = str(arc)
        if arc not in members:
            continue
        rec = [str(n) for n in (info or {}).get("recipes") or []]
        if any(n in existing for n in rec):
            hda_skip.append((arc, rec))
        else:
            # manifest 在可信边界之外：original 只取文件名（包作者可任意
            # 伪造），防 ../ 相对路径与绝对路径把解包目标写出库目录之外
            original = str((info or {}).get("original") or arc)
            hda_import.append((arc, os.path.basename(original)))
    return hda_import, hda_skip


def inspect_package(zip_path, existing_names=()):
    """读包摘要（导入确认对话框用）：数量统计 + 同名冲突预判，不落盘。"""
    existing = set(existing_names or ())
    with _open_zip(zip_path) as zf:
        manifest = _read_manifest(zf)
        meta = _read_packaged_metadata(zf)
        members = {i.filename for i in zf.infolist() if not i.is_dir()}
        hda_import, hda_skip = _hda_plan(manifest, members, existing)
    names = [str(n) for n in manifest.get("recipes") or []]
    try:
        size = os.path.getsize(zip_path)
    except OSError:
        size = 0
    return {
        "recipes": names,
        "conflicts": [n for n in names if n in existing],
        "docs": _doc_dirs_of(members),
        "thumbs": [n for n, arc in meta["thumbs"].items() if arc in members],
        "hda_import": hda_import,
        "hda_skip": hda_skip,
        "size": size,
        "exported_at": str(manifest.get("exported_at") or ""),
    }


def import_package(zip_path, lib_dir=None, existing_names=(), progress=None):
    """把交换包合并导入当前环境，返回导入摘要 dict。

    - 元数据按内部名合并：包内覆盖同名，本地独有保留（最后一次性落盘，
      取消/失败不会留下改了一半的设置）
    - 缩略图：包内带图的名字，图复制进 settings/recipe_thumbs 并改写
      记录（存储形式 = 相对插件根，与 set_thumb_from_file 一致）
    - 文档：包内有 doc.md 的名字整目录替换本地（含 assets）
    - .hda：只复制无同名冲突的文件到 lib_dir（不存在自动创建，重名
      文件自动加序号）；包里有可导文件却不给 lib_dir → 报错
    """
    existing = set(existing_names or ())
    with _open_zip(zip_path) as zf:
        manifest = _read_manifest(zf)
        meta = _read_packaged_metadata(zf)
        members = {i.filename: i for i in zf.infolist() if not i.is_dir()}
        hda_import, hda_skip = _hda_plan(manifest, members, existing)
        thumb_items = [(n, a) for n, a in meta["thumbs"].items()
                       if a in members]
        doc_dirs = _doc_dirs_of(members)
        total = len(thumb_items) + len(doc_dirs) + len(hda_import) + 1
        done = 0

        # ---- 1) 缩略图 ----
        new_thumbs = {}
        for name, arc in thumb_items:
            if _report(progress, done, total,
                       "导入缩略图 {}".format(name)):
                raise TransferCancelled()
            _extract_member(zf, members[arc],
                            Path(metadata.THUMBS_DIR) / os.path.basename(arc))
            new_thumbs[name] = os.path.join("settings", "recipe_thumbs",
                                            os.path.basename(arc))
            done += 1

        # ---- 2) 文档：包内有的整目录替换（含 assets），没有的不动 ----
        for safe in doc_dirs:
            if _report(progress, done, total, "导入文档 {}".format(safe)):
                raise TransferCancelled()
            prefix = _DOC_ARC_DIR + safe + "/"
            target = Path(metadata.DOCS_DIR) / safe
            if target.exists():
                shutil.rmtree(str(target), ignore_errors=True)
            target.mkdir(parents=True, exist_ok=True)
            for arc in members:
                if arc.startswith(prefix):
                    _extract_member(zf, members[arc],
                                    target / _safe_rel(arc[len(prefix):]))
            done += 1

        # ---- 3) .hda 库文件（导入后由浏览器 reload 自动安装） ----
        hda_copied = []
        if hda_import:
            if not lib_dir:
                raise TransferError("未指定 .hda 的导入目标文件夹")
            if _report(progress, done, total, "复制库文件..."):
                raise TransferCancelled()
            os.makedirs(lib_dir, exist_ok=True)
            taken = {n.lower() for n in os.listdir(lib_dir)}
            for arc, original in hda_import:
                stem, ext = os.path.splitext(original)
                dst, i = original, 2
                while dst.lower() in taken:   # 与盘上已有文件重名
                    dst = "{}_{}{}".format(stem, i, ext)
                    i += 1
                taken.add(dst.lower())
                _extract_member(zf, members[arc], Path(lib_dir) / dst)
                hda_copied.append(dst)
                done += 1

        # ---- 4) 元数据合并，一次落盘（不可取消点：文件已就位） ----
        _report(progress, done, total, "合并元数据...")
        local = metadata.all_metadata()
        favs = list(local["favorites"])
        for n in meta["favorites"]:
            if n not in favs:
                favs.append(n)
        merged_thumbs = dict(local["thumbs"])
        merged_thumbs.update(new_thumbs)
        tags = dict(local["tags"])
        tags.update(meta["tags"])
        display = dict(local["display_names"])
        display.update(meta["display_names"])
        colors = dict(local["colors"])
        colors.update(meta["colors"])
        metadata.bulk_set(favorites=favs, tags=tags, thumbs=merged_thumbs,
                          display_names=display, colors=colors)
    return {
        "metadata_names": len(set(meta["tags"]) | set(meta["display_names"])
                              | set(meta["colors"]) | set(new_thumbs)
                              | set(meta["favorites"])),
        "docs": len(doc_dirs),
        "thumbs": len(new_thumbs),
        "hda_copied": hda_copied,
        "hda_skipped": hda_skip,
    }
