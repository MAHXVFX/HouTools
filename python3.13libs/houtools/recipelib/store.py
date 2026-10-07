"""官方 recipes 的脚本层封装：库安装 / 枚举 / 创建 / 应用 / 删除。

底层是 H22 的公开 API（本仓库做过完整可行性验证，22.0.429 实测）：
- 枚举:   recipeutils.recipeNames(RecipeCategory.xxx, ...)
- 元信息: recipe 数据是 data 资产（HDA definition），完整结构存放在
  该 definition 的 ``data.recipe.json`` section（properties/tool/info），
  用 hou.nodeType(hou.dataNodeTypeCategory(), name).definition() 直读，
  不依赖内部包 hrecipes。
- 创建:   hou.data.saveToolRecipe / saveNodePresetRecipe / ...
- 应用:   hou.data.applyToolRecipe / applyNodePresetRecipe / ...
- 删除:   HDADefinition.destroy()

加载模型（面板只管用户自己的库）：
- 用户在面板配置若干"库文件夹"（settings/recipelib.json 的 lib_dirs，
  可多个）；文件夹里**任意层级**的 .hda 都会被安装，且只枚举这些文件里
  的 recipe——出厂（$HFS）/ Labs / 用户偏好默认库里的 recipes 一律不显示。
- 面板只做展示与管理，创建 recipe 走 Houdini 官方保存流程；文件夹里放
  官方保存到库文件夹的 .hda 或别人分享的 recipe .hda，都会被扫描加载。
- 误装防护：安装后检查 definitionsInFile，没有任何 data 类定义的文件
  （误放进来的普通 OTL）立即卸载还原，不给会话留副作用。
- uiready.py 每次启动也调 ensure_libraries_installed，让 Tab 菜单里的
  session tool 从会话一开始就注册。

约束：import hou / recipeutils 一律在函数内——本模块必须无头可导入
（smoke test 依赖）；所有函数都只在 Houdini 主线程调用。
"""

import json
import os
from dataclasses import dataclass, field

from houtools.core.log import get_logger

log = get_logger("recipelib.store")

# 与 docs/metadata 模板一致的类型展示顺序
CATEGORY_LABELS = {
    "tool": "Tool（Tab 工具）",
    "node": "Node Preset（节点参数）",
    "parm": "Parameter Preset（参数预设）",
    "decoration": "Decoration（装饰）",
    "parmTemplate": "Parm Template（参数模板）",
    "data": "Data（数据）",
}


@dataclass
class RecipeInfo:
    name: str            # 内部名（apply/delete 调用名）
    label: str = ""      # 菜单显示名
    category: str = "tool"
    submenu: str = ""    # 官方 submenu / tab_submenu
    comment: str = ""
    author: str = ""
    patterns: list = field(default_factory=list)   # nodetype_patterns
    library: str = ""    # 所在 .hda 文件路径
    under_hfs: bool = False  # 出厂 recipe（只读，不可删）
    houdini_version: str = ""  # 保存该 recipe 时的 Houdini 版本（info 块）
    net_category: str = ""     # 目标网络类别（"Sop"/"Lop"/...，卡片展示用）
    node_count: int = -1       # 内含节点总数；-1 = 不适用（参数预设等）

    @property
    def display_label(self):
        """官方显示链：官方 label > 内部名末段（mahx::my_copy_test →
        my_copy_test）。用户自定义显示名在 browser/metadata 层叠加。"""
        if self.label:
            return self.label
        return self.name.rsplit("::", 1)[-1] or self.name


def _count_network_items(item_data):
    """递归数一个网络条目树里的节点总数。

    照官方 hrecipes.utils.countContentsOfItem 的语义实现（公开数据格式，
    不依赖内部包）：children / editable_nodes 递归累计，
    subnetindirectinput 只是连线占位不计。
    """
    if str(item_data.get("type", "")).lower() == "subnetindirectinput":
        return 0
    count = 1
    children = item_data.get("children")
    if isinstance(children, dict):
        sub_items = children.values()
    elif isinstance(children, (list, tuple)):
        sub_items = children
    else:
        sub_items = ()
    for sub in sub_items:
        count += _count_network_items(sub or {})
    for eable in (item_data.get("editable_nodes") or {}).values():
        count += _count_network_items(eable or {})
    return count


