"""截取缩略图：全部屏幕抓屏 + 比例锁定遮罩框选（截屏设缩略图），支持多显示器。

右键卡片「截取缩略图」→ 面板自隐（避免把自己截进素材）→ 延时抓取
全部屏幕 → 每屏各放一个全屏置顶遮罩、铺各自的冻结抓屏。所有遮罩共享
同一选框（虚拟桌面坐标，SnipOverlay 会话持有）：可在任意屏幕上按下
拖拽出严格锁定卡片缩略图区比例的选框（宽:高 = 1:0.66，与
crop.TARGET_RATIO、卡片 thumb_h = base*0.66 同源），选框内保持抓屏
原样（所见即所得）、框外压暗，选框可以跨屏。框选交互对齐
ThumbCropDialog 手感：框内拖动移动、四角手柄按比例缩放、框外按下
重新框选；松手不确认——确认认 Enter 或选框下的 ✓ 按钮、取消认
Esc 或 ✗（微信式按钮条：左叉右勾，贴选框下缘右对齐，选框贴底时
翻到上缘；经 confirmed(QPixmap) 回交原图分辨率裁剪结果、
cancelled() 通知）。像素尺寸标签贴选框上缘左对齐（微信同款），
给下缘的按钮条让位。

坐标模型：Qt 多屏的"虚拟桌面坐标"（QScreen.geometry 的定位空间，
鼠标事件的 globalPosition 同属此空间）作为共享坐标；各遮罩绘制时把
选框平移回本屏局部坐标（非主屏原点非零 / 混合 DPR 均成立）。拖拽中
光标跨屏时事件仍由按下那块遮罩接收（Qt 隐式鼠标抓取），不能用本地
坐标推算，globalPosition 由平台按真实光标位置给出、跨屏正确。确认
时按屏换算回物理像素：选框落在单屏内 = 该屏抓屏 ×该屏 DPR（单屏
场景与旧版逐像素一致）；跨屏 = 各屏截块按参与屏最大 DPR 重采样拼合
（共享边两端同式舍入，拼接无缝）。无 hou 依赖（smoke test 直测交互）。

生命周期归调用方（browser）：遮罩自身只发信号不自我关闭，调用方在
confirmed/cancelled 收尾时 close()（逐屏关闭，WA_DeleteOnClose 自行
释放）。
"""

from PySide6 import QtCore, QtGui, QtWidgets

from houtools.core.log import get_logger
from houtools.recipelib.crop import HANDLE, MIN_SEL, TARGET_RATIO

log = get_logger("recipelib.capture")

HIDE_DELAY_MS = 200   # 面板隐藏后等 DWM 重绘完再抓屏（急了会把面板截进去）
DIM_ALPHA = 110       # 选框外压暗度（与 crop.py 同观感）
CHIP_BG = (20, 20, 24, 210)   # 提示条/尺寸标签/按钮条底色
CHIP_FG = "#e2e2e8"
# 微信式 ✓/✗ 按钮条：左叉右勾，贴选框下缘右对齐，选框贴底翻到上缘
BAR_BTN = 32      # 单个按钮格（正方形）
BAR_PAD = 5       # 按钮条内边距
BAR_GAP = 4       # 两格间距
BAR_MARGIN = 8    # 按钮条与选框边缘的间距（与尺寸标签同距）
BAR_CHECK = "#2fbf71"
BAR_CROSS = "#e5484d"


def _scaled_span(lo, hi, dpr):
    """逻辑区间 [lo, hi) ×DPR 的设备区间（两端各自舍入，共享边不裂缝）。"""
    return int(round(lo * dpr)), int(round(hi * dpr))


