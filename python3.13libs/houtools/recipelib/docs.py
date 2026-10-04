"""Recipe Library 的 Markdown 文档：编辑器、实时预览、媒体播放。

渲染走 QTextBrowser.setMarkdown（QtWebEngine 在 Houdini 里无法安全
初始化——Qt6 要求 QtWebEngineQuick.initialize() 先于 QApplication，而
Houdini 已创建主 QApplication，本仓库已实测确认）。能力边界（目检
验证）：标题/粗斜体/行内代码/链接/表格/列表/图片都正常；围栏代码块
无语法高亮。

媒体的处理（Houdini 22.0.429 GUI 会话实测）：
- 文档流里的 GIF：markdown ![]() 先渲染出静态首帧，随后后处理把该
  字符替换成 QLabel+QMovie 原地动起来（QTextCursor.insertWidget）。
- 文档流里的视频（![x](assets/a.mp4)）：替换成"▶ 文件名"按钮，点击
  弹出 MediaDialog（QMediaPlayer+QVideoWidget）播放。
  ⚠ 必须先 player.setVideoOutput(widget) 再 play，否则 InvalidMedia。
"""

import os
import re
from urllib.parse import unquote

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget

from houtools.core.log import get_logger
from houtools.recipelib import metadata
from houtools.ui.dialogs import localize_buttons, warn

log = get_logger("recipelib.docs")

PREVIEW_DEBOUNCE_MS = 300


class _DocPreview(QtWidgets.QTextBrowser):
    """文档预览：相对路径图片按文档目录解析（markdown 里写 assets/x.png）。"""

    def __init__(self, doc_dir, parent=None):
        super().__init__(parent)
        self._doc_dir = doc_dir
        self.setOpenLinks(False)
        self.anchorClicked.connect(self._on_anchor)

    def set_doc_dir(self, doc_dir):
        self._doc_dir = doc_dir

    def _resolve(self, url):
        path = unquote(url.toString())
        if path.startswith("file:///"):
            path = QtCore.QUrl(path).toLocalFile()
        if not os.path.isabs(path):
            path = os.path.join(self._doc_dir, path)
        return os.path.normpath(path)

    def loadResource(self, rtype, url):
        if rtype == QtGui.QTextDocument.ImageResource:
            path = self._resolve(url)
            if os.path.exists(path):
                img = QtGui.QImage(path)
                if not img.isNull():
                    return img
        return super().loadResource(rtype, url)

    def _on_anchor(self, url):
        path = self._resolve(url)
        ext = os.path.splitext(path)[1].lower()
        if os.path.exists(path) and (ext in metadata.VIDEO_EXTS
                                     or ext == ".gif"):
            MediaDialog.open_media(self, path)
        else:
            QtGui.QDesktopServices.openUrl(url)


class _MovieLabel(QtWidgets.QLabel):
    """GIF 播放标签（MediaDialog 用，QMovie 循环播放）。"""

    def __init__(self, path, max_w, parent=None):
        super().__init__(parent)
        movie = QtGui.QMovie(self)
        movie.setFileName(path)
        movie.setCacheMode(QtGui.QMovie.CacheAll)
        movie.jumpToFrame(0)
        pm = movie.currentPixmap()
        if not pm.isNull() and pm.width() > max_w:
            h = max(1, int(round(pm.height() * max_w / pm.width())))
            movie.setScaledSize(QtCore.QSize(max_w, h))
        self.setMovie(movie)
        movie.start()


