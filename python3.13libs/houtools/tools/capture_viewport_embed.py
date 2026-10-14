"""Capture the 3D viewport and embed the image beside the right-clicked node.

The OPmenu.xml at the repo root (loaded via HOUDINI_PATH) hangs "Capture
Viewport to Node [HT]" on every node; the menu scriptCode is dispatched via
dev.dispatcher, landing at this module's run(kwargs).  The companion entry
"Delete Viewport Capture [HT]" (delete_viewport_capture.py) is a thin
dispatcher onto delete_capture() in this module.

机制（需求约定）：
- 图片落盘 $HIP/HouTools_cfg/captures/<路径转横线>.jpg，背景图只存
  "$HIP/..." 变量形态的路径引用——hip 不膨胀，图删了显示即失效；
- 截图走官方单帧 flipbook（SideFX 论坛标准模式），beautyPassOnly 去掉
  网格/gizmo 等视口覆盖层；
- hou.NetworkImage.setRelativeToPath(节点路径) 把图钉在节点上（节点移动图
  跟随），rect 存锚点局部坐标——官方文档明确"rect 会按锚点位置平移"；
- 落库双步：setBackgroundImages 改编辑器临时显示列表（立即生效）+
  nodegraphutils.saveBackgroundImages 写网络节点 userData（键
  backgroundimages，切层级后编辑器按它还原布局，缺了图就丢）；
- 同一节点重截 = 同名覆盖：文件覆写 + 移除该节点旧背景图条目再追加新条目；
  同路径覆写后编辑器位图缓存不重读，须清空重放强制刷新（官方 Reload
  Images 手法，见 _embed）；
- 节点改名/删除跟随：节点 userData 写 ht_capture_image 标记，并挂
  NameChanged/BeingDeleted 回调（会话级，hip 重开失效）。改名时磁盘文件、
  背景图条目、标记一并跟到新路径（新路径必然缓存 miss 自动重读，无需
  清空重放）；删除时反向清理。加载/清空场景会群发 BeingDeleted，由
  hipFile 事件钩子置 teardown 旗标挡住，防止换工程把上一工程的截图文件
  当孤儿误删。
"""

import os

from houtools.core.log import get_logger

log = get_logger("tools.capture_viewport_embed")

# 截图落盘目录（$HIP 变量形态；展开后的绝对路径只用于磁盘读写，
# 背景图引用始终存变量形态，工程挪动/换机后链接仍有效）
CAPTURE_DIR = "$HIP/HouTools_cfg/captures"

# 背景图显示宽度（网络坐标单位，约 2 个默认节点宽），高按图片宽高比推算。
# 用户指定再小一倍（原 4.0），调试期可继续调。
DISPLAY_WIDTH_UNITS = 2.0
# 图与节点右缘的间隙
NODE_GAP_UNITS = 0.5

# 节点侧标记（capture 时写入，记录当前截图文件名）：改名跟随/删除清理/
# 右键删除菜单的显示判据都靠它。与 OPmenu.xml 的 context 表达式双处同步。
MARKER_KEY = "ht_capture_image"

# 会话级状态。teardown 由 hipFile 事件钩子维护（见 _ensure_hipfile_hook）；
# load_pending 处理加载序列 BeforeLoad→BeforeClear→AfterClear→AfterLoad 中
# AfterClear 先于 AfterLoad 的嵌套（不能让 AfterClear 提前解除防护）。
_STATE = {"teardown": False, "load_pending": False, "hipfile_hooked": False}


def capture_filename(node_path):
    """节点绝对路径 → 图片文件名（含 .jpg）。

    规则：去掉开头 "/"，路径里每个节点名中的 "-" 转义成 "~"，再把 "/" 转
    成 "-"。Houdini 节点名只允许 [A-Za-z0-9._-]，"~" 天然不冲突，编码可逆
    且单射——"/obj/net1/sub-foo" 与 "/obj/net1/sub/foo" 分别得到
    obj-net1-sub~foo.jpg 与 obj-net1-sub-foo.jpg，互不撞名；同一节点永远
    映射同一文件名，重截即同名覆盖。
    """
    return node_path.strip("/").replace("-", "~").replace("/", "-") + ".jpg"