def _norm(path):
    """路径归一（realpath 解析 8.3 短名；失败退回 abspath+normcase）。"""
    try:
        return os.path.normcase(os.path.realpath(path))
    except (OSError, ValueError):
        return os.path.normcase(os.path.abspath(path))


# --------------------------------------------------------------------------
# 库文件夹扫描与安装
# --------------------------------------------------------------------------

def scan_library_files(lib_dirs):
    """递归收集库文件夹里的 .hda 文件（跳过隐藏目录与 backup 目录，排序稳定）。

    backup 目录必须跳过：saveToolRecipe 每次保存都会在
    <库文件夹>/backup/ 里写 HouToolsRecipes_bakN.hda，把备份装进来
    会让已删除的 recipe 随旧定义"复活"，并与现行定义同名冲突。
    """
    files = []
    seen = set()
    for d in lib_dirs or []:
        d = os.path.abspath(d)
        if d in seen:
            continue
        seen.add(d)
        if not os.path.isdir(d):
            log.warning("recipe library dir missing: %s", d)
            continue
        for root, dirs, names in os.walk(d):
            dirs[:] = sorted(x for x in dirs
                             if not x.startswith(".") and x.lower() != "backup")
            for n in sorted(names):
                if n.lower().endswith(".hda"):
                    files.append(os.path.join(root, n))
    return files


def ensure_libraries_installed(lib_dirs):
    """安装库文件夹里的全部 .hda，返回其中确含 recipe 定义的文件集合。

    - 已加载的文件跳过（realpath 比对，重复刷新不重装）；
    - 新安装后检查 definitionsInFile：没有任何 data 类定义的（误放进
      来的普通 OTL）立即卸载——安装是读 recipe 的必要手段，不该有副作用。
    """
    import hou
    loaded = {_norm(p) for p in hou.hda.loadedFiles()}
    data_cat = hou.dataNodeTypeCategory().name()
    keep = set()
    for path in scan_library_files(lib_dirs):
        key = _norm(path)
        if key not in loaded:
            try:
                hou.hda.installFile(path)
            except Exception:
                # 装不上的 .hda：极小的是空壳/垃圾（destroy 残留的空库
                # 连 installFile 都过不去），直接清掉；大文件可能是损坏
                # 的真资产，保留并告警，交给用户处理。
                try:
                    if os.path.getsize(path) < 512:
                        os.remove(path)
                        log.debug("removed tiny invalid library %s", path)
                    else:
                        log.warning("install recipe library %s failed: "
                                    "kept for inspection", path)
                except OSError as exc:
                    log.warning("cannot inspect invalid library %s: %s",
                                path, exc)
                continue
        try:
            has_recipe = any(
                d.nodeTypeCategory() is not None
                and d.nodeTypeCategory().name() == data_cat
                for d in hou.hda.definitionsInFile(path))
        except Exception as exc:
            log.warning("inspect definitions in %s failed: %s", path, exc)
            has_recipe = False
        if has_recipe:
            keep.add(key)
        else:
            try:
                defs_here = hou.hda.definitionsInFile(path)
            except Exception as exc:
                # 二次读取失败（多数是库已被 Houdini 自动卸载）：按空
                # 处理，但不再静默，留下告警痕迹
                log.warning("re-inspect definitions in %s failed: %s",
                            path, exc)
                defs_here = []
            if not defs_here and os.path.isfile(path):
                # 空壳库文件（0 个定义，历史删除残留）：卸载并删掉，
                # 免得每次刷新都走一遍安装→卸载。删除前与安装路径同样
                # 的尺寸护栏：读不到定义但文件不小（可能是损坏的真资产）
                # 保留待查，只清极小的空壳
                try:
                    hou.hda.uninstallFile(path)
                except Exception as exc:
                    log.debug("uninstall empty shell %s failed: %s",
                              path, exc)
                try:
                    size = os.path.getsize(path)
                    if size < 512:
                        os.remove(path)
                        log.debug("removed empty recipe library shell %s",
                                  path)
                    else:
                        log.warning("empty recipe library shell %s is %d "
                                    "bytes; kept for inspection", path, size)
                except OSError as exc:
                    log.warning("cannot inspect empty shell %s: %s",
                                path, exc)
            elif key not in loaded:
                # 本次会话新装、且与 recipe 无关（误放进来的普通 OTL）：
                # 卸载还原，不删除文件本身
                try:
                    hou.hda.uninstallFile(path)
                except Exception as exc:
                    log.warning("uninstall non-recipe library %s failed: %s",
                                path, exc)
    return keep


