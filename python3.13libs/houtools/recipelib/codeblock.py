"""代码块：Recipe Library 的文本型卡片（VEX / Python 片段）。

**一卡一文件**（2026-10-18 用户拍板，对照 Houdini 官方 vex include 的
存放方式 `$HFS/houdini/vex/include/*.h`）：代码存
`settings/vex_python_lib/vex/<内部名>.h` 与
`settings/vex_python_lib/python/<内部名>.py`，文件名 = 用户命名的内部
名（首次创建时设置、查重、之后不可改）；收藏/标签/颜色/缩略图/显示名
/文档仍按内部名键控复用 metadata 层，侧栏「代码块」段、搜索、卡片主
题色自动生效。

交互语义（2026-10 用户拍板）：
- 双击卡片 = 把代码**追加**到全部选中节点的代码参数（VEX→snippet、
  Python→python），不是覆盖；无匹配参数的节点逐个跳过并汇报
- 拖入文本编辑框（VEX 编辑器等）= 真 QDrag + text/plain，Houdini
  编辑器原生接受后在落点光标处插入（原生拖放指示随编辑器自带，追加
  语义由 Qt 文本拖放保证）。只做这一个方案，不做程序化插入兜底；
  若实测编辑器不接受外来文本拖放，整体换方案 B（见调研报告），不并存。
  **MIME 文本必须 CRLF**：Houdini VEX 编辑框的行模型按 \\r\\n 切行，
  投 \\n-only 会整段挤成一行且排版错乱（2026-10-18 会话实测）
- 拖入网络编辑器 = 外部 MIME 不被网络编辑器接受，QDrag.exec 返回
  IgnoreAction → 按松手点创建对应节点（VEX→attribwrangle、
  Python→python SOP）并填入代码
- 卡片无自定义缩略图时，缩略图区直接渲染代码文本（深底 + 语法色，
  可读性优先，见 render_code_pixmap）

无头约束：import hou 一律在函数内；模型/高亮器/弹窗/渲染可无头测试。
"""

import os
import re
from datetime import datetime
from dataclasses import dataclass, field

from PySide6 import QtCore, QtGui, QtWidgets

from houtools.core.constants import SETTINGS_DIR
from houtools.core.log import get_logger
from houtools.recipelib import metadata
from houtools.recipelib.vex_keywords import VEX_FUNCTIONS
from houtools.ui.dialogs import localize_buttons, warn

log = get_logger("recipelib.codeblock")

LANGUAGE_LABELS = {"vex": "VEX", "python": "Python"}

# 拖到网络编辑器时按语言创建的节点 / 承载代码的参数名（22.0.429 无头实测）
_NODE_TYPE_FOR_LANGUAGE = {"vex": "attribwrangle", "python": "python"}
_PARM_FOR_LANGUAGE = {"vex": "snippet", "python": "python"}


# --------------------------------------------------------------------------
# 数据模型与文件存储（一卡一文件，见模块 docstring）
# --------------------------------------------------------------------------

# 库根目录：settings/vex_python_lib/{vex,python}/，各语言一个子文件夹；
# 所有路径在调用时由它派生（smoke test 打补丁只换这一个常量）
CODELIB_ROOT = SETTINGS_DIR / "vex_python_lib"
FILE_EXT = {"vex": ".h", "python": ".py"}
# 内部名 = 文件名 stem（用户命名）：字母/数字开头，只收字母数字 _ -
_STEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def code_dir(language):
    """某语言的代码库文件夹（不自动创建；写文件时 mkdir）。"""
    return CODELIB_ROOT / ("python" if language == "python" else "vex")


def sanitize_stem(text):
    """用户输入 → 合法文件名 stem：掐空白，其余不合法字符原样保留交由
    validate_stem 报错（不静默改写——内部名就是文件名，悄悄变形会误导）。"""
    return str(text or "").strip()