class MarkdownMediaView(_DocPreview):
    """可渲染 markdown + 内联媒体的预览视图（文档编辑器右栏与主面板共用）。

    - GIF：QMovie 逐帧同步进文档资源（loadResource 返回当前帧 +
      frameChanged 时 addResource + viewport 刷新），真正内联动画；
      超宽的帧按视口宽度等比缩小
    - 视频：set_markdown_with_media 前把 ![](x.mp4) 预处理成普通链接
      （QTextCursor 无 insertWidget，Qt 富文本不支持内嵌控件），点击
      链接由 _DocPreview._on_anchor 弹播放器
    """

    _VIDEO_IMAGE_RE = re.compile(
        r"!\[([^\]]*)\]\(([^)]+\.(?:mp4|mov|avi|webm|mkv))\)", re.IGNORECASE)

    def __init__(self, doc_dir, parent=None):
        super().__init__(doc_dir, parent)
        self._movies = {}   # 文档里的 url 字符串 -> QMovie

    def set_markdown_with_media(self, text):
        self.stop_media()
        text = self._VIDEO_IMAGE_RE.sub(r"[▶ \1](\2)", text)
        self.setMarkdown(text)
        # QMovie 在布局首次查询图片资源时经 loadResource 惰性创建并启动

    def stop_media(self):
        for movie in self._movies.values():
            movie.stop()
        self._movies.clear()

    def _on_movie_frame(self, url_str):
        movie = self._movies.get(url_str)
        if movie is not None:
            self.document().addResource(
                QtGui.QTextDocument.ImageResource, QtCore.QUrl(url_str),
                self._movie_frame_pixmap(movie))
            self.viewport().update()

    def _movie_frame_pixmap(self, movie):
        pm = movie.currentPixmap()
        max_w = max(120, self.viewport().width() - 12)
        if not pm.isNull() and pm.width() > max_w:
            pm = pm.scaledToWidth(max_w, QtCore.Qt.SmoothTransformation)
        return pm

    def loadResource(self, rtype, url):
        if rtype == QtGui.QTextDocument.ImageResource:
            url_str = url.toString()
            if url_str.lower().split("?", 1)[0].endswith(".gif"):
                movie = self._movies.get(url_str)
                if movie is None:
                    path = self._resolve(url)
                    if os.path.exists(path):
                        movie = QtGui.QMovie(self)
                        movie.setFileName(path)
                        movie.setCacheMode(QtGui.QMovie.CacheAll)
                        movie.frameChanged.connect(
                            lambda _i, u=url_str: self._on_movie_frame(u))
                        self._movies[url_str] = movie
                        movie.start()
                if movie is not None:
                    return self._movie_frame_pixmap(movie)
        return super().loadResource(rtype, url)