# --------------------------------------------------------------------------
# 枚举
# --------------------------------------------------------------------------

def list_recipes(lib_dirs=None):
    """枚举用户库里的 recipe（lib_dirs 空 → 返回空列表）。"""
    if not lib_dirs:
        return []
    import hou
    import recipeutils as ru

    keep = ensure_libraries_installed(lib_dirs)
    if not keep:
        return []

    mapping = [
        ("tool", ru.RecipeCategory.tool),
        ("node", ru.RecipeCategory.nodePreset),
        ("parm", ru.RecipeCategory.parmPreset),
        ("decoration", ru.RecipeCategory.decoration),
        ("parmTemplate", ru.RecipeCategory.parmTemplate),
        ("data", ru.RecipeCategory.data),
    ]
    hfs = hou.text.expandString("$HFS")
    seen = set()
    out = []
    for cat_key, cat in mapping:
        try:
            names = ru.recipeNames(cat, include_label=False, pad=False)
        except Exception as exc:
            log.warning("recipeNames(%s) failed: %s", cat_key, exc)
            continue
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            # 先按库文件过滤：非用户库（出厂 $HFS / Labs / 偏好库）的
            # definition 不解析 data.recipe.json——全量枚举里大头是出厂
            # recipe，逐个 JSON 解析白烧时间
            lib_path = _definition_library(name)
            if not lib_path or _norm(lib_path) not in keep:
                continue
            info = RecipeInfo(name=name, category=cat_key, library=lib_path)
            _read_header(info, hfs)
            if info.library and _norm(info.library) in keep:
                out.append(info)
    order = {k: i for i, k in enumerate(CATEGORY_LABELS)}
    out.sort(key=lambda r: (order.get(r.category, 99),
                            r.display_label.lower()))
    return out


def _definition_library(name):
    """轻量取 definition 所在库文件路径（不解析 recipe JSON）。"""
    import hou
    try:
        ntype = hou.nodeType(hou.dataNodeTypeCategory(), name)
        defn = ntype.definition() if ntype else None
        return defn.libraryFilePath() if defn else None
    except Exception as exc:
        log.debug("read library path for %s failed: %s", name, exc)
        return None