def validate_stem(text):
    """创建时的内部名校验：返回 (合法, 提示文案)。合法与否与文件系统
    无关的规则固定在此（弹窗实时校验 + 保存侧共用同一结论）。"""
    stem = sanitize_stem(text)
    if not stem:
        return False, "名称不能为空"
    if not _STEM_RE.match(stem):
        return False, "只能用字母、数字、_ 或 -（字母或数字开头，≤64 字符）"
    if stem_exists(stem):
        return False, "该名称已被使用，换一个"
    return True, "名称可用"


def stem_exists(stem):
    """内部名查重：两个语言目录都查（同一内部名只能有一张卡片），
    Windows 文件系统大小写不敏感，比对也大小写不敏感。"""
    stem_low = stem.lower()
    for language in metadata.CODE_LANGUAGES:
        ext = FILE_EXT[language]
        try:
            names = os.listdir(str(code_dir(language)))
        except OSError:
            continue
        for fn in names:
            if fn.lower() == (stem + ext).lower():
                return True
    return False


def free_stem(stem):
    """返回不撞名的可用内部名（迁移/旧包导入的兜底自动让名；创建侧
    是硬拒绝不经此路）。"""
    unique = sanitize_stem(stem)
    if not _STEM_RE.match(unique):
        unique = metadata.sanitize_code_name(stem) or "vex_code"
    base, i = unique, 2
    while stem_exists(unique):
        unique = "{}_{}".format(base, i)
        i += 1
    return unique


def code_path(name, language):
    """内部名 -> 代码文件路径（.h=VEX、.py=Python）。"""
    return code_dir(language) / (name + FILE_EXT[language])


def read_code(name, language):
    """读代码文件；缺失/读失败返回空串（文件可能被外部删除）。"""
    path = code_path(name, language)
    try:
        with open(str(path), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError as exc:
        log.warning("cannot read code file %s: %s", path, exc)
        return ""


def save_code(name, language, code):
    """写代码文件（临时文件 + os.replace 原子替换，项目通用手法）。

    语言变更 = 文件搬到另一语言目录（旧扩展名文件删除）。返回落盘路径。
    """
    if not _STEM_RE.match(name):
        raise RuntimeError("内部名不合法: {}".format(name))
    language = language if language in metadata.CODE_LANGUAGES else "vex"
    path = code_path(name, language)
    old_paths = []
    for lang in metadata.CODE_LANGUAGES:
        p = code_path(name, lang)
        if p != path and p.exists():
            old_paths.append(p)
    d = code_dir(language)
    d.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".~tmp%d" % os.getpid())
    try:
        with open(str(tmp), "w", encoding="utf-8", newline="\n") as f:
            f.write(str(code or ""))
        os.replace(str(tmp), str(path))
    finally:
        if os.path.exists(str(tmp)):
            try:
                os.remove(str(tmp))
            except OSError as exc:
                log.debug("cannot remove code tmp %s: %s", tmp, exc)
    for p in old_paths:   # 换语言重设：旧扩展名文件摘除
        try:
            os.remove(str(p))
        except OSError as exc:
            log.warning("cannot remove old code file %s: %s", p, exc)
    return str(path)


def delete_code(name, language):
    """删除代码文件（元数据清理由调用方做，同删除 recipe 的分工）。"""
    path = code_path(name, language)
    if os.path.isfile(str(path)):
        try:
            os.remove(str(path))
        except OSError as exc:
            log.warning("cannot remove code file %s: %s", path, exc)
            raise RuntimeError("删除失败: {}".format(exc))


def list_code_infos():
    """扫描两个语言目录 -> CodeInfo 列表（按显示名排序，网格顺序稳定）。

    modified = 文件 mtime（文件即数据源，外部改动自动反映）；平铺不
    递归（一卡一文件，子目录不在约定内，忽略）。
    """
    out = []
    for language in metadata.CODE_LANGUAGES:
        ext = FILE_EXT[language]
        d = code_dir(language)
        try:
            names = sorted(os.listdir(str(d)))
        except OSError:
            continue
        for fn in names:
            path = d / fn
            if not fn.lower().endswith(ext) or not path.is_file():
                continue
            stem = fn[: -len(ext)]
            try:
                mtime = datetime.fromtimestamp(
                    path.stat().st_mtime).isoformat(timespec="seconds")
            except OSError as exc:
                log.warning("stat code file %s failed: %s", path, exc)
                mtime = ""
            out.append(CodeInfo(name=stem, language=language,
                                code=read_code(stem, language),
                                modified=mtime))
    out.sort(key=lambda c: c.display_label.lower())
    return out