def decode_capture_filename(filename):
    """capture_filename 的逆变换（不含开头 "/"），测试注入性用。"""
    name = filename[: -len(".jpg")] if filename.endswith(".jpg") else filename
    return name.replace("-", "/").replace("~", "-")


def hip_capture_path(filename):
    """文件名 → 背景图引用用的 $HIP 变量形态路径（与 CAPTURE_DIR 一致）。"""
    return "{}/{}".format(CAPTURE_DIR, filename)


def is_stale_capture(image, node_path, hip_paths):
    """既有背景图是否属于该节点的旧截图（重截覆盖/删除清理的判据）。

    hip_paths 接受 str 或可迭代：除当前文件名外还可传入节点上一次的
    marker 文件名——跨会话改名（回调已失效）后锚点与文件名都是旧值，
    靠 marker 才能把死条目找出来清掉。两个判据：锚点等于该节点路径，
    或路径引用等于任一候选 $HIP 路径。
    """
    if isinstance(hip_paths, str):
        hip_paths = (hip_paths,)
    return image.relativeToPath() == node_path or image.path() in hip_paths


def retarget_capture_entries(images, old_anchor, old_hip_path,
                             new_anchor, new_hip_path):
    """把旧锚点/旧路径的截图条目改指新节点（改名跟随的纯逻辑部分）。

    images 为 duck-typed NetworkImage（path/relativeToPath 读、setPath/
    setRelativeToPath 写），返回是否有条目被改写。
    """
    hit = False
    for image in images:
        if image.relativeToPath() == old_anchor or image.path() == old_hip_path:
            image.setRelativeToPath(new_anchor)
            image.setPath(new_hip_path)
            hit = True
    return hit


def _find_scene_viewer():
    """取当前 3D 视窗：可见（isCurrentTab）的 SceneViewer 优先，多个取
    最后一个（paneTabs 枚举序，社区成熟启发式）；没有可见视窗报错。
    """
    import hou

    viewers = [t for t in hou.ui.paneTabs()
               if t.type() == hou.paneTabType.SceneViewer]
    if not viewers:
        raise RuntimeError("场景里没有 Scene Viewer 面板")
    visible = [t for t in viewers if t.isCurrentTab()]
    if not visible:
        raise RuntimeError("没有可见的 3D 视窗（Scene Viewer 被折叠或切到了其他标签页）")
    return visible[-1]


def _find_network_editor(node, kwargs):
    """取右键发生所在的网络编辑器：菜单 kwargs 自带 editor 优先，其次按
    节点父级匹配 pwd，再退回光标下/任意可见的 NetworkEditor。
    """
    import hou

    editor = (kwargs or {}).get("editor")
    if editor is not None:
        return editor
    parent = node.parent()
    candidates = [t for t in hou.ui.paneTabs()
                  if t.type() == hou.paneTabType.NetworkEditor]
    visible = [t for t in candidates if t.isCurrentTab()]
    for pool in (visible, candidates):
        for tab in pool:
            if tab.pwd() == parent:
                return tab
    if visible:
        return visible[0]
    if candidates:
        return candidates[0]
    raise RuntimeError("找不到网络编辑器面板")


def _editors_showing(parent):
    """所有正显示 parent 网络的 NetworkEditor 面板（显示刷新用）。"""
    import hou

    return [t for t in hou.ui.paneTabs()
            if t.type() == hou.paneTabType.NetworkEditor and t.pwd() == parent]


def _capture_to_file(viewer, file_abs):
    """单帧 flipbook 截图当前视口 → file_abs（扩展名定格式，jpg 直出）。

    用 flipbookSettings().stash() 复制用户当前对话框设置为底，再钉死与
    本功能相关的项：不弹 MPlay、不重置模拟（防截图触发重算）、beauty
    pass 去掉网格/gizmo 覆盖层、按视口原始分辨率输出。flipbook 对直接
    给 output 路径的情况不弹保存对话框（SideFX 论坛确认）。
    """
    import hou

    frame = int(hou.frame())
    settings = viewer.flipbookSettings().stash()
    settings.frameRange((frame, frame))
    settings.output(file_abs)
    settings.outputToMPlay(False)
    settings.initializeSimulations(False)
    settings.beautyPassOnly(True)
    settings.useResolution(False)
    settings.outputZoom(100)
    settings.appendFramesToCurrent(False)
    viewer.flipbook(viewer.curViewport(), settings)


