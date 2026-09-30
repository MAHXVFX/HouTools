# MAHX_Tools — Houdini 22 插件项目规划

> 目标：在 Houdini 22.0（Python 3.13）上从零构建一套可热加载开发的插件工具集。
> 顶栏入口：`MainMenuCommon.xml`；开发期无需重启 Houdini 即可迭代工具逻辑代码。

---

## 1. 环境事实（已逐项核实）

| 项目 | 结论 |
|------|------|
| Houdini 版本 | 22.0.429（`C:\Program Files\Side Effects Software\Houdini 22.0.429`） |
| Python | **3.13**（运行时 `python313`；库目录命名 `python3.13libs`） |
| PySide6 | **6.8.3 + shiboken6 内置**（`python313\lib\site-packages-forced\`），含 `QtWebEngineWidgets`；H22 新 UI 基于 Qt/QML，部件级 PySide6 UI 可正常使用 |
| 包机制 | `Documents/houdini22.0/packages/MAHX_Tools.json` 已安装，内容：`{"enable": true, "env": [{"HOUDINI_PATH": "$HOUDINI_PACKAGE_PATH/../MAHX_Tools"}]}` → **项目根目录已在 HOUDINI_PATH 上** |
| 菜单加载 | `$HOUDINI_MENU_PATH`（默认 = HOUDINI_PATH）逆序搜索 `MainMenuCommon.xml`，同名文件可 add / modify / remove 合并 |
| 主菜单热重载 | ❌ **不存在**。`hou.ui` 无 `reloadMenuFiles`；hscript `menurefresh` 只重载 OPmenu/PARMmenu/ParmGearMenu/CHGmenu 四个右键菜单文件 |
| 可运行时重载 | ✅ Python 模块（importlib）；✅ viewer states（`hou.ui.reloadViewerState(s)`）；✅ 右键菜单（`menurefresh`）；✅ .pypanel（面板自带 Reload 按钮） |

**核心结论**：主菜单 XML 结构（新增/删除/改名菜单项）必须重启 Houdini 生效；但菜单项 `scriptCode` 里的代码是点击时才执行的，只要写成"薄分发器"，工具逻辑代码即可 100% 热加载。开发期绝大多数改动是逻辑代码，菜单骨架一次性规划到位后，XML 很少再动。

---

## 2. 目录结构

```
MAHX_Tools/                        # 项目根 = Houdini 包目录（已在 HOUDINI_PATH）
├── MAHX_Tools.json                # 包清单副本（真正生效的是 packages/ 下那份；此份供分发安装）
├── MainMenuCommon.xml             # 顶部菜单（Houdini 规定的文件名）
├── python3.13libs/                # Houdini 自动加入 sys.path（命名必须精确到 3.13）
│   └── mahx/
│       ├── __init__.py            # 版本号等元信息，保持极薄
│       ├── dev/                   # ★ 开发期热加载框架（重载时自身跳过，保持稳定）
│       │   ├── __init__.py
│       │   ├── reloader.py        # reload_all()：按"叶子优先"顺序 importlib.reload 所有 mahx 模块
│       │   └── dispatcher.py      # 菜单 scriptCode 唯一入口：run("tool_id")
│       ├── core/                  # 基础设施
│       │   ├── constants.py       # 路径常量（项目根自动计算）
│       │   ├── log.py             # 统一 logging.getLogger("mahx")
│       │   └── settings.py        # JSON 设置管理
│       ├── tools/                 # 工具模块，约定：每个 <name>.py 暴露 run()
│       │   ├── __init__.py
│       │   └── example_tool.py    # 示例工具，用于验证热加载闭环
│       └── ui/
│           └── window_manager.py  # PySide6 顶层窗口登记/单例/统一关闭
├── python_panels/                 # （按需）Pane Tab 内嵌面板入口 .pypanel
├── toolbar/                       # （按需）工具架 .shelf
├── docs/
└── README.md
```

**为什么不用工具注册表**：注册表若随 `tools/__init__.py` 一起被 reload，重载顺序会导致子模块注册进"旧对象"。改为**约定优于注册**——dispatcher 按菜单 id 直接 `importlib.import_module("mahx.tools.<tool_id>")` 并调用其 `run()`，天然免疫重载顺序问题。

---

## 3. 菜单设计（MainMenuCommon.xml）

```xml
<?xml version="1.0" encoding="UTF-8"?>
<mainMenu>
  <menuBar>
    <subMenu id="mahx_tools_menu">
      <insertBefore>help_menu</insertBefore>
      <label>MAHX Tools</label>

      <scriptItem id="mahx.dev.reload">
        <label>Reload Modules (Dev)</label>
        <scriptCode><![CDATA[
import mahx.dev.reloader as _r
_r.reload_all()
]]></scriptCode>
      </scriptItem>

      <separatorItem/>

      <scriptItem id="mahx.example_tool">
        <label>Example Tool</label>
        <scriptCode><![CDATA[
import mahx.dev.dispatcher as _d
_d.run("example_tool")
]]></scriptCode>
      </scriptItem>
    </subMenu>
  </menuBar>