def migrate_legacy_code_blocks():
    """旧版（recipelib.json 的 code_blocks 段）一次性迁移为一卡一文件。

    幂等：段空（或已迁过）直接返回 0。每个旧条目落一个 .h/.py（内部名
    去掉 htcode:: 前缀、撞名时加序号），元数据键 htcode::X 改挂到新名。
    """
    blocks = metadata.take_code_blocks_for_migration()
    if not blocks:
        return 0
    moved = 0
    for old_name, rec in blocks.items():
        if not isinstance(rec, dict):
            continue
        stem = old_name.split("::")[-1] or old_name
        language = rec.get("language")
        if language not in metadata.CODE_LANGUAGES:
            language = "vex"
        unique = free_stem(stem)
        save_code(unique, language, str(rec.get("code") or ""))
        if unique != old_name:
            metadata.rekey_metadata_name(old_name, unique)
        moved += 1
        if unique != stem:
            log.info("migrated code block %r -> %r (name taken)",
                     old_name, unique)
    log.info("migrated %d legacy code blocks to vex_python_lib files",
             moved)
    return moved


@dataclass
class CodeInfo:
    """代码块信息（browser 的卡片渲染路径与 RecipeInfo 共用）。"""

    name: str            # 内部名 = 文件名 stem（用户命名，创建后不可改）
    language: str = "vex"
    code: str = ""
    created: str = ""
    modified: str = ""   # 文件 mtime（文件即数据源）
    label: str = ""
    category: str = "code"
    submenu: str = ""
    comment: str = ""
    author: str = ""
    patterns: list = field(default_factory=list)
    library: str = ""        # 无库文件；预览"来源"列按空串显示"面板内建"
    under_hfs: bool = False  # 始终可删
    houdini_version: str = ""
    net_category: str = ""
    node_count: int = -1

    @property
    def is_code(self):
        """代码块标记（browser 侧 filter/水印/分支判别用；RecipeInfo 无
        此属性，getattr 默认 False）。"""
        return True

    @property
    def display_label(self):
        if self.label:
            return self.label
        return self.name.split("::", 1)[-1] or self.name

    @property
    def language_label(self):
        return LANGUAGE_LABELS.get(self.language, self.language)


# --------------------------------------------------------------------------
# 语法高亮（QSyntaxHighlighter；编辑弹窗 / 预览 / 缩略图共用）
# --------------------------------------------------------------------------

_COLORS = {
    "keyword": "#c586c0",
    "type": "#4ec9b0",
    "func": "#dcdcaa",
    "string": "#ce9178",
    "comment": "#6a9955",
    "number": "#b5cea8",
    "directive": "#c586c0",
    "binding": "#9cdcfe",
    "builtin": "#9cdcfe",
}

# VEX：类型 / 保留字 / wrangle 里常读的无 @ 全局量（小写标识符那批）
VEX_TYPES = frozenset((
    "void", "float", "int", "string", "vector", "vector2", "vector4",
    "matrix", "matrix2", "matrix3", "dict", "bsdf", "uv"))
VEX_KEYWORDS = frozenset((
    "if", "else", "for", "while", "do", "foreach", "return", "break",
    "continue", "function", "struct", "export", "import"))
VEX_GLOBALS = frozenset((
    "ptnum", "numpt", "primnum", "numprim", "vtxnum", "numvtx",
    "Time", "Frame", "SimTime", "dPdx", "dPdy", "dPdz", "pt", "prim", "vtx"))

PYTHON_KEYWORDS = frozenset((
    "False", "None", "True", "and", "as", "assert", "async", "await",
    "break", "class", "continue", "def", "del", "elif", "else", "except",
    "finally", "for", "from", "global", "if", "import", "in", "is",
    "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try",
    "while", "with", "yield"))
