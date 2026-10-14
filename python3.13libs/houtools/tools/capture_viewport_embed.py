"""Capture the 3D viewport and embed the image beside the right-clicked node.

The OPmenu.xml at the repo root (loaded via HOUDINI_PATH) hangs "Capture
Viewport to Node [HT]" on every node; the menu scriptCode is dispatched via
dev.dispatcher, landing at this module's run(kwargs).

机制（需求约定）：
- 图片落盘 $HIP/HouTools_cfg/captures/<路径转横线>.jpg，背景图只存
  "$HIP/..." 变量形态的路径引用——hip 不膨胀，图删了显示即失效；
- 截图走官方单帧 flipbook（SideFX 论坛标准模式），beautyPassOnly 去掉
  网格/gizmo 等视口覆盖层；
- hou.NetworkImage.setRelativeToPath(节点路径) 把图钉在节点上（节点移动图
  跟随），rect 存锚点局部坐标——官方文档明确"rect 会按锚点位置平移"；
- 同一节点重截 = 同名覆盖：文件覆写 + 移除该节点旧背景图条目再追加新条目。
"""

import os

from houtools.core.log import get_logger

log = get_logger("tools.capture_viewport_embed")

# 截图落盘目录（$HIP 变量形态；展开后的绝对路径只用于磁盘读写，
# 背景图引用始终存变量形态，工程挪动/换机后链接仍有效）
CAPTURE_DIR = "$HIP/HouTools_cfg/captures"

# 背景图显示宽度（网络坐标单位，约 4 个默认节点宽），高按图片宽高比推算。
# 调试期可调常数（用户指定后续再定具体大小）。
DISPLAY_WIDTH_UNITS = 4.0
# 图与节点右缘的间隙
NODE_GAP_UNITS = 0.5


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


def is_stale_capture(image, node_path, hip_path):
    """既有背景图是否属于该节点的旧截图（重截覆盖时移除）。

    两个判据：锚点等于该节点（现行条目），或路径引用等于本次目标路径
    （节点改名/挪窝后残留的旧条目——文件名由旧路径编码而来，仍指向
    同一份文件）。
    """
    return image.relativeToPath() == node_path or image.path() == hip_path


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


def _embed(editor, node, hip_path, pix_w, pix_h):
    """移除该节点旧截图条目后，把新背景图钉在节点右侧（单个 undo 槽）。

    rect 存锚点局部坐标（原点 = node.position()）：文档明确 rect 显示时
    会按锚点位置平移，故 x 从节点右缘（size.x/2 + 间隙）起，y 以节点
    中心竖直居中；显示宽 DISPLAY_WIDTH_UNITS，高按图片宽高比推算。
    """
    import hou

    width = DISPLAY_WIDTH_UNITS
    height = width * float(pix_h) / float(pix_w)
    x0 = node.size().x() / 2.0 + NODE_GAP_UNITS
    image = hou.NetworkImage()
    image.setPath(hip_path)
    image.setRelativeToPath(node.path())
    image.setRect(hou.BoundingRect(
        x0, -height / 2.0, x0 + width, height / 2.0))

    node_path = node.path()
    kept = tuple(im for im in editor.backgroundImages()
                 if not is_stale_capture(im, node_path, hip_path))
    with hou.undos.group("HouTools Capture Viewport"):
        editor.setBackgroundImages(kept + (image,))


def run(kwargs=None):
    """OPmenu 菜单入口；kwargs 为菜单脚本上下文（含 node，可能含 editor）。"""
    import hou

    node = (kwargs or {}).get("node")
    if node is None:
        return
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
        # 背景图引用存 $HIP 变量形态（与 CAPTURE_DIR 一致的斜杠风格）
        hip_path = "{}/{}".format(CAPTURE_DIR, filename)

        _capture_to_file(viewer, file_abs)
        if not os.path.isfile(file_abs):
            raise RuntimeError(
                "flipbook 未生成截图文件: {}".format(file_abs))

        pix_w, pix_h = hou.imageResolution(file_abs)
        if not pix_w or not pix_h:
            raise RuntimeError("截图分辨率读取失败: {}".format(file_abs))
        _embed(editor, node, hip_path, pix_w, pix_h)
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
    hou.ui.setStatusMessage(
        "HouTools: 视窗截图已嵌入 {}（{}x{}，{}）".format(
            node.path(), pix_w, pix_h, hip_path))