def _read_header(info, hfs):
    """从 definition 的 data.recipe.json section 读头部元信息。"""
    import hou
    try:
        ntype = hou.nodeType(hou.dataNodeTypeCategory(), info.name)
        defn = ntype.definition() if ntype else None
        if defn is None:
            return
        info.library = defn.libraryFilePath()
        try:
            info.under_hfs = os.path.realpath(
                info.library).lower().startswith(
                    os.path.realpath(hfs).lower() + os.sep)
        except (OSError, ValueError):
            info.under_hfs = False
        sec = defn.sections().get("data.recipe.json")
        if sec is None:
            return
        data = json.loads(sec.contents())
        props = data.get("properties") or {}
        info.patterns = list(props.get("nodetype_patterns") or [])
        tool = data.get("tool") or {}

        # label 与子菜单的实际存储位置随 recipe_category 不同（22.0.429
        # 实测，与 recipe_format 文档页有出入）：
        # - tab_tool_recipe / preset 类: properties.label / properties.submenu
        # - tool_recipe(shelf 式): label 在 tool.tool_labels[]（[0]=tab 名，
        #   [1]=shelf 名），子菜单在 tool.tab_submenus[]
        info.label = props.get("label") or ""
        labels = tool.get("tool_labels") or []
        for lb in labels:
            if lb:
                info.label = info.label or str(lb)
                break
        # 不在此处回退内部名：display_label 属性统一处理"末段"回退

        subs = props.get("submenu")
        if not subs:
            subs = tool.get("tab_submenus") or tool.get("tab_submenu")
        if isinstance(subs, (list, tuple)):
            info.submenu = ",".join(str(s) for s in subs if s)
        elif subs:
            info.submenu = str(subs)

        # 卡片展示三项：目标网络类别、内含节点数、制作版本。
        # data 键不总是 dict（ramp 类是 list、recipebuilder 是字符串等），
        # 节点数只对"网络条目集合"形态的 recipe 有意义，其余一律 -1
        rdata = data.get("data")
        if isinstance(rdata, dict):
            children = rdata.get("children")
            if isinstance(children, dict) and children:
                info.node_count = sum(_count_network_items(it)
                                      for it in children.values())
            elif isinstance(children, (list, tuple)) and children:
                info.node_count = sum(_count_network_items(it or {})
                                      for it in children)
            elif rdata.get("type"):
                # node/parm/decoration 预设：data 本身就是单个节点的描述
                info.node_count = 1
        cats = tool.get("network_categories") or []
        info.net_category = (str(cats[0]) if cats
                             else str(props.get("nodetype_category") or ""))

        header = data.get("info") or {}
        info.comment = header.get("comment") or ""
        info.author = header.get("author") or ""
        info.houdini_version = header.get("houdini_version") or ""
    except Exception as exc:
        # 头部信息读不到就用内部名兜底，不隐藏该 recipe
        log.warning("read recipe header failed for %s: %s", info.name, exc)


def get_recipe(name, lib_dirs=None):
    """按内部名取单个 RecipeInfo（找不到返回 None）。"""
    for info in list_recipes(lib_dirs):
        if info.name == name:
            return info
    return None


# --------------------------------------------------------------------------
# 应用
# --------------------------------------------------------------------------

def selected_nodes():
    import hou
    return list(hou.selectedNodes())


def current_network_editor():
    """当前桌面上的网络编辑器 pane（没有则 None）。"""
    import hou
    try:
        return hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
    except Exception as exc:
        log.debug("no network editor: %s", exc)
        return None


def network_editor_under_cursor():
    """鼠标光标下的 pane，是网络编辑器才返回（拖拽落点判定）。"""
    import hou
    try:
        pane = hou.ui.paneTabUnderCursor()
    except Exception as exc:
        log.debug("paneTabUnderCursor failed: %s", exc)
        return None
    try:
        if pane is not None and pane.type() == hou.paneTabType.NetworkEditor:
            return pane
    except Exception as exc:
        log.debug("check pane type failed: %s", exc)
    return None


# 官方两套调用模板（hrecipes/api/sessiontool.py），照抄参数只改调用方式：
# shelf = 工具体验（双击/应用按钮）：立即放置 + 框选 + 启用提示
# drag  = 拖拽落点（松手即建）：接入落点导线、不框选、不带提示
_SHELF_OPTS = dict(
    tool_inputs=[],
    tool_outputs=[],
    drop_on_wire=False,
    click_to_place=False,
    avoid_overlap=False,
    frame=False,   # 官方默认 True 会把视图拉得极近；改由 ensure_items_visible
    prompt=True,   # 只保证"创建物可见"，不动缩放
    skip_notes=True,
)
_DRAG_OPTS = dict(
    tool_inputs=[],
    tool_outputs=[],
    drop_on_wire=True,
    click_to_place=False,
    avoid_overlap=False,
    frame=False,
    prompt=False,
    skip_notes=False,
)


# 网络层级展示名（层级不匹配提示用）
_CONTEXT_HINTS = {
    "Object": "OBJ（/obj 层级）",
    "Sop": "SOP（geo 内部）",
    "Lop": "LOP（Solaris）",
    "Dop": "DOP",
    "Cop": "COP",
    "Cop2": "COP2",
    "Chop": "CHOP",
}


def _read_tool_network_categories(name):
    """tool recipe 声明的可用网络类别（tool.network_categories），无则 []。"""
    import hou
    try:
        ntype = hou.nodeType(hou.dataNodeTypeCategory(), name)
        defn = ntype.definition() if ntype else None
        sec = defn.sections().get("data.recipe.json") if defn else None
        if sec is None:
            return []
        data = json.loads(sec.contents())
        return [str(c) for c in
                (data.get("tool") or {}).get("network_categories") or []]
    except Exception as exc:
        log.debug("read network_categories for %s failed: %s", name, exc)
        return []


