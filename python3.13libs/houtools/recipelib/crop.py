"""缩略图裁剪对话框：头像上传式框选，确认返回裁剪后的 QPixmap。

选框锁定卡片缩略图区比例（宽:高 = 1:0.66，与 browser 的缩略图区一致），
所见即所得：确认后存储的图在卡片上铺满显示、无灰边。交互：框内拖动
移动；四角手柄按锁定比例缩放；框外按下重新框选。无 hou 依赖。
"""

from PySide6 import QtCore, QtGui, QtWidgets

from houtools.ui.dialogs import localize_buttons

TARGET_RATIO = 1 / 0.66   # 选框宽/高（与卡片缩略图区 thumb_h = w*0.66 一致）
MIN_SEL = 40              # 选框最小显示边长（px）
HANDLE = 7                # 角手柄绘制/热区半径（px）
MAX_CANVAS = (720, 460)   # 原图展示区上限（contain）


class _CropCanvas(QtWidgets.QWidget):
    """图片展示 + 选框交互（显示坐标系），选区变化发 selectionChanged。"""

    selectionChanged = QtCore.Signal()

    def __init__(self, source_pixmap, ratio, parent=None):
        super().__init__(parent)
        self._src = source_pixmap
        self._ratio = ratio                  # 宽/高
        self._disp = source_pixmap.scaled(
            *MAX_CANVAS, QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation)
        self.setFixedSize(self._disp.size())
        self._sel = self._max_sel()
        self._mode = None                    # None/"move"/"new"/"resize"
        self._handle = None                  # "tl"/"tr"/"bl"/"br"
        self._press = None
        self._sel_start = None

    # ---- 选区 ----

    def _max_sel(self):
        dw, dh = self._disp.width(), self._disp.height()
        if dw / dh > self._ratio:
            h = dh
            w = int(h * self._ratio)
        else:
            w = dw
            h = int(w / self._ratio)
        return QtCore.QRect((dw - w) // 2, (dh - h) // 2, w, h)

    def source_rect(self):
        """选框换算回原图坐标（裁剪用）。"""
        f = self._src.width() / self._disp.width()
        s = self._sel.normalized()
        return QtCore.QRect(int(s.x() * f), int(s.y() * f),
                            int(s.width() * f), int(s.height() * f))

    def source_pixmap(self):
        """当前选区的原图分辨率裁剪结果（确认存储时用）。"""
        return self._src.copy(self.source_rect())

    def display_crop_pixmap(self):
        """当前选区的显示分辨率裁剪（预览热路径）。

        直接从显示用 _disp（与原图同比例）取块，不做原图分辨率的整块
        copy——mouseMove 每步都会走到这里，原图大时逐帧整拷很费；最终
        预览就 150px 档，显示分辨率源精度足够。
        """
        return self._disp.copy(self._sel.normalized())

    def _fit_ratio_rect(self, anchor, target_w, sign_x, sign_y):
        """从锚点向 (sign_x, sign_y) 方向铺 target_w 宽的锁定比例框，夹图内。"""
        w = max(MIN_SEL, target_w)
        h = int(w / self._ratio)
        if w > self._disp.width():
            w = self._disp.width()
            h = int(w / self._ratio)
        if h > self._disp.height():
            h = self._disp.height()
            w = int(h * self._ratio)
        x = anchor.x() if sign_x >= 0 else anchor.x() - w
        y = anchor.y() if sign_y >= 0 else anchor.y() - h
        x = max(0, min(x, self._disp.width() - w))
        y = max(0, min(y, self._disp.height() - h))
        return QtCore.QRect(x, y, w, h)

    # ---- 交互 ----

    def _handle_at(self, pos):
        s = self._sel.normalized()
        for name, cx, cy in (("tl", s.left(), s.top()),
                             ("tr", s.right(), s.top()),
                             ("bl", s.left(), s.bottom()),
                             ("br", s.right(), s.bottom())):
            if abs(pos.x() - cx) <= HANDLE + 3 \
                    and abs(pos.y() - cy) <= HANDLE + 3:
                return name
        return None

    def mousePressEvent(self, e):
        if e.button() != QtCore.Qt.LeftButton:
            return
        pos = e.position().toPoint()
        h = self._handle_at(pos)
        self._press = pos
        self._sel_start = QtCore.QRect(self._sel)
        if h:
            self._mode = "resize"
            self._handle = h
        elif self._sel.normalized().contains(pos):
            self._mode = "move"
        else:
            self._mode = "new"
            self._sel = QtCore.QRect(pos, QtCore.QSize(1, 1))

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self._mode is None:
            h = self._handle_at(pos)
            if h in ("tl", "br"):
                self.setCursor(QtCore.Qt.SizeFDiagCursor)
            elif h in ("tr", "bl"):
                self.setCursor(QtCore.Qt.SizeBDiagCursor)
            elif self._sel.normalized().contains(pos):
                self.setCursor(QtCore.Qt.SizeAllCursor)
            else:
                self.setCursor(QtCore.Qt.ArrowCursor)
            return
        d = pos - self._press
        if self._mode == "move":
            s = QtCore.QRect(self._sel_start).translated(d)
            s.moveLeft(max(0, min(s.left(),
                                  self._disp.width() - s.width())))
            s.moveTop(max(0, min(s.top(),
                                 self._disp.height() - s.height())))
            self._sel = s
        elif self._mode == "new":
            # 以按下点为角，宽随横向拖动、方向随象限
            sign_x = 1 if pos.x() >= self._press.x() else -1
            sign_y = 1 if pos.y() >= self._press.y() else -1
            self._sel = self._fit_ratio_rect(
                self._press, abs(pos.x() - self._press.x()), sign_x, sign_y)
        elif self._mode == "resize":
            # sign = 框相对锚点的方向：tl 角的框在锚（右下）的左上方
            anchor = self._anchor_point()
            sign_x, sign_y = {"tl": (-1, -1), "tr": (1, -1),
                              "bl": (-1, 1), "br": (1, 1)}[self._handle]
            # 宽度取两轴中更大的需求（拖横/拖竖都有反馈），高按比例
            w = max(abs(pos.x() - anchor.x()),
                    int(abs(pos.y() - anchor.y()) * self._ratio))
            self._sel = self._fit_ratio_rect(anchor, w, sign_x, sign_y)
        self.update()
        self.selectionChanged.emit()

    def _anchor_point(self):
        """resize 时固定不动的对角点（resize 开始时选区的对角）。"""
        s = QtCore.QRect(self._sel_start).normalized()
        return {"tl": s.bottomRight(), "tr": s.bottomLeft(),
                "bl": s.topRight(), "br": s.topLeft()}[self._handle]

    def mouseReleaseEvent(self, _e):
        self._mode = None
        self._handle = None

    # ---- 绘制 ----

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.drawPixmap(0, 0, self._disp)
        s = self._sel.normalized()
        r = self.rect()
        # 选框外半透明遮罩（四块）
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(0, 0, 0, 130))
        p.drawRect(0, 0, r.width(), s.top())
        p.drawRect(0, s.bottom() + 1, r.width(),
                   r.height() - s.bottom() - 1)
        p.drawRect(0, s.top(), s.left(), s.height())
        p.drawRect(s.right() + 1, s.top(),
                   r.width() - s.right() - 1, s.height())
        # 边框 + 三分构图线
        p.setBrush(QtCore.Qt.NoBrush)
        pen = p.pen()
        pen.setColor(QtGui.QColor("#ffffff"))
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
        # 四角手柄
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor("#ffffff"))
        for cx, cy in ((s.left(), s.top()), (s.right(), s.top()),
                       (s.left(), s.bottom()), (s.right(), s.bottom())):
            p.drawRect(cx - HANDLE, cy - HANDLE, HANDLE * 2, HANDLE * 2)