</mainMenu>
```

要点：
- 每个 `scriptCode` 只有 2 行：import 分发器 + 调用。分发器属于被跳过重载的 `mahx.dev`，永远引用最新工具模块。
- `reload_all()` 流程：`window_manager.close_all()`（先关已打开的 PySide6 窗口，防悬挂 C++ 对象）→ 叶子优先逐个 `importlib.reload` → 结果写状态栏 `hou.ui.setStatusMessage` + logger；单个模块失败不中断其余模块，错误汇总报告。
- `mahx.dev` 自身不参与重载（改了 dev 框架才需重启，频率极低）。

---

## 4. 热加载方案（手动按钮，已按需求确认）

| 改动类型 | 生效方式 |
|----------|----------|
| `mahx/` 下任意工具/核心/Ui 代码 | 菜单 `MAHX Tools → Reload Modules (Dev)`，立即生效 |
| 已打开的工具窗口 | Reload 前自动关闭，下次点击菜单重新创建，拿到的是新代码 |
| `python_panels/*.pypanel` | 面板 Pane 自带 Reload 按钮重新实例化 |
| viewer states（若将来开发） | `hou.ui.registerViewerStateFile` / `reloadViewerState` 原生支持 |
| 右键菜单定制（若将来需要） | `hou.hscript("menurefresh")` 即时生效 |
| **MainMenuCommon.xml 结构** | **重启 Houdini**（H22 硬约束，无绕过方案） |

开发工作流：写代码 → Ctrl+S → 菜单点 Reload → 点工具验证。仅当新增/删除/重命名菜单项时才重启。

---

## 5. 关键实现约定

- **线程**：QThread + Signal（沿用成熟模式）；任何回改 Houdini 的调用经 `hdefereval.executeDeferred` 派发主线程。
- **日志**：统一 `logging.getLogger("mahx")`，子模块用 `getChild`。
- **窗口**：所有 PySide6 顶层窗口必须经 `window_manager.open(id, factory)` 创建（单例 + 登记 Reload 可关）。
- **import hou**：函数内或 try/except，保证模块可在 Houdini 外被静态分析。
- **路径**：`core/constants.py` 由 `__file__` 反推项目根，不依赖 cwd。
- **可选调试**：向 `python313\python.exe` 安装 debugpy 后可在 VS Code 断点调试 Houdini 内代码。

---

## 6. 实施阶段

- **Phase 1 — 骨架与热加载闭环**：目录结构、MainMenuCommon.xml、`mahx.dev.reloader/dispatcher`、`window_manager`、`example_tool`；验收标准 = 修改 example_tool 代码 → Reload → 不重启看到变化。
- **Phase 2 — 基础设施**：constants / log / settings，替换 example_tool 中的临时实现。
- **Phase 3 — 正式工具开发**：按需求逐个添加 `mahx/tools/` 模块与菜单项。
