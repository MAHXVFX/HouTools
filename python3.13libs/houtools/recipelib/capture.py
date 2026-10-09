"""截取缩略图：全屏抓屏 + 比例锁定遮罩框选（截屏设缩略图）。

右键卡片「截取缩略图」→ 面板自隐（避免把自己截进素材）→ 延时抓屏 →
全屏置顶遮罩铺冻结抓屏：按下拖拽出严格锁定卡片缩略图区比例的选框
（宽:高 = 1:0.66，与 crop.TARGET_RATIO、卡片 thumb_h = base*0.66 同源），
选框内保持抓屏原样（所见即所得）、框外压暗。框选交互对齐
ThumbCropDialog 手感：框内拖动移动、四角手柄按比例缩放、框外按下
重新框选；松手不确认——确认只认 Enter（经 confirmed(QPixmap) 回交
原图分辨率裁剪结果），取消只认 Esc（经 cancelled() 通知）。选框是
逻辑坐标，确认时按抓屏 DPR 换算回物理像素裁剪。无 hou 依赖
（smoke test 直测交互）。

生命周期归调用方（browser）：遮罩自身只发信号不自我关闭，调用方在
confirmed/cancelled 收尾时 close()（WA_DeleteOnClose 自行释放）。
"""

from PySide6 import QtCore, QtGui, QtWidgets

from houtools.core.log import get_logger
from houtools.recipelib.crop import HANDLE, MIN_SEL, TARGET_RATIO

log = get_logger("recipelib.capture")

HIDE_DELAY_MS = 200   # 面板隐藏后等 DWM 重绘完再抓屏（急了会把面板截进去）
DIM_ALPHA = 110       # 选框外压暗度（与 crop.py 同观感）
CHIP_BG = (20, 20, 24, 210)   # 提示条/尺寸标签底色
CHIP_FG = "#e2e2e8"


