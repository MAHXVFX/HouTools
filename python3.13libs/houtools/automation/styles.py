"""Automation 样式常量。

集中管理主窗口 `STYLE_SHEET`（暗色主题：#18181b 底 + #0d6399 蓝 /
#8a5cf5 紫强调，与项目其他工具的 styles 常量共享同一套配色语义）。

设计意图:
- 颜色用字面量写死（不抽常量）：这套配色只服务本面板，真需要跨模块共享时再抽
- 关键 objectName (`startBtn` / `taskSlot`) 在样式表中通过 ``#name`` 选择器特化样式;
  `addBtn` / `removeBtn` 的样式在 window.py 里用部件级内联样式实现
  （pane 内窗口级规则会被 Houdini 全局样式表压过）
"""

# 暗色主题基色
STYLE_SHEET = """
QDialog { background-color: #18181b; }
QPushButton { background-color: #2d2d2d; color: #e0e0e0; border: none;
              padding: 6px 16px; border-radius: 4px; font-size: 13px; }
QPushButton:hover { background-color: #3d3d3d; }
QPushButton:pressed { background-color: #0d6399; }
QPushButton#startBtn { background-color: #0d6399; color: white; font-weight: bold; }
QPushButton#startBtn:hover { background-color: #0e7bc9; }
QComboBox { background-color: #2d2d2d; color: #e0e0e0; border: 1px solid #3d3d3d;
            padding: 4px 8px; border-radius: 4px; }
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView { background-color: #2d2d2d; color: #e0e0e0;
                               selection-background-color: #0d6399; }
/* 配置下拉:突出显示(蓝色边框 + 醒目下拉按钮区) */
QComboBox#configCombo {
    background-color: #1d1d20; color: #e0e0e0;
    border: 2px solid #0d6399;
    padding: 4px 8px; border-radius: 4px;
    min-height: 22px;
}
QComboBox#configCombo:hover {
    border-color: #0e7bc9; background-color: #252528;
}
QComboBox#configCombo:focus {
    border-color: #8a5cf5; background-color: #252528;
}
QComboBox#configCombo::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border: none;
    background-color: transparent;  /* SVG 自身有蓝色圆形,无背景 */
}
QComboBox#configCombo::drop-down:hover {
    background-color: rgba(13, 99, 153, 60);  /* hover 微高亮 */
}
QComboBox#configCombo::drop-down:on {
    background-color: rgba(138, 92, 245, 80);  /* 按下紫色微高亮 */
}
QComboBox#configCombo::down-arrow {
    /* SVG 由 window.py 注入(combo-level stylesheet,
       路径用 Path(__file__) 算绝对,避免 CWD 不可靠) */
    image: none;
    width: 16px; height: 16px;
    margin-right: 4px;
}
QLineEdit { background-color: #2d2d2d; color: #e0e0e0; border: 1px solid #3d3d3d;
            padding: 4px 8px; border-radius: 4px; }
QCheckBox { color: #e0e0e0; spacing: 6px; }
QScrollArea { border: none; background-color: transparent; }
QLabel { background-color: transparent; color: #e0e0e0; border: none; }
QWidget#taskSlot { background-color: #252528; }
QWidget#taskSlot[selected="true"],
QWidget#taskSlot[dragging="true"] { background-color: #2d2d32; }
QWidget#taskSlot[selected="true"] QLabel#taskSlotHandle { color: #0d6399; }
QWidget#taskSlot[dragging="true"] { border: 1px solid #0d6399; }
QLabel#taskSlotHandle { background-color: transparent; padding: 2px 4px; }
QLabel#taskSlotHandle:hover { background-color: #2d2d32; }
/* 工具栏按钮：用 id 选择器确保压过 Houdini 全局样式表
   （类型选择器 QPushButton 在 pane 内会被全局规则覆盖） */
QPushButton#autoFillBtn, QPushButton#clearBtn, QPushButton#settingsBtn {
    background-color: #2d2d2d; color: #e0e0e0; border: none;
    padding: 6px 16px; border-radius: 4px; font-size: 13px; }
QPushButton#autoFillBtn:hover, QPushButton#clearBtn:hover,
QPushButton#settingsBtn:hover { background-color: #3d3d3d; }
QPushButton#autoFillBtn:pressed, QPushButton#clearBtn:pressed,
QPushButton#settingsBtn:pressed { background-color: #0d6399; }
"""
