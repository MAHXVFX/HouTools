"""收藏角标共享组件：SVG 本体 + 柔和投影，叠加到缩略图右上角。

Recipe Library 与 Hdr Library 共用；素材为仓库
``python3.13libs/houtools/icons/favorite_badge.svg``（__file__ 解析
绝对路径，Houdini 启动 CWD 不固定）。

- QPainter 没有内建模糊：投影经 QGraphicsDropShadowEffect 离屏场景
  渲染（高斯模糊从源 alpha 生成），画布四周留 pad 容纳溢出
- composite(base_icon, request_size)：底图按 request_size 取像素图
  （QIcon 不放大，返回实际可用尺寸），画布取该实际尺寸——角标大小
  随底图等比（24%，14-44px 夹档），贴缩略图右上角而非槽位角
"""

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

try:
    from PySide6 import QtSvg
except ImportError:  # QtSvg 缺失时角标降级为不显示（正常环境都有）
    QtSvg = None

from houtools.core.log import get_logger

log = get_logger("ui.badge")

FAVORITE_BADGE_SVG = Path(__file__).resolve().parent.parent / "icons" / \
    "favorite_badge.svg"


class FavoriteBadge:
    """收藏角标渲染器（惰性加载 SVG，投影与合成结果均按尺寸缓存）。"""

    def __init__(self, svg_path=FAVORITE_BADGE_SVG):
        self._svg_path = Path(svg_path)
        self._renderer_cache = False   # False=未初始化；None=不可用
        self._badge_cache = {}   # badge_size -> QPixmap（本体+投影画布）

    def _renderer(self):
        if self._renderer_cache is False:
            renderer = None
            if QtSvg is None:
                log.warning("QtSvg unavailable, favorite badge disabled")
            elif not self._svg_path.is_file():
                log.warning("favorite badge svg missing: %s", self._svg_path)
            else:
                renderer = QtSvg.QSvgRenderer(str(self._svg_path))
                if not renderer.isValid():
                    log.warning("favorite badge svg invalid: %s",
                                self._svg_path)
                    renderer = None
            self._renderer_cache = renderer
        return self._renderer_cache

    def badge_pixmap(self, size):
        """角标本体 + 柔和投影，透明底画布 (size + 2*pad)²，按尺寸缓存。"""
        size = max(1, int(size))
        pm = self._badge_cache.get(size)
        if pm is not None:
            return pm
        blur = max(2, size // 7)
        offset = max(1, size // 14)
        pad = blur + offset + 2
        canvas_w = size + pad * 2
        pm = QtGui.QPixmap(canvas_w, canvas_w)
        pm.fill(QtCore.Qt.transparent)
        renderer = self._renderer()
        if renderer is not None:
            badge_src = QtGui.QPixmap(size, size)
            badge_src.fill(QtCore.Qt.transparent)
            sp = QtGui.QPainter(badge_src)
            renderer.render(sp, QtCore.QRectF(0, 0, size, size))
            sp.end()

            scene = QtWidgets.QGraphicsScene(0, 0, canvas_w, canvas_w)
            item = scene.addPixmap(badge_src)
            item.setPos(pad, pad)
            effect = QtWidgets.QGraphicsDropShadowEffect()
            effect.setBlurRadius(blur)
            effect.setOffset(offset, offset)
            effect.setColor(QtGui.QColor(0, 0, 0, 170))
            item.setGraphicsEffect(effect)
            rp = QtGui.QPainter(pm)
            scene.render(rp, QtCore.QRectF(0, 0, canvas_w, canvas_w),
                         QtCore.QRectF(0, 0, canvas_w, canvas_w))
            rp.end()
        self._badge_cache[size] = pm
        return pm

    def composite(self, base_icon, request_size):
        """底图右上角叠加角标，返回合成后的 QIcon。

        request_size 是底图像素图的请求尺寸（QIcon 不会放大，实际取到
        的是可用尺寸），画布随之取实际尺寸；调用方在底图或尺寸变化时
        自行重新合成。
        """
        base_pm = base_icon.pixmap(max(16, int(request_size)))
        if base_pm.isNull():
            return base_icon
        canvas = QtGui.QPixmap(base_pm.size())
        canvas.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(canvas)
        painter.drawPixmap(0, 0, base_pm)
        badge_size = max(14, min(44, int(base_pm.width() * 0.24)))
        badge = self.badge_pixmap(badge_size)
        margin = max(3, badge_size // 8)
        # badge 画布含投影余量（四周 pad），按 -pad 贴回视觉角落
        pad = (badge.width() - badge_size) // 2
        painter.drawPixmap(base_pm.width() - badge_size - margin - pad,
                           margin - pad, badge)
        painter.end()
        return QtGui.QIcon(canvas)