def _embed(editor, node, hip_path, pix_w, pix_h, prev_marker=None):
    """移除该节点旧截图条目后，把新背景图钉在节点右侧。

    rect 存锚点局部坐标（原点 = node.position()）：文档明确 rect 显示时
    会按锚点位置平移，故 x 从节点右缘（size.x/2 + 间隙）起，y 以节点
    中心竖直居中；显示宽 DISPLAY_WIDTH_UNITS，高按图片宽高比推算。

    落库必须走官方 nodegraphutils.saveBackgroundImages 把整份布局写到
    网络节点 userData（键 backgroundimages）——setBackgroundImages 只改
    编辑器临时显示列表，切换层级后编辑器按该 userData 还原布局，不写它
    图就丢（首版实测踩坑；undo 组由它自建，勿再外包 hou.undos.group 防
    嵌套）。返回是否成功写入持久层（锁定 HDA 内部网络等不可写场景为
    False，截图文件本身已落盘）。
    """
    import hou
    import nodegraphutils

    width = DISPLAY_WIDTH_UNITS
    height = width * float(pix_h) / float(pix_w)
    x0 = node.size().x() / 2.0 + NODE_GAP_UNITS
    image = hou.NetworkImage()
    image.setPath(hip_path)
    image.setRelativeToPath(node.path())
    image.setRect(hou.BoundingRect(
        x0, -height / 2.0, x0 + width, height / 2.0))

    node_path = node.path()
    stale_paths = [hip_path]
    if prev_marker and prev_marker != hip_path.rsplit("/", 1)[-1]:
        # 跨会话改名（回调已失效）后重截：marker 还是旧文件名，靠它把
        # 死锚点旧条目一并清掉（O(1)，无扫描）
        stale_paths.append(hip_capture_path(prev_marker))
    originals = editor.backgroundImages()
    kept = tuple(im for im in originals
                 if not is_stale_capture(im, node_path, stale_paths))
    images = kept + (image,)
    if len(kept) != len(originals):
        # 重截（替换了旧条目）：编辑器把位图按路径缓存，同名覆写文件后
        # setBackgroundImages 重摆条目也不重读。用官方 "Reload Images" 右键
        # 菜单的手法（nodegraphpopupmenus.py BackgroundImageContextMenu）：
        # 清空再整份放回，强制重读文件。不得用层级往返刷新——导航会触发
        # 解算重算卡死会话（用户实测否决）。
        editor.setBackgroundImages([])
    editor.setBackgroundImages(images)
    try:
        nodegraphutils.saveBackgroundImages(editor.pwd(), images, editor)
    except Exception:
        log.exception("背景图持久化失败 (%s)", editor.pwd().path())
        return False
    return True


def _ensure_hipfile_hook():
    """装 hipFile 事件钩子（会话一次）：加载/清空场景期间置 teardown 旗标。

    拆除期间 BeingDeleted 会对旧场景节点群发（实测），不挡住的话跟随回调
    会把上一工程的截图文件当孤儿删掉。加载序列为 BeforeLoad→BeforeClear→
    AfterClear→AfterLoad，AfterClear 先于 AfterLoad，须用 load_pending
    区分"清空完毕"与"加载中"。回调首参为 (event_type,) 元组（实测）。
    """
    import hou

    if _STATE["hipfile_hooked"]:
        return

    def on_hip_event(event_types=(), **kwargs):
        for etype in event_types:
            if etype in (hou.hipFileEventType.BeforeLoad,
                         hou.hipFileEventType.BeforeClear):
                _STATE["teardown"] = True
                if etype == hou.hipFileEventType.BeforeLoad:
                    _STATE["load_pending"] = True
            elif etype in (hou.hipFileEventType.AfterLoad,
                           hou.hipFileEventType.AfterClear):
                if etype == hou.hipFileEventType.AfterLoad:
                    _STATE["load_pending"] = False
                _STATE["teardown"] = _STATE["load_pending"]

    try:
        hou.hipFile.addEventCallback(on_hip_event)
        _STATE["hipfile_hooked"] = True
    except Exception:
        log.exception("hipFile 拆除防护钩子注册失败")


def _make_rename_cb(node, state):
    def on_name_changed(**kwargs):
        try:
            _follow_rename(node, state)
        except Exception:
            log.exception("截图改名跟随失败 (%s)", state.get("anchor"))
    on_name_changed._ht_capture_cb = True
    return on_name_changed