class _ScreenOverlay(QtWidgets.QWidget):
    """单屏遮罩：铺本屏冻结抓屏，绘制共享选框的本地部分并转发输入。"""

    def __init__(self, session, screen, grab):
        super().__init__(None)
        self._session = session
        self._screen = screen
        self._grab = grab
        self.setWindowFlags(QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint
                            | QtCore.Qt.WindowStaysOnTopHint)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setCursor(QtCore.Qt.CrossCursor)
        self.setGeometry(screen.geometry())

    # ---- 输入转发（交互逻辑全在会话里，见 SnipOverlay）----

    def keyPressEvent(self, e):
        if not self._session.key(self, e):
            super().keyPressEvent(e)

    def mousePressEvent(self, e):
        self._session.press(self, e)

    def mouseMoveEvent(self, e):
        self._session.move(self, e)

    def mouseReleaseEvent(self, e):
        self._session.release(self, e)

    # ---- 绘制 ----

    def _local_sel(self):
        """共享选框平移回本屏局部坐标并夹窗口内；无选框/无交集时 None。"""
        s = self._session._sel
        if s is None:
            return None
        local = s.translated(-self.geometry().topLeft())
        local = local.intersected(self.rect())
        return None if local.isEmpty() else local

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        # 冻结抓屏铺满：抓屏是物理像素、窗口是逻辑区域，按 DPR 一比一映射
        p.drawPixmap(self.rect(), self._grab)
        r = self.rect()
        s = self._local_sel()
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
            # 曾因此边框/三分线不可见）；选框跨屏时超界部分被本窗口裁剪，
            # 属于邻屏的边由那块屏的遮罩画出
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
            # 四角手柄（同 crop.py：白色实心方块，热区见会话 _handle_at）
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor("#ffffff"))
            for cx, cy in ((s.left(), s.top()), (s.right(), s.top()),
                           (s.left(), s.bottom()), (s.right(), s.bottom())):
                p.drawRect(cx - HANDLE, cy - HANDLE, HANDLE * 2, HANDLE * 2)
            if self._session._owns_chip(self):
                self._draw_size_chip(p, s)
            bar = self._session._bar_rect()
            if bar is not None:
                self._draw_bar(p, bar)
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
        """选框像素尺寸标签（物理像素，即存储分辨率），只画在属主屏上。

        贴框上缘左对齐（微信同款），给下缘的 ✓/✗ 按钮条让位；顶部
        出界（选框顶到屏顶）时贴进选框内左上角。
        """
        text = self._session._size_text()
        fm = p.fontMetrics()
        bw = fm.horizontalAdvance(text) + 12
        bh = fm.height() + 12
        by = s.top() - bh - 8
        if by < 0:
            by = s.top() + 8
        by = max(0, min(by, self.height() - bh))
        x = max(0, min(s.left(), self.width() - bw))
        self._draw_chip(p, text, x, by)

    def _draw_bar(self, p, bar):
        """微信式确认/取消按钮条：左叉右勾，悬停格淡高亮。

        按钮条几何是会话共享状态（虚拟桌面坐标），每屏遮罩各自把
        本地可见部分画出（跨屏时 painter 自动裁剪）；悬停格存会话，
        多屏高亮一致。
        """
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        local = bar.translated(-self.geometry().topLeft())
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(*CHIP_BG))
        p.drawRoundedRect(local, 6, 6)
        cross, check = self._session._bar_cells(bar)
        hover = self._session._hover_cell
        for cell, kind in ((cross, "cross"), (check, "check")):
            c = cell.translated(-self.geometry().topLeft())
            if hover == kind:
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(QtGui.QColor(255, 255, 255, 30))
                p.drawRoundedRect(c, 4, 4)
            self._draw_bar_icon(p, c, kind)
        p.setRenderHint(QtGui.QPainter.Antialiasing, False)

    def _draw_bar_icon(self, p, cell, kind):
        """格子中央画勾/叉：圆头粗笔画，绿勾红叉（微信同观感）。"""
        cx, cy = cell.center().x(), cell.center().y()
        r = BAR_BTN * 0.28   # 笔画半幅，整图标约 0.56 格宽
        pen = QtGui.QPen(QtGui.QColor(BAR_CHECK if kind == "check"
                                      else BAR_CROSS))
        pen.setWidth(3)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(QtCore.Qt.NoBrush)
        if kind == "check":
            path = QtGui.QPainterPath()
            path.moveTo(cx - r, cy + r * 0.15)
            path.lineTo(cx - r * 0.15, cy + r)
            path.lineTo(cx + r, cy - r)
            p.drawPath(path)
        else:
            p.drawLine(QtCore.QPointF(cx - r, cy - r),
                       QtCore.QPointF(cx + r, cy + r))
            p.drawLine(QtCore.QPointF(cx - r, cy + r),
                       QtCore.QPointF(cx + r, cy - r))

    def _draw_hint(self, p):
        """顶部居中操作提示条。"""
        # 不写 ✓/✗ 字形——Houdini UI 字体（Source Sans Pro）不一定带，
        # 提示是纯文本；按钮条本体是 QPainter 画的图标，无字形依赖
        text = ("拖拽框选（可跨屏） · 框内拖动移动 · 角上缩放 · "
                "Enter 或点按钮确认 · Esc 取消")
        bw = p.fontMetrics().horizontalAdvance(text) + 12
        x = max(0, (self.width() - bw) // 2)
        self._draw_chip(p, text, x, 12)


class SnipOverlay(QtCore.QObject):
    """多屏截取会话：每屏一块 _ScreenOverlay，共享同一比例锁定选框。

    选框与拖拽状态存虚拟桌面坐标；确认（Enter/✓ 按钮）经 _confirm 发
    confirmed(QPixmap) 且只发一次（键盘焦点只在其中一块遮罩上，_done
    是焦点竞争/重复点击的双确认保险），取消（Esc/✗）经 _cancel 发
    cancelled 且不闩——遮罩不自我关闭，重复取消由调用方幂等收尾；
    close() 逐屏关闭全部遮罩。
    """

    confirmed = QtCore.Signal(QtGui.QPixmap)
    cancelled = QtCore.Signal()

    def __init__(self, grabs, ratio=TARGET_RATIO):
        super().__init__(None)
        self._grabs = list(grabs)   # [(QScreen, 冻结抓屏 QPixmap)]
        self._ratio = ratio
        self._bounds = QtCore.QRect()   # 有遮罩屏幕的并集（虚拟坐标）
        for scr, _pm in self._grabs:
            self._bounds = self._bounds.united(scr.geometry())
        self._sel = None        # 共享选框（虚拟桌面坐标，恒 normalized）
        self._mode = None       # None/"move"/"new"/"resize"（同 crop 画布）
        self._handle = None     # resize 中的角："tl"/"tr"/"bl"/"br"
        self._press = None
        self._sel_start = None  # move/resize 开始时的选框
        self._hover_cell = None  # 悬停的按钮格："check"/"cross"/None
                                 # （存会话，跨屏遮罩绘制一致）
        self._done = False      # 确认已发，忽略后续 Enter/✓（Esc 不闩：
                                # 遮罩不自我关闭，Esc 后会话仍归调用方处置）
        self._overlays = [_ScreenOverlay(self, scr, pm)
                          for scr, pm in self._grabs]

    def begin(self):
        """显示全部遮罩并抢焦点（须在抓屏之后调用，避免把自己截进去）。

        键盘焦点给光标所在屏的遮罩；Enter/Esc 无论焦点落在哪块屏都
        收口到会话，语义一致。
        """
        if not self._overlays:
            return
        under = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
        target = self._overlays[0]
        for ov in self._overlays:
            ov.show()
            ov.raise_()
            if ov._screen is under:
                target = ov
        target.activateWindow()
        target.setFocus(QtCore.Qt.OtherFocusReason)

    def close(self):
        """关闭全部遮罩（WA_DeleteOnClose 自行释放）。"""
        for ov in self._overlays:
            try:
                ov.close()
            except RuntimeError:
                pass

    def repaint_all(self):
        """选框是共享状态，任何变化都要让全部遮罩重绘。"""
        for ov in self._overlays:
            ov.update()

    # ---- 交互（由各屏遮罩转发；坐标一律虚拟桌面坐标）----

    def key(self, ov, e):
        if e.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self._confirm()
            return True
        if e.key() == QtCore.Qt.Key_Escape:
            self._cancel()
            return True
        return False

    def _confirm(self):
        """确认（Enter 与 ✓ 按钮同路径）：裁剪回物理像素并回交。"""
        if self._sel is not None and not self._done:
            self._done = True
            self.confirmed.emit(self._result_pixmap())

    def _cancel(self):
        """取消（Esc 与 ✗ 按钮同路径）：只发信号，收尾归调用方。"""
        if not self._done:
            self.cancelled.emit()

    def press(self, ov, e):
        if e.button() != QtCore.Qt.LeftButton:
            return
        pos = e.globalPosition().toPoint()
        # 按钮条命中优先于框选/移动/重框——按钮上按下不得清掉旧选框
        bar = self._bar_rect()
        if bar is not None and bar.contains(pos):
            cross, check = self._bar_cells(bar)
            if check.contains(pos):
                self._confirm()
            elif cross.contains(pos):
                self._cancel()
            ov.setFocus(QtCore.Qt.MouseFocusReason)
            self.repaint_all()
            return
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
        ov.setFocus(QtCore.Qt.MouseFocusReason)   # 点击兜底拿焦点（Enter 用）
        self.repaint_all()

    def move(self, ov, e):
        pos = e.globalPosition().toPoint()
        if self._mode is None:
            self._update_cursor(ov, pos)
            return
        if self._mode == "move":
            s = QtCore.QRect(self._sel_start).translated(pos - self._press)
            b = self._bounds
            s.moveLeft(max(b.left(),
                           min(s.left(), b.left() + b.width() - s.width())))
            s.moveTop(max(b.top(),
                          min(s.top(), b.top() + b.height() - s.height())))
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
        self.repaint_all()

    def release(self, ov, e):
        if e.button() != QtCore.Qt.LeftButton:
            return
        self._mode = None       # 松手只结束拖拽，确认只认 Enter/✓
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

    def _update_cursor(self, ov, pos):
        bar = self._bar_rect()
        if bar is not None and bar.contains(pos):
            cross, check = self._bar_cells(bar)
            hover = ("check" if check.contains(pos)
                     else "cross" if cross.contains(pos) else None)
            if hover != self._hover_cell:
                self._hover_cell = hover
                self.repaint_all()
            ov.setCursor(QtCore.Qt.PointingHandCursor)
            return
        if self._hover_cell is not None:   # 离开按钮条清悬停高亮
            self._hover_cell = None
            self.repaint_all()
        h = self._handle_at(pos)
        if h in ("tl", "br"):
            ov.setCursor(QtCore.Qt.SizeFDiagCursor)
        elif h in ("tr", "bl"):
            ov.setCursor(QtCore.Qt.SizeBDiagCursor)
        elif self._sel is not None and self._sel.contains(pos):
            ov.setCursor(QtCore.Qt.SizeAllCursor)
        else:
            ov.setCursor(QtCore.Qt.ArrowCursor)

    # ---- 微信式 ✓/✗ 按钮条 ----

    def _bar_rect(self):
        """按钮条几何（虚拟桌面坐标）；无选框时 None。

        常规贴选框下缘、右缘对齐选框右缘（微信同款）；选框底缘贴到
        屏幕并集底部放不下时翻到上缘；翻上去若与尺寸标签（同在上缘）
        左右相撞（窄选框），整条再上移一行与其上下堆叠。最后夹进
        屏幕并集，保证按钮永远落在有遮罩的屏幕上、点得到。
        """
        if self._sel is None:
            return None
        w = BAR_PAD * 2 + BAR_BTN * 2 + BAR_GAP
        h = BAR_BTN + BAR_PAD * 2
        s = self._sel
        b = self._bounds
        x = s.right() - w + 1
        y = s.bottom() + BAR_MARGIN
        if y + h > b.bottom() + 1:   # 底部放不下 → 翻到选框上方
            y = s.top() - BAR_MARGIN - h
            cw, ch = self._chip_size()
            if s.width() < cw + w + 16:
                y -= ch + 6          # 窄选框：与尺寸标签同排会撞，堆叠错开
        x = max(b.left(), min(x, b.left() + b.width() - w))
        y = max(b.top(), min(y, b.top() + b.height() - h))
        return QtCore.QRect(x, y, w, h)

    def _bar_cells(self, bar):
        """按钮条里两格：左叉右勾（微信同序），返回 (叉格, 勾格)。"""
        top = bar.top() + BAR_PAD
        cross = QtCore.QRect(bar.left() + BAR_PAD, top, BAR_BTN, BAR_BTN)
        check = QtCore.QRect(cross.right() + 1 + BAR_GAP, top,
                             BAR_BTN, BAR_BTN)
        return cross, check

    def _chip_size(self):
        """尺寸标签 (宽, 高)，与遮罩绘制同一默认字体度量。"""
        fm = QtGui.QFontMetrics(QtGui.QGuiApplication.font())
        return fm.horizontalAdvance(self._size_text()) + 12, fm.height() + 12

    def _fit_from_anchor(self, anchor, target_w, sign_x, sign_y):
        """从 anchor 向 (sign_x, sign_y) 铺锁定比例框，夹进全部屏幕并集。

        与 crop._CropCanvas._fit_ratio_rect 同一算法；遮罩画布是"有遮罩
        屏幕的并集"（虚拟坐标），无 _disp 显示层，无法直接复用，独立
        实现于此。
        """
        b = self._bounds
        w = max(MIN_SEL, target_w)
        h = int(round(w / self._ratio))
        if w > b.width():
            w = b.width()
            h = int(round(w / self._ratio))
        if h > b.height():
            h = b.height()
            w = int(round(h * self._ratio))
        x = anchor.x() if sign_x >= 0 else anchor.x() - w
        y = anchor.y() if sign_y >= 0 else anchor.y() - h
        x = max(b.left(), min(x, b.left() + b.width() - w))
        y = max(b.top(), min(y, b.top() + b.height() - h))
        return QtCore.QRect(x, y, w, h)

    # ---- 结果 ----

    def _sel_parts(self):
        """选框与各屏的交集 [(screen, grab, 虚拟坐标截块)]，按 grabs 序。"""
        out = []
        for scr, pm in self._grabs:
            part = self._sel.intersected(scr.geometry())
            if not part.isEmpty():
                out.append((scr, pm, part))
        return out

    def _sel_dpr(self):
        """选框覆盖屏的取像 DPR：单屏 = 该屏 DPR；跨屏 = 参与屏最大值。"""
        parts = self._sel_parts()
        if not parts:
            return 1.0
        if len(parts) == 1:
            return parts[0][1].devicePixelRatio() or 1.0
        return max((pm.devicePixelRatio() or 1.0) for _s, pm, _p in parts)

    def _size_text(self):
        dpr = self._sel_dpr()
        return "{} × {}".format(int(round(self._sel.width() * dpr)),
                                int(round(self._sel.height() * dpr)))

    def _owns_chip(self, ov):
        """尺寸标签属主：含选框左上角的屏（标签贴上缘左对齐）；跨屏/
        舍入落空时取首个相交屏。

        跨屏选框不在每块遮罩上都贴尺寸标签，只在属主屏贴一张。
        """
        if self._sel is None:
            return False
        pt = self._sel.topLeft()
        first = None
        for o in self._overlays:
            g = o.geometry()
            if g.contains(pt):
                return o is ov
            if first is None and g.intersects(self._sel):
                first = o
        return first is ov

    def _result_pixmap(self):
        """选框 ×DPR 换算回物理像素，从冻结抓屏裁出确认结果。

        单屏 = 该屏抓屏直接裁（与旧单屏版逐像素一致）；跨屏 = 各屏截块
        按参与屏最大 DPR 重采样拼合，共享边两端同式舍入不裂缝。
        """
        parts = self._sel_parts()
        if not parts:
            return QtGui.QPixmap()   # 混合 DPI 舍入缝隙的极端情形，防炸
        if len(parts) == 1:
            scr, pm, part = parts[0]
            dpr = pm.devicePixelRatio() or 1.0
            local = part.translated(-scr.geometry().topLeft())
            dev = QtCore.QRect(
                int(round(local.x() * dpr)), int(round(local.y() * dpr)),
                int(round(local.width() * dpr)),
                int(round(local.height() * dpr)))
            dev = dev.intersected(pm.rect())
            out = pm.copy(dev)
            out.setDevicePixelRatio(1.0)   # copy 继承源 DPR，重置为纯像素尺寸
            return out
        ref = max((pm.devicePixelRatio() or 1.0) for _s, pm, _p in parts)
        out = QtGui.QPixmap(int(round(self._sel.width() * ref)),
                            int(round(self._sel.height() * ref)))
        out.fill(QtCore.Qt.black)
        p = QtGui.QPainter(out)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        sel = self._sel
        for scr, pm, part in parts:
            dpr = pm.devicePixelRatio() or 1.0
            g = scr.geometry()
            sx0, sx1 = _scaled_span(part.left() - g.left(),
                                    part.right() + 1 - g.left(), dpr)
            sy0, sy1 = _scaled_span(part.top() - g.top(),
                                    part.bottom() + 1 - g.top(), dpr)
            dx0, dx1 = _scaled_span(part.left() - sel.left(),
                                    part.right() + 1 - sel.left(), ref)
            dy0, dy1 = _scaled_span(part.top() - sel.top(),
                                    part.bottom() + 1 - sel.top(), ref)
            src = QtCore.QRect(sx0, sy0, sx1 - sx0, sy1 - sy0)
            src = src.intersected(pm.rect())
            if src.isEmpty():
                continue
            # 坑：Houdini 自带 PySide6 6.8.3 的 drawPixmap(QRect, QPixmap,
            # QRectF) 签名标注收 QRectF、值分发实测拒绝（ValueError），
            # 源矩形必须传 QRect（src 本就是整数设备像素，无损）
            p.drawPixmap(QtCore.QRect(dx0, dy0, dx1 - dx0, dy1 - dy0),
                         pm, src)
        p.end()
        out.setDevicePixelRatio(1.0)
        return out