PYTHON_BUILTINS = frozenset((
    "abs", "all", "any", "bin", "bool", "bytearray", "bytes", "callable",
    "chr", "classmethod", "compile", "complex", "delattr", "dict", "dir",
    "divmod", "enumerate", "eval", "exec", "filter", "float", "format",
    "frozenset", "getattr", "globals", "hasattr", "hash", "hex", "id",
    "input", "int", "isinstance", "issubclass", "iter", "len", "list",
    "locals", "map", "max", "memoryview", "min", "next", "object", "oct",
    "open", "ord", "pow", "print", "property", "range", "repr", "reversed",
    "round", "self", "cls", "set", "setattr", "slice", "sorted",
    "staticmethod", "str", "sum", "super", "tuple", "type", "vars", "zip",
    "ValueError", "TypeError", "KeyError", "IndexError", "RuntimeError",
    "ZeroDivisionError", "AttributeError", "OSError", "Exception"))

# 单遍主正则：同一位置 comment/string/number 等优先于 word 命中并整段
# 消费，天然规避"独立规则互相覆色"（字符串里的 #、注释里的引号都不会
# 误着色）。VEX binding 的 (?<![A-Za-z0-9_]) 不能用 \b——"@P" 行首时
# @ 是非词字符、前面构不成词边界，\b 会失配
_VEX_MASTER = QtCore.QRegularExpression(
    r"(?P<comment>//[^\n]*)"
    r"|(?P<string>\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*')"
    r"|(?P<number>\b(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)\b)"
    r"|(?P<binding>(?<![A-Za-z0-9_])[fsuivpmd24]?@[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<directive>^[ \t]*#\w+)"
    r"|(?P<word>[A-Za-z_][A-Za-z0-9_]*)")
_PY_MASTER = QtCore.QRegularExpression(
    r"(?P<comment>#[^\n]*)"
    r"|(?P<string>\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*')"
    r"|(?P<number>\b(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)\b)"
    r"|(?P<deco>@[A-Za-z_][A-Za-z0-9_.]*)"
    r"|(?P<word>[A-Za-z_][A-Za-z0-9_]*)")

_CALL_LOOKAHEAD = re.compile(r"\s*\(")


def _fmt(color):
    f = QtGui.QTextCharFormat()
    f.setForeground(QtGui.QColor(color))
    return f


def _group_fmt():
    return {key: _fmt(color) for key, color in _COLORS.items()}