class MediaDialog(QtWidgets.QDialog):
    """媒体播放对话框：GIF 用 QMovie 循环，视频用 QMediaPlayer。

    非模态（看文档时可反复对照网络），同一时刻一个实例。
    """

    _current = None

    @classmethod
    def open_media(cls, parent, path):
        if cls._current is not None:
            try:
                cls._current.close()
                cls._current.deleteLater()
            except RuntimeError:
                pass
        dlg = cls(parent, path)
        cls._current = dlg
        dlg.show()
        dlg.raise_()
        return dlg

    def __init__(self, parent, path):
        super().__init__(parent)
        self.setWindowTitle("预览 - {}".format(os.path.basename(path)))
        self.setModal(False)
        self.resize(680, 480)
        self._path = path
        self._player = None
        self._audio = None
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        ext = os.path.splitext(path)[1].lower()
        if ext == ".gif":
            label = _MovieLabel(path, 640, self)
            lay.addWidget(label, 1)
            return

        # 视频：⚠ setVideoOutput 必须在 play 之前，否则 InvalidMedia
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._audio.setVolume(0.8)
        self._player.setAudioOutput(self._audio)
        video = QVideoWidget(self)
        self._player.setVideoOutput(video)
        self._player.setSource(QtCore.QUrl.fromLocalFile(path))
        lay.addWidget(video, 1)

        bar = QtWidgets.QHBoxLayout()
        self._play_btn = QtWidgets.QPushButton("暂停")
        self._slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._slider.setRange(0, 1000)
        self._time = QtWidgets.QLabel("0:00")
        bar.addWidget(self._play_btn)
        bar.addWidget(self._slider, 1)
        bar.addWidget(self._time)
        lay.addLayout(bar)

        self._play_btn.clicked.connect(self._toggle_play)
        self._slider.sliderMoved.connect(self._player.setPosition)
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(
            lambda d: self._slider.setValue(0))
        self._player.play()

    def _toggle_play(self):
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            self._play_btn.setText("播放")
        else:
            self._player.play()
            self._play_btn.setText("暂停")

    def _on_position(self, pos):
        dur = max(1, self._player.duration())
        if not self._slider.isSliderDown():
            self._slider.setValue(int(pos * 1000 / dur))
        secs = pos // 1000
        self._time.setText("{}:{:02d}".format(secs // 60, secs % 60))

    def closeEvent(self, event):
        if self._player is not None:
            self._player.stop()
            self._player.setVideoOutput(None)
        type(self)._current = None
        super().closeEvent(event)


class DocEditorDialog(QtWidgets.QDialog):
    """配方文档编辑器（非模态子窗口）：左编辑、右实时预览。

    - 插入图片/视频把文件复制进配方 assets/，正文插入相对引用
    - 预览自动刷新（防抖）；GIF 原地动、视频渲染成播放按钮
    - 确认/取消模式：编辑期间不落盘，点「确认」才保存并关窗（发
      docSaved 信号，主面板同步刷新文档预览）；「取消」/关窗丢弃改动
      （有未保存修改先确认），本次会话插入的 assets 一并清理
    """

    docSaved = QtCore.Signal(str)   # recipe 内部名

    def __init__(self, parent, info, display_label=None):
        super().__init__(parent)
        self.setWindowTitle("文档 - {}".format(display_label
                                              or info.display_label))
        self.setModal(False)
        self.resize(960, 620)
        self._info = info
        self._name = info.name
        self._doc_dir = metadata.doc_dir(self._name)
        self._inserted_assets = []   # 本次会话新插入的 assets 相对路径

        self.editor = QtWidgets.QPlainTextEdit()
        # 没有文档就是空白（不自动建模板文件）——确认保存后 doc.md 才存在，
        # 主面板的"有文档渲染 markdown / 无文档显示备注"随之切换
        self.editor.setPlainText(metadata.read_doc(self._name))
        self.editor.document().setModified(False)
        self.preview = MarkdownMediaView(self._doc_dir)

        insert_img = QtWidgets.QPushButton("插入图片...")
        insert_media = QtWidgets.QPushButton("插入视频/GIF...")
        open_dir_btn = QtWidgets.QPushButton("打开文档文件夹")
        tip = QtWidgets.QLabel("Markdown 语法；图片/视频会复制进 assets/ 并用相对路径引用")
        tip.setStyleSheet("color: #888888;")
        btns = QtWidgets.QHBoxLayout()
        btns.addWidget(tip, 1)
        btns.addWidget(insert_img)
        btns.addWidget(insert_media)
        btns.addWidget(open_dir_btn)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setHandleWidth(4)
        splitter.addWidget(self.editor)
        splitter.addWidget(self.preview)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([480, 470])

        bbox = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bbox.accepted.connect(self._confirm_save)
        bbox.rejected.connect(self._cancel)

        lay = QtWidgets.QVBoxLayout(self)
        lay.addLayout(btns)
        lay.addWidget(splitter, 1)
        lay.addWidget(bbox)
        localize_buttons(self)   # 布局收养后才有父子关系，Ok → 确认、Cancel → 取消

        insert_img.clicked.connect(lambda: self._insert_asset(False))
        insert_media.clicked.connect(lambda: self._insert_asset(True))
        open_dir_btn.clicked.connect(self._open_dir)

        self._render_timer = QtCore.QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(PREVIEW_DEBOUNCE_MS)
        self._render_timer.timeout.connect(self._render_preview)
        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.setTabChangesFocus(False)

        self._render_preview()

    # ---------------- 编辑 / 保存 ----------------

    def _on_text_changed(self):
        self._render_timer.start()   # 只刷新右侧预览；落盘等「确认」

    def _confirm_save(self):
        try:
            metadata.write_doc(self._name, self.editor.toPlainText())
        except OSError as exc:
            log.warning("save doc failed for %s: %s", self._name, exc)
            warn(self, "保存失败", str(exc))
            return   # 保存失败不关窗，内容留在编辑器里
        self.docSaved.emit(self._name)
        self.accept()

    def _cancel(self):
        if self._confirm_discard():
            self._discard_inserted_assets()
            self.reject()

    def _confirm_discard(self):
        """有未保存修改时确认丢弃；返回 True=继续关闭。"""
        if not self.editor.document().isModified():
            return True
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("放弃修改")
        box.setText("有未保存的修改，确定丢弃？")
        box.setStandardButtons(QtWidgets.QMessageBox.Yes
                               | QtWidgets.QMessageBox.No)
        localize_buttons(box)   # Yes → 确认、No → 取消
        return box.exec_() == QtWidgets.QMessageBox.Yes

    def _discard_inserted_assets(self):
        """取消时清理本次会话插入的 assets（正文已丢弃，引用不存在）。"""
        for rel in self._inserted_assets:
            path = os.path.join(self._doc_dir, rel.replace("/", os.sep))
            try:
                os.remove(path)
            except OSError as exc:
                log.debug("cleanup asset %s failed: %s", path, exc)
        self._inserted_assets = []

    def _insert_asset(self, media):
        start = self._doc_dir
        if media:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "插入视频或 GIF", start, metadata.MEDIA_FILTER)
        else:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "插入图片", start,
                "图片 (*.png *.jpg *.jpeg *.gif *.webp *.bmp);;全部文件 (*.*)")
        if not path:
            return
        try:
            rel = metadata.insert_asset(self._name, path)
        except (OSError, RuntimeError) as exc:
            warn(self, "插入失败", str(exc))
            return
        alt = os.path.basename(path)
        self._inserted_assets.append(rel)
        self.editor.insertPlainText("![{}]({})".format(alt, rel))

    def _open_dir(self):
        QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(self._doc_dir))

    # ---------------- 预览渲染 ----------------

    def _render_preview(self):
        self.preview.set_markdown_with_media(self.editor.toPlainText())

    def closeEvent(self, event):
        # X 关闭等同取消：有未保存修改先确认丢弃（拒绝则不关窗）
        if self._confirm_discard():
            self._discard_inserted_assets()
            super().closeEvent(event)
        else:
            event.ignore()