class ContextMismatch(RuntimeError):
    """recipe 与目标网络层级不匹配。

    这是预期内的用户操作反馈（层级点错了），UI 红字提示即可——
    调用方不得把它当程序错误打日志/堆栈。
    """


def _check_tool_context(name, pane):
    """tool recipe 与目标网络层级的匹配检查（官方 Tab 菜单按
    network_categories 过滤，面板直连应用绕过了这层，须自己补上），
    不匹配抛 RuntimeError。声明缺失/上下文读不到时放行。"""
    import hou
    if pane is None:
        return
    cats = _read_tool_network_categories(name)
    if not cats:
        return
    try:
        # ⚠ pwd() 是"容纳当前网络的节点"（geo1 内部时 pwd=/obj/geo1），
        # 它的 type().category() 是 Object（geo 本身是 OBJ 节点）；
        # 编辑器显示的网络类别必须用 childTypeCategory()——
        # /obj→Object、/obj/geo1→Sop、/obj/geo1/dopnet1→Dop（实测）。
        pwd_cat = pane.pwd().childTypeCategory().name()
    except Exception as exc:
        log.debug("read editor pwd category failed: %s", exc)
        return
    if pwd_cat not in cats:
        need = " / ".join(_CONTEXT_HINTS.get(c, c) for c in cats)
        have = _CONTEXT_HINTS.get(pwd_cat, pwd_cat)
        raise ContextMismatch(
            "层级不匹配：该 recipe 需要 {} 网络，当前是 {} 网络".format(need, have))


def ensure_items_visible(name, result, editor):
    """frame=False 创建后保证创建物可见：锚点落在视图外时把整组节点
    平移到视图中心（只平移、不改缩放）。已可见则不动。"""
    import hou
    items = (result or {}).get("items") or {}
    if not items or editor is None:
        return
    try:
        anchor_name = None
        try:
            data = hou.data.dataFromRecipe(name) or {}
            anchor_name = (data.get("tags") or {}).get("target_tag")
        except Exception:
            pass
        anchor = items.get(anchor_name) or next(iter(items.values()))
        bounds = editor.visibleBounds()
        lo = bounds.min()
        hi = bounds.max()
        pos = anchor.position()
        if lo.x() <= pos.x() <= hi.x() and lo.y() <= pos.y() <= hi.y():
            return  # 已在视野内
        _align_items_to_position(name, result, bounds.center())
    except Exception as exc:
        log.debug("ensure items visible failed: %s", exc)


def apply_tool_recipe(name, pane=None, position=None, mode="shelf"):
    """应用 tool recipe。

    mode="shelf"：官方工具架体验——立即在 pane 的当前网络里创建
    （参数照抄官方 _shelf_tool_script，仅 frame 改 False 避免视角
    拉近），创建后由 ensure_items_visible 保证可见。
    mode="drag"：拖拽松手即建；给 position（hou.Vector2 网络坐标）时
    把整组节点平移对齐到该点——apply 在此模式下的落点（视图中心/
    粘贴位置）官方 API 不保证是鼠标点，所以按返回的 items 锚点平移兜底。
    未知 mode 显式归一化为 drag 并告警，避免静默走错分支。
    两者都会先做网络层级匹配检查，不匹配抛 RuntimeError（含中文提示）。
    """
    import hou
    if mode not in ("shelf", "drag"):
        log.warning("apply_tool_recipe: unknown mode %r, treated as drag",
                    mode)
        mode = "drag"
    _check_tool_context(name, pane)
    kwargs = dict(_SHELF_OPTS if mode == "shelf" else _DRAG_OPTS)
    kwargs["pane"] = pane
    result = hou.data.applyToolRecipe(name, **kwargs)
    if mode == "shelf":
        ensure_items_visible(name, result, pane)
    elif position is not None:
        _align_items_to_position(name, result, position)
    return result