class _RuleHighlighter(QtGui.QSyntaxHighlighter):
    """单遍主正则 + 词分类 + 块级多行串/注释（跨行靠 blockState 接力，
    Qt 官方 highlighting 示例手法）。函数名着色 = 已知函数表命中，或
    任意词后跟 "("（自定义函数也能着色）。"""

    MASTER = None        # QRegularExpression（命名分组，见模块内正则定义）
    GROUP_FMT = None     # 分组/词类 -> QTextCharFormat
    KEYWORDS = frozenset()
    TYPES = frozenset()
    BUILTINS = frozenset()
    FUNCTIONS = frozenset()
    MULTILINE = None     # state -> (开启正则, 关闭正则)；None = 无多行构造
    ML_FMT = None        # 多行构造整段着色

    def __init__(self, document):
        super().__init__(document)
        self._group_names = ("comment", "string", "number", "binding",
                             "deco", "directive")

    def highlightBlock(self, text):
        it = self.MASTER.globalMatch(text)
        while it.hasNext():
            m = it.next()
            for gname in self._group_names:
                if m.capturedStart(gname) >= 0:
                    fmt = self.GROUP_FMT.get(gname)
                    if fmt is not None:
                        self.setFormat(m.capturedStart(gname),
                                       m.capturedLength(gname), fmt)
                    break
            else:
                word = m.captured("word")
                if word:
                    self._color_word(text, m, word)
        self._highlight_multiline(text)

    def _color_word(self, text, m, word):
        fmt = None
        if word in self.KEYWORDS:
            fmt = self.GROUP_FMT["keyword"]
        elif word in self.TYPES:
            fmt = self.GROUP_FMT["type"]
        elif word in self.BUILTINS:
            fmt = self.GROUP_FMT["builtin"]
        elif word in self.FUNCTIONS:
            fmt = self.GROUP_FMT["func"]
        elif _CALL_LOOKAHEAD.match(text, m.capturedEnd()):
            fmt = self.GROUP_FMT["func"]
        if fmt is not None:
            self.setFormat(m.capturedStart("word"),
                           m.capturedLength("word"), fmt)

    def _highlight_multiline(self, text):
        """/* */ 与三引号串：previousBlockState 接力跨行；同行内开闭
        正常着色，同行关闭后还能继续开（罕见）也正确。"""
        if not self.MULTILINE:
            self.setCurrentBlockState(0)
            return
        state = max(0, self.previousBlockState())
        pos = 0
        self.setCurrentBlockState(0)
        while True:
            if state == 0:
                best = None
                for st, (start_rx, _end_rx) in self.MULTILINE.items():
                    m = start_rx.match(text, pos)
                    if m.hasMatch() and (best is None
                                         or m.capturedStart() < best[1]):
                        best = (st, m.capturedStart(), m.capturedEnd())
                if best is None:
                    return
                st, start, after = best
                close_m = self.MULTILINE[st][1].match(text, after)
                if close_m.hasMatch():
                    self.setFormat(start, close_m.capturedEnd() - start,
                                   self.ML_FMT)
                    pos = close_m.capturedEnd()
                    state = 0
                    continue
                self.setFormat(start, len(text) - start, self.ML_FMT)
                self.setCurrentBlockState(st)
                return
            close_m = self.MULTILINE[state][1].match(text, pos)
            if close_m.hasMatch():
                self.setFormat(pos, close_m.capturedEnd() - pos, self.ML_FMT)
                pos = close_m.capturedEnd()
                state = 0
                continue
            self.setFormat(pos, len(text) - pos, self.ML_FMT)
            self.setCurrentBlockState(state)
            return


class VexHighlighter(_RuleHighlighter):
    MASTER = _VEX_MASTER
    GROUP_FMT = _group_fmt()
    KEYWORDS = VEX_KEYWORDS
    TYPES = VEX_TYPES
    BUILTINS = VEX_GLOBALS
    FUNCTIONS = frozenset(VEX_FUNCTIONS)
    ML_FMT = _fmt(_COLORS["comment"])
    MULTILINE = {1: (QtCore.QRegularExpression(r"/\*"),
                     QtCore.QRegularExpression(r"\*/"))}


class PythonHighlighter(_RuleHighlighter):
    MASTER = _PY_MASTER
    GROUP_FMT = _group_fmt()
    KEYWORDS = PYTHON_KEYWORDS
    TYPES = frozenset()
    BUILTINS = PYTHON_BUILTINS
    FUNCTIONS = frozenset()
    ML_FMT = _fmt(_COLORS["string"])
    MULTILINE = {1: (QtCore.QRegularExpression(r'"""'),
                     QtCore.QRegularExpression(r'"""')),
                 2: (QtCore.QRegularExpression(r"'''"),
                     QtCore.QRegularExpression(r"'''"))}


def make_highlighter(language, document):
    """按语言给 document 装高亮器；旧的须先 setDocument(None) 摘除
    （QSyntaxHighlighter 挂在 document 上，不摘会两个同时生效）。"""
    cls = PythonHighlighter if language == "python" else VexHighlighter
    return cls(document)


def mono_font():
    """代码用等宽字体：Consolas 优先（Windows 随附），Monospace 兜底。"""
    f = QtGui.QFont("Consolas")
    f.setStyleHint(QtGui.QFont.Monospace, QtGui.QFont.PreferDefault)
    return f


# --------------------------------------------------------------------------
# 代码编辑弹窗（新建 / 编辑共用；确认/取消模式，取消不落盘）
# --------------------------------------------------------------------------