def _make_delete_cb(state):
    def on_deleted(**kwargs):
        try:
            _on_node_deleted(state)
        except Exception:
            log.exception("截图删除清理失败 (%s)", state.get("anchor"))
    on_deleted._ht_capture_cb = True
    return on_deleted


def _ensure_watch(node):
    """给节点挂改名/删除跟随回调（先摘旧再挂新，重复截图不叠加）。

    两个回调共享一份 state（anchor/filename 由改名跟随自更新），保证
    "改名后再删除"用的是新文件名。会话级，hip 重开自动失效。
    """
    import hou

    parent = node.parent()
    if parent is None:
        return
    _ensure_hipfile_hook()
    for types, cb in list(node.eventCallbacks()):
        if getattr(cb, "_ht_capture_cb", False):
            try:
                node.removeEventCallback(tuple(types), cb)
            except Exception:
                log.exception("摘除旧截图跟随回调失败")
    state = {"anchor": node.path(), "filename": node.userData(MARKER_KEY),
             "parent_path": parent.path()}
    node.addEventCallback((hou.nodeEventType.NameChanged,),
                          _make_rename_cb(node, state))
    node.addEventCallback((hou.nodeEventType.BeingDeleted,),
                          _make_delete_cb(state))


def _unwatch(node):
    """摘除本工具挂在节点上的全部跟随回调（删除截图时调用）。"""
    import hou

    for types, cb in list(node.eventCallbacks()):
        if getattr(cb, "_ht_capture_cb", False):
            try:
                node.removeEventCallback(tuple(types), cb)
            except Exception:
                log.exception("摘除截图跟随回调失败")


def _follow_rename(node, state):
    """NameChanged 回调主体：改名后磁盘文件、背景图条目、标记一并跟到新路径。

    新路径必然缓存 miss，setBackgroundImages 即自动重读，无需清空重放；
    改名撤销（Ctrl+Z）会再次触发本回调，原路改回，撤销语义自洽。
    """
    import hou
    import nodegraphutils

    new_path = node.path()
    if new_path == state["anchor"]:
        return
    old_anchor = state["anchor"]
    old_filename = state["filename"]
    new_filename = capture_filename(new_path)
    old_hip = hip_capture_path(old_filename)
    new_hip = hip_capture_path(new_filename)

    dir_abs = hou.text.expandString(CAPTURE_DIR)
    old_abs = os.path.join(dir_abs, old_filename)
    new_abs = os.path.join(dir_abs, new_filename)
    if os.path.isfile(old_abs):
        os.replace(old_abs, new_abs)
    elif not os.path.isfile(new_abs):
        log.warning("改名跟随：找不到截图文件 %s", old_abs)

    parent = node.parent()
    images = []
    if parent is not None:
        images = list(nodegraphutils.loadBackgroundImages(parent))
        if retarget_capture_entries(images, old_anchor, old_hip,
                                    new_path, new_hip):
            nodegraphutils.saveBackgroundImages(parent, images)
        for editor in _editors_showing(parent):
            editor.setBackgroundImages(images)

    try:
        node.setUserData(MARKER_KEY, new_filename)
    except Exception:
        log.warning("改名后无法更新截图标记 (%s)", new_path)
    state["anchor"] = new_path
    state["filename"] = new_filename
    log.info("截图跟随改名: %s -> %s", old_anchor, new_path)


def _on_node_deleted(state):
    """BeingDeleted 回调主体：节点删除 → 删背景图条目 + 删磁盘文件。

    state 记录最后已知的锚点/文件名/父网络路径（节点对象本身删除中不可
    靠，全程不触碰它）。换工程/清场景的批量拆除已在 _STATE["teardown"]
    拦下（文件留在磁盘属正常——那是上一个工程的截图）。
    """
    import hou
    import nodegraphutils

    if _STATE["teardown"] or hou.hipFile.isLoadingHipFile():
        return
    hip_path = hip_capture_path(state["filename"])
    parent = hou.node(state["parent_path"])
    if parent is not None:
        all_images = list(nodegraphutils.loadBackgroundImages(parent))
        kept = [im for im in all_images
                if not is_stale_capture(im, state["anchor"], hip_path)]
        if len(kept) != len(all_images):
            with hou.UndosDisabler():
                # 文件已删不可恢复，条目清理一并退出 undo——防 Ctrl+Z
                # 恢复节点后留下指向已删文件的死条目
                nodegraphutils.saveBackgroundImages(parent, kept)
            for editor in _editors_showing(parent):
                editor.setBackgroundImages(kept)
    file_abs = os.path.join(hou.text.expandString(CAPTURE_DIR),
                            state["filename"])
    if os.path.isfile(file_abs):
        try:
            os.remove(file_abs)
        except OSError:
            log.exception("删除截图文件失败: %s", file_abs)
    log.info("节点删除，已清理其截图 (%s)", state["anchor"])