class SnipOverlay(QtWidgets.QWidget):
    """全屏遮罩：冻结抓屏 + 比例锁定框选；Enter 确认 / Esc 取消。"""

    confirmed = QtCore.Signal(QtGui.QPixmap)
    cancelled = QtCore.Signal()

    def __init__(self, screen, grab, ratio=TARGET_RATIO):
        super().__init__(None)
        self._grab = grab
        self._ratio = ratio
        self._dpr = grab.devicePixelRatio() or 1.0
        self._sel = None        # 当前选框（逻辑坐标，恒 normalized）
        self._mode = None       # None/"move"/"new"/"resize"（同 crop 画布）
        self._handle = None     # resize 中的角："tl"/"tr"/"bl"/"br"
        self._press = None
        self._sel_start = None  # move/resize 开始时的选框
        self.setWindowFlags(QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint
                            | QtCore.Qt.WindowStaysOnTopHint)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setCursor(QtCore.Qt.CrossCursor)
        self.setGeometry(screen.geometry())

    def begin(self):
        """显示遮罩并抢焦点（须在抓屏之后调用，避免把自己截进去）。"""
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(QtCore.Qt.OtherFocusReason)

    # ---- 交互 ----

    def keyPressEvent(self, e):
        if e.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            if self._sel is not None:
                self.confirmed.emit(self._result_pixmap())
        elif e.key() == QtCore.Qt.Key_Escape:
            self.cancelled.emit()
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e):
        if e.button() != QtCore.Qt.LeftButton:
            return
        pos = e.position().toPoint()
        h = self._handle_at(pos)
        self._press = pos
        self._sel_start = (QtCore.QRect(self._sel)
                           if self._sel is not None else None)
        if h:
            self._mode = "resize"
            self._handle = h
        elif self._sel is not None and self._sel.contains(pos):
            self._mode = "move"
        else:
            # 框外重按 = 重框：清旧选框，随 move 重新生成
            self._mode = "new"
            self._sel = None
        self.setFocus(QtCore.Qt.MouseFocusReason)   # 点击兜底拿焦点（Enter 用）
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self._mode is None:
            self._update_cursor(pos)
            return
        if self._mode == "move":
            s = QtCore.QRect(self._sel_start).translated(pos - self._press)
            r = self.rect()
            s.moveLeft(max(0, min(s.left(), r.width() - s.width())))
            s.moveTop(max(0, min(s.top(), r.height() - s.height())))
            self._sel = s
        elif self._mode == "new":
            sign_x = 1 if pos.x() >= self._press.x() else -1
            sign_y = 1 if pos.y() >= self._press.y() else -1
            # 宽取两轴中更大的需求（拖横/拖竖都有反馈），高按比例
            w = max(abs(pos.x() - self._press.x()),
                    int(abs(pos.y() - self._press.y()) * self._ratio))
            self._sel = self._fit_from_anchor(self._press, w, sign_x, sign_y)
        elif self._mode == "resize":
            anchor = self._anchor_point()
            sign_x, sign_y = {"tl": (-1, -1), "tr": (1, -1),
                              "bl": (-1, 1), "br": (1, 1)}[self._handle]
            w = max(abs(pos.x() - anchor.x()),
                    int(abs(pos.y() - anchor.y()) * self._ratio))
            self._sel = self._fit_from_anchor(anchor, w, sign_x, sign_y)
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != QtCore.Qt.LeftButton:
            return
        self._mode = None       # 松手只结束拖拽，确认只认 Enter
        self._handle = None

    def _handle_at(self, pos):
        if self._sel is None:
            return None
        s = self._sel
        for name, cx, cy in (("tl", s.left(), s.top()),
                             ("tr", s.right(), s.top()),
                             ("bl", s.left(), s.bottom()),
                             ("br", s.right(), s.bottom())):
            if abs(pos.x() - cx) <= HANDLE + 3 \
                    and abs(pos.y() - cy) <= HANDLE + 3:
                return name
        return None

    def _anchor_point(self):
        """resize 时固定不动的对角点（resize 开始时选区的对角）。"""
        s = QtCore.QRect(self._sel_start).normalized()
        return {"tl": s.bottomRight(), "tr": s.bottomLeft(),
                "bl": s.topRight(), "br": s.topLeft()}[self._handle]

    def _update_cursor(self, pos):
        h = self._handle_at(pos)
        if h in ("tl", "br"):
            self.setCursor(QtCore.Qt.SizeFDiagCursor)
        elif h in ("tr", "bl"):
            self.setCursor(QtCore.Qt.SizeBDiagCursor)
        elif self._sel is not None and self._sel.contains(pos):
            self.setCursor(QtCore.Qt.SizeAllCursor)
        else:
            self.setCursor(QtCore.Qt.ArrowCursor)

    def _fit_from_anchor(self, anchor, target_w, sign_x, sign_y):
        """从 anchor 向 (sign_x, sign_y) 铺锁定比例框，夹屏内。

        与 crop._CropCanvas._fit_ratio_rect 同一算法；遮罩画布即整屏、
        无 _disp 显示层，无法直接复用，独立实现于此。
        """
        r = self.rect()
        w = max(MIN_SEL, target_w)
        h = int(round(w / self._ratio))
        if w > r.width():
            w = r.width()
            h = int(round(w / self._ratio))
        if h > r.height():
            h = r.height()
            w = int(round(h * self._ratio))
        x = anchor.x() if sign_x >= 0 else anchor.x() - w
        y = anchor.y() if sign_y >= 0 else anchor.y() - h
        x = max(0, min(x, r.width() - w))
        y = max(0, min(y, r.height() - h))
        return QtCore.QRect(x, y, w, h)

    def _result_pixmap(self):
        """选框 ×DPR 换算回物理像素，从冻结抓屏裁出确认结果。"""
        s = self._sel
        dpr = self._dpr
        dev = QtCore.QRect(
            int(round(s.x() * dpr)), int(round(s.y() * dpr)),
            int(round(s.width() * dpr)), int(round(s.height() * dpr)))
        dev = dev.intersected(self._grab.rect())
        pm = self._grab.copy(dev)
        pm.setDevicePixelRatio(1.0)   # copy 继承源 DPR，重置为纯像素尺寸
        return pm

    # ---- 绘制 ----

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        # 冻结抓屏铺满：抓屏是物理像素、窗口是逻辑区域，按 DPR 一比一映射
        p.drawPixmap(self.rect(), self._grab)
        r = self.rect()
        s = self._sel
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(0, 0, 0, DIM_ALPHA))
        if s is None:
            p.drawRect(r)
        else:
            # 选框外四块压暗（同 crop.py 画法），框内保持抓屏原样
            p.drawRect(0, 0, r.width(), s.top())
            p.drawRect(0, s.bottom() + 1, r.width(),
                       r.height() - s.bottom() - 1)
            p.drawRect(0, s.top(), s.left(), s.height())
            p.drawRect(s.right() + 1, s.top(),
                       r.width() - s.right() - 1, s.height())
            # 边框 + 三分构图线：显式构造 QPen——此时 painter 的 pen 是
            # NoPen 样式，pen() 取回改色样式不变、什么也画不出（crop.py
            # 曾因此边框/三分线不可见）
            p.setBrush(QtCore.Qt.NoBrush)
            pen = QtGui.QPen(QtGui.QColor("#ffffff"))
            pen.setWidth(1)
            p.setPen(pen)
            p.drawRect(s)
            pen.setColor(QtGui.QColor(255, 255, 255, 55))
            p.setPen(pen)
            for i in (1, 2):
                x = s.left() + s.width() * i // 3
                y = s.top() + s.height() * i // 3
                p.drawLine(x, s.top(), x, s.bottom())
                p.drawLine(s.left(), y, s.right(), y)
            # 四角手柄（同 crop.py：白色实心方块，热区见 _handle_at）
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor("#ffffff"))
            for cx, cy in ((s.left(), s.top()), (s.right(), s.top()),
                           (s.left(), s.bottom()), (s.right(), s.bottom())):
                p.drawRect(cx - HANDLE, cy - HANDLE, HANDLE * 2, HANDLE * 2)
            self._draw_size_chip(p, s)
        self._draw_hint(p)

    def _draw_chip(self, p, text, x, y):
        """深底白字信息条，左上角 (x, y)；夹界由调用方负责。"""
        fm = p.fontMetrics()
        pad = 6
        bw = fm.horizontalAdvance(text) + pad * 2
        bh = fm.height() + pad * 2
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(*CHIP_BG))
        p.drawRoundedRect(x, y, bw, bh, 4, 4)
        p.setPen(QtGui.QPen(QtGui.QColor(CHIP_FG)))
        p.drawText(x + pad, y + pad + fm.ascent(), text)

    def _draw_size_chip(self, p, s):
        """选框像素尺寸标签（物理像素，即存储分辨率）。

        贴框下缘（+8 让开四角手柄的伸出半边），底部放不下挪到上缘。
        """
        text = "{} × {}".format(
            int(round(s.width() * self._dpr)),
            int(round(s.height() * self._dpr)))
        fm = p.fontMetrics()
        bw = fm.horizontalAdvance(text) + 12
        bh = fm.height() + 12
        by = s.bottom() + 8
        if by + bh > self.height():
            by = s.top() - bh - 8
        by = max(0, min(by, self.height() - bh))
        x = max(0, min(s.right() - bw, self.width() - bw))
        self._draw_chip(p, text, x, by)

    def _draw_hint(self, p):
        """顶部居中操作提示条。"""
        text = "拖拽框选 · 框内拖动移动 · 角上缩放 · Enter 确认 · Esc 取消"
        bw = p.fontMetrics().horizontalAdvance(text) + 12
        x = max(0, (self.width() - bw) // 2)
        self._draw_chip(p, text, x, 12)