def _align_items_to_position(name, result, position):
    """把 apply 出来的整组节点平移，使 anchor（target_tag 节点）落在
    position 上；apply 已落准（误差 < 0.01）则跳过。"""
    import hou
    items = (result or {}).get("items") or {}
    if not items:
        return
    anchor_name = None
    try:
        data = hou.data.dataFromRecipe(name) or {}
        anchor_name = (data.get("tags") or {}).get("target_tag")
    except Exception as exc:
        log.debug("dataFromRecipe for alignment failed: %s", exc)
    anchor = items.get(anchor_name) or next(iter(items.values()))
    cur = anchor.position()
    dx = position.x() - cur.x()
    dy = position.y() - cur.y()
    if abs(dx) < 0.01 and abs(dy) < 0.01:
        return
    for node in items.values():
        p = node.position()
        node.setPosition(hou.Vector2(p.x() + dx, p.y() + dy))


def apply_node_preset(name, node):
    import hou
    if node is None:
        raise RuntimeError("请先在场景中选中目标节点")
    hou.data.applyNodePresetRecipe(name, node)


def apply_decoration(name, node):
    import hou
    if node is None:
        raise RuntimeError("请先选中装饰的中心节点")
    hou.data.applyDecorationRecipe(name, central_node=node)


def apply_parm_preset(name, node=None):
    """参数预设：从保存的数据里取参数名（metadata.path 的末段），
    在选中节点上找同名参数应用。"""
    import hou
    if node is None:
        raise RuntimeError("请先在场景中选中目标节点")
    data = hou.data.dataFromRecipe(name) or {}
    path = (data.get("metadata") or {}).get("path") or ""
    base = path.rsplit("/", 1)[-1].split("#")[0]
    parm = node.parmTuple(base) or node.parm(base)
    if parm is None:
        raise RuntimeError("节点 {} 上找不到参数 {}".format(node.path(), base))
    hou.data.applyParmPresetRecipe(name, parm)


# --------------------------------------------------------------------------
# 删除
# --------------------------------------------------------------------------

def _is_under(path, root):
    try:
        return os.path.realpath(path).lower().startswith(
            os.path.realpath(root).lower() + os.sep)
    except (OSError, ValueError):
        return False


def delete_recipe(name):
    """删除 recipe（data 资产 definition）。出厂 recipe（$HFS 下）拒绝。

    所在 .hda 因删除而变空时连文件一起清理（卸载 + 删除），不留空壳；
    文件里还有别的 recipe（如队友分享的多配方库）则只摘除这一个。
    """
    import hou
    ntype = hou.nodeType(hou.dataNodeTypeCategory(), name)
    defn = ntype.definition() if ntype else None
    if defn is None:
        raise RuntimeError("找不到 recipe 资产: {}".format(name))
    lib = defn.libraryFilePath()
    hfs = hou.text.expandString("$HFS")
    if _is_under(lib, hfs):
        raise RuntimeError("出厂 recipe（{}）不可删除".format(lib))
    defn.destroy()
    # 清理语义（无头实测）：destroy 掉文件里**最后一个**定义时，Houdini
    # 会自动卸载该库（definitionsInFile 对未加载文件抛 OperationFailed），
    # 但 83 字节空壳文件留在磁盘；还有幸存定义时文件保持加载、内容完好。
    # 所以：读不到定义（含 OperationFailed）→ 视为空 → 卸载 + 删空壳；
    # 有幸存定义 → 不动文件。
    try:
        if os.path.isfile(lib) and not _is_under(lib, hfs):
            try:
                defs_left = hou.hda.definitionsInFile(lib)
            except Exception:
                defs_left = []  # 库已被 Houdini 自动卸载 = 已无定义
            if not defs_left:
                try:
                    hou.hda.uninstallFile(lib)
                except Exception as exc:
                    # 未加载的文件卸载会失败，直接删文件即可
                    log.debug("uninstall %s after delete failed: %s",
                              lib, exc)
                try:
                    os.remove(lib)
                except OSError as exc:
                    log.warning("cannot remove empty recipe library %s: %s",
                                lib, exc)
    except Exception as exc:
        log.warning("cleanup after delete %s failed: %s", name, exc)