class _CodeEditor(QtWidgets.QPlainTextEdit):
    """代码编辑区：行号栏（Qt 官方 Code Editor Example 手法）+ Tab 键
    输入 4 空格。等宽字体经 apply_mono() 显式设置——fonts.py 的
    ChildAdded 过滤器会把工具字体刷给刚挂树的控件（弹窗把编辑器加进
    布局那一刻），必须在挂树之后（弹窗 __init__ 尾部）再压回等宽。"""

    class _NumberArea(QtWidgets.QWidget):
        def __init__(self, editor):
            super().__init__(editor)
            self._editor = editor

        def sizeHint(self):
            return QtCore.QSize(self._editor.number_area_width(), 0)

        def paintEvent(self, event):
            self._editor.paint_number_area(event)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self._num_area = self._NumberArea(self)
        self.blockCountChanged.connect(self._update_number_area_width)
        self.updateRequest.connect(self._update_number_area)
        self._update_number_area_width(0)

    def apply_mono(self):
        f = mono_font()
        f.setPointSize(10)
        self.setFont(f)
        self._num_area.setFont(f)

    def number_area_width(self):
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 14 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_number_area_width(self, _count):
        self.setViewportMargins(self.number_area_width(), 0, 0, 0)

    def _update_number_area(self, rect, dy):
        if dy:
            self._num_area.scroll(0, dy)
        else:
            self._num_area.update(0, rect.y(), self._num_area.width(),
                                  rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_number_area_width(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._num_area.setGeometry(QtCore.QRect(
            cr.left(), cr.top(), self.number_area_width(), cr.height()))

    def paint_number_area(self, event):
        painter = QtGui.QPainter(self._num_area)
        painter.fillRect(event.rect(), QtGui.QColor("#141417"))
        painter.setPen(QtGui.QColor("#55555c"))
        block = self.firstVisibleBlock()
        num = block.blockNumber()
        top = round(self.blockBoundingGeometry(block)
                    .translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(2, top, self._num_area.width() - 8,
                                 self.fontMetrics().height(),
                                 QtCore.Qt.AlignRight | QtCore.Qt.AlignTop,
                                 str(num + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            num += 1
        painter.end()

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Tab \
                and event.modifiers() == QtCore.Qt.NoModifier \
                and not self.textCursor().hasSelection():
            self.textCursor().insertText("    ")
            return
        super().keyPressEvent(event)


class CodeEditorDialog(QtWidgets.QDialog):
    """代码块新建/编辑弹窗：内部名称 + 语言下拉 + 高亮编辑区，确认/取消
    模式（确认不落盘，落盘由 browser 在 exec 返回后统一做）。

    内部名称 = 文件名 stem，**仅首次创建可设**：实时查重（两语言目录
    合并查、大小写不敏感），重名/非法时提示红字并禁用确定键；编辑态
    名称只读（2026-10-18 用户拍板）。中文等展示层命名走「自定义显示名」
    （卡片右键），与本名称解耦。"""

    def __init__(self, parent, info=None):
        super().__init__(parent)
        self.setWindowTitle("编辑代码块" if info is not None
                            else "新建代码块")
        self.resize(680, 520)
        self.setMinimumSize(520, 380)
        self._create_mode = info is None

        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText(
            "内部名称（字母/数字/_/-；创建后不可改）")
        self.name_hint = QtWidgets.QLabel("")
        self.lang_combo = QtWidgets.QComboBox()
        for lang in metadata.CODE_LANGUAGES:
            self.lang_combo.addItem(LANGUAGE_LABELS[lang], lang)
        self.lang_combo.currentIndexChanged.connect(self._on_lang_changed)

        head = QtWidgets.QHBoxLayout()
        head.addWidget(QtWidgets.QLabel("名称:"))
        head.addWidget(self.name_edit, 1)
        head.addSpacing(8)
        head.addWidget(QtWidgets.QLabel("语言:"))
        head.addWidget(self.lang_combo)

        self.editor = _CodeEditor()
        self._highlighter = None

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        localize_buttons(buttons)   # OK/Cancel → 确定/取消
        self._ok_btn = buttons.button(QtWidgets.QDialogButtonBox.Ok)

        lay = QtWidgets.QVBoxLayout(self)
        lay.addLayout(head)
        lay.addWidget(self.name_hint)
        lay.addWidget(self.editor, 1)
        lay.addWidget(buttons)

        if info is not None:
            self.name_edit.setText(info.name)
            self.name_edit.setReadOnly(True)   # 内部名称创建后不可改
            self.name_edit.setToolTip("内部名称仅在首次创建时可设置")
            self.name_hint.setText("内部名称创建后不可修改")
            self.name_hint.setStyleSheet("color: #888888;")
            idx = self.lang_combo.findData(info.language)
            if idx >= 0:
                self.lang_combo.setCurrentIndex(idx)
            self.editor.setPlainText(info.code)
        else:
            self.name_edit.textChanged.connect(self._update_name_state)
        self._on_lang_changed()
        if self._create_mode:
            self._update_name_state()   # 空名即禁用确定键
        # 挂树之后压回等宽字体（fonts.py ChildAdded 过滤器刚刷过工具字体，
        # 见 _CodeEditor docstring）
        self.editor.apply_mono()
        self.editor.setFocus()

    def _update_name_state(self, *_args):
        ok, msg = validate_stem(self.name_edit.text())
        self.name_hint.setText(msg)
        self.name_hint.setStyleSheet(
            "color: #6a9955;" if ok else "color: #d1283e;")
        self._ok_btn.setEnabled(ok)

    def _on_lang_changed(self, *_args):
        lang = self.lang_combo.currentData() or "vex"
        if self._highlighter is not None:
            self._highlighter.setDocument(None)   # 摘旧高亮器防双重着色
        self._highlighter = make_highlighter(lang, self.editor.document())
        # setDocument 不清旧格式残留（python↔vex 规则覆盖面不同），重设
        # 文本重建文档最干净；代价是语言切换后光标回开头，可接受
        code = self.editor.toPlainText()
        if code:
            self.editor.setPlainText(code)

    def _on_accept(self):
        if self._create_mode:
            ok, msg = validate_stem(self.name_edit.text())
            if not ok:
                warn(self, "代码块", msg)
                return
        if not self.editor.toPlainText().strip():
            warn(self, "代码块", "代码内容为空。")
            return
        self.accept()

    def result_values(self):
        """(内部名, 语言, 代码)。"""
        return (sanitize_stem(self.name_edit.text()),
                self.lang_combo.currentData() or "vex",
                self.editor.toPlainText())


# --------------------------------------------------------------------------
# 卡片缩略图：代码文本渲染（无自定义缩略图时的默认显示）
# --------------------------------------------------------------------------

_THUMB_BG = "#17171b"
_TEXT_COLOR = "#d4d4d4"


def render_code_pixmap(code, language, w, h, dpr=1.0):
    """卡片缩略图：代码文本预览（可读性优先，2026-10-18 用户反馈）。

    - **无行号栏**：卡片面积有限，行号浪费宽度（预览面板的代码视图
      才带行号，那边用户已认可）
    - **字号只随缩略图高度取档、绝不随内容缩小**：长行折行、展示不
      下的内容按行自然截断——"内容可以不展示完，但字一定要看得清"
    - 深底 + 语法色；QTextDocument + 高亮器渲染完即弃
    """
    w, h = max(1, int(w)), max(1, int(h))
    dpr = dpr if dpr and dpr > 0 else 1.0
    pm = QtGui.QPixmap(int(w * dpr), int(h * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(QtGui.QColor(_THUMB_BG))

    doc = QtGui.QTextDocument()
    font = mono_font()
    font.setPixelSize(int(min(22, max(10, h / 4.5))))
    doc.setDefaultFont(font)
    doc.setDocumentMargin(0.0)
    doc.setPlainText(code if code.strip() else "（空代码块）")
    cursor = QtGui.QTextCursor(doc)
    cursor.select(QtGui.QTextCursor.Document)
    base = QtGui.QTextCharFormat()
    base.setForeground(QtGui.QColor(_TEXT_COLOR))
    cursor.mergeCharFormat(base)     # 默认前景色是黑，深底上必须提亮
    make_highlighter(language, doc)
    # 长行折行（不缩字）；竖向超出部分被裁掉 = 内容截断
    doc.setTextWidth(max(1.0, w - 8))

    painter = QtGui.QPainter(pm)
    painter.setClipRect(QtCore.QRectF(0, 0, w, h))
    painter.translate(4, 3)
    doc.drawContents(painter)
    painter.end()
    return pm


# --------------------------------------------------------------------------
# 应用到场景（hou 相关；一律函数内 import，只在主线程调用）
# --------------------------------------------------------------------------

_NETWORK_HINTS = {
    "Object": "OBJ（/obj 层级）",
    "Lop": "LOP（Solaris）",
    "Dop": "DOP",
    "Cop": "COP",
    "Cop2": "COP2",
    "Chop": "CHOP",
}


def append_to_nodes(nodes, language, code):
    """把代码**追加**到选中节点的代码参数（追加不是覆盖，用户拍板）。

    现有内容非空时以空行分隔接在其后；空内容直接写入。逐节点独立
    容错：无匹配参数 / 参数锁定跳过并记录原因，不中断整批（同
    paste_bbox 的逐分量容错约定）。整批一个 undo 组。返回
    (appended, skipped)：appended 是节点路径列表，skipped 是
    "路径: 原因" 列表；一个都没选中/全不匹配由调用方决定如何反馈。
    """
    import hou
    parm_name = _PARM_FOR_LANGUAGE.get(language)
    if not parm_name:
        raise RuntimeError("语言 {} 不支持填充".format(language))
    if not nodes:
        raise RuntimeError("请先在场景中选中目标节点")
    label = LANGUAGE_LABELS.get(language, language)
    appended, skipped = [], []
    with hou.undos.group("HouTools 追加代码块"):
        for node in nodes:
            try:
                parm = node.parm(parm_name)
                if parm is None:
                    skipped.append("{}: 无 {} 参数（不是 {} 代码节点）".format(
                        node.path(), parm_name, label))
                    continue
                existing = parm.evalAsString()
                if existing.strip():
                    parm.set(existing.rstrip() + "\n\n" + code.strip("\n"))
                else:
                    parm.set(code)
                appended.append(node.path())
            except Exception as exc:   # 锁定参数等：单点跳过不中断整批
                skipped.append("{}: {}".format(node.path(), exc))
    return appended, skipped


def create_node_with_code(parent, language, code, display_label="",
                          position=None):
    """在 parent 网络里创建对应节点并填入代码（拖代码块进网络编辑器）。

    仅 SOP 网络（attribwrangle / python SOP 都是 SOP 类）；层级不符抛
    RuntimeError（中文提示，browser 推状态栏红字）。节点名取显示名的
    ASCII token（中文显示名不进节点名——节点名 charset 限制，显示层
    不受影响）。创建失败不残留半截节点。
    """
    import hou
    ntype = _NODE_TYPE_FOR_LANGUAGE.get(language)
    if not ntype:
        raise RuntimeError("语言 {} 不支持创建节点".format(language))
    try:
        cat = parent.childTypeCategory().name()
    except Exception:
        cat = ""
    if cat != "Sop":
        have = _NETWORK_HINTS.get(cat, cat or "未知")
        raise RuntimeError("代码块节点需要 SOP 网络（geo 内部），"
                           "当前是 {} 网络".format(have))
    token = metadata.sanitize_code_name(display_label) \
        or ("python_code" if language == "python" else "vex_code")
    node = parent.createNode(ntype, token)
    try:
        node.parm(_PARM_FOR_LANGUAGE[language]).set(code)
    except Exception:
        node.destroy()
        raise
    if position is not None:
        node.setPosition(position)
    return node