def delete_capture(node):
    """右键「Delete Viewport Capture」主体：删背景图条目 + 删磁盘文件 +
    清标记 + 摘除跟随回调。返回 (是否删了条目, 是否删了文件)。"""
    import hou
    import nodegraphutils

    filename = node.userData(MARKER_KEY)
    if not filename:
        raise RuntimeError("节点 {} 没有截图标记".format(node.path()))
    hip_path = hip_capture_path(filename)

    removed_entry = False
    parent = node.parent()
    kept = ()
    if parent is not None:
        all_images = list(nodegraphutils.loadBackgroundImages(parent))
        kept = [im for im in all_images
                if not is_stale_capture(im, node.path(), hip_path)]
        removed_entry = len(kept) != len(all_images)
        if removed_entry:
            nodegraphutils.saveBackgroundImages(parent, kept)
            for editor in _editors_showing(parent):
                editor.setBackgroundImages(kept)

    removed_file = False
    file_abs = os.path.join(hou.text.expandString(CAPTURE_DIR), filename)
    if os.path.isfile(file_abs):
        try:
            os.remove(file_abs)
            removed_file = True
        except OSError:
            log.exception("删除截图文件失败: %s", file_abs)

    if node.userData(MARKER_KEY) is not None:
        try:
            node.destroyUserData(MARKER_KEY)
        except Exception:
            log.exception("清除截图标记失败 (%s)", node.path())
    _unwatch(node)
    return removed_entry, removed_file


def run(kwargs=None):
    """OPmenu 菜单入口；kwargs 为菜单脚本上下文（含 node，可能含 editor）。"""
    import hou

    node = (kwargs or {}).get("node")
    if node is None:
        return
    persisted = False
    try:
        viewer = _find_scene_viewer()
        editor = _find_network_editor(node, kwargs)

        filename = capture_filename(node.path())
        dir_abs = hou.text.expandString(CAPTURE_DIR)
        file_abs = os.path.join(dir_abs, filename).replace("\\", "/")
        try:
            os.makedirs(dir_abs, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                "无法创建截图目录 {}（工程是否已保存？）: {}".format(
                    dir_abs, exc))

        _capture_to_file(viewer, file_abs)
        if not os.path.isfile(file_abs):
            raise RuntimeError(
                "flipbook 未生成截图文件: {}".format(file_abs))

        pix_w, pix_h = hou.imageResolution(file_abs)
        if not pix_w or not pix_h:
            raise RuntimeError("截图分辨率读取失败: {}".format(file_abs))

        prev_marker = node.userData(MARKER_KEY)
        persisted = _embed(editor, node, hip_capture_path(filename),
                           pix_w, pix_h, prev_marker)
        try:
            node.setUserData(MARKER_KEY, filename)
        except Exception:
            log.warning("无法写入截图标记 (%s)", node.path())
        _ensure_watch(node)
    except RuntimeError as exc:
        log.warning("%s", exc)
        hou.ui.setStatusMessage(
            "HouTools: {}".format(exc), severity=hou.severityType.Error)
        return
    except Exception:
        log.exception("capture_viewport_embed crashed")
        hou.ui.setStatusMessage(
            "HouTools: 视窗截图失败（详见日志）",
            severity=hou.severityType.Error)
        return
    if persisted:
        hou.ui.setStatusMessage(
            "HouTools: 视窗截图已嵌入 {}（{}x{}，{}）".format(
                node.path(), pix_w, pix_h, hip_capture_path(filename)))
    else:
        hou.ui.setStatusMessage(
            "HouTools: 截图已落盘但背景图写入失败（网络不可写？）: {}".format(
                file_abs), severity=hou.severityType.Warning)