class ThumbCropDialog(QtWidgets.QDialog):
    """框选裁剪对话框；exec_ 后经 result_pixmap() 取原图分辨率结果。"""

    def __init__(self, parent, source_path, display_name, ratio=TARGET_RATIO):
        super().__init__(parent)
        self.setWindowTitle("裁剪缩略图 - {}".format(display_name))
        self.setStyleSheet(
            "QDialog { background-color: #18181b; }"
            "QLabel { color: #bbbbbb; }")
        src = QtGui.QPixmap(source_path)
        if src.isNull():
            raise RuntimeError("无法读取图片: {}".format(source_path))
        self._canvas = _CropCanvas(src, ratio)

        hint = QtWidgets.QLabel(
            "拖动选框调整区域；拖四角按比例缩放；框外拖动重新框选。\n"
            "确认后存储所选区域（比例与卡片一致，铺满显示）。")
        hint.setStyleSheet("color: #888888;")

        self._preview = QtWidgets.QLabel()
        self._preview.setFixedSize(150, int(150 / ratio))
        self._preview.setAlignment(QtCore.Qt.AlignCenter)
        self._preview.setStyleSheet(
            "background-color: #161619; border: 1px solid #3d3d3d;")

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        bottom = QtWidgets.QHBoxLayout()
        pv_box = QtWidgets.QVBoxLayout()
        pv_label = QtWidgets.QLabel("卡片效果")
        pv_label.setStyleSheet("color: #888888;")
        pv_box.addWidget(pv_label)
        pv_box.addWidget(self._preview)
        bottom.addLayout(pv_box)
        bottom.addStretch(1)
        bottom.addWidget(buttons)

        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(self._canvas, 0, QtCore.Qt.AlignCenter)
        lay.addWidget(hint)
        lay.addLayout(bottom)
        localize_buttons(self)
        self._canvas.selectionChanged.connect(self._update_preview)
        self._update_preview()

    def _update_preview(self):
        # 预览走显示分辨率裁剪（_disp 与原图同比例，最终就 150px 档）；
        # 原图分辨率的精确裁剪只在确认时经 result_pixmap() 取
        pm = self._canvas.display_crop_pixmap()
        if pm.isNull():
            return
        self._preview.setPixmap(pm.scaled(
            self._preview.size(), QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation))

    def result_pixmap(self):
        return self._canvas.source_pixmap()
