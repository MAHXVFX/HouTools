# HouTools (Houdini 22 / Python 3.13)

Houdini 22 插件工具集，开发期支持**手动热加载**：改完代码无需重启 Houdini。

> 仅支持 Windows：Automation 的窗口置顶用 Win32 API，部分工具的"打开所在文件夹"依赖 explorer。

## 安装

1. 把本文件夹整个拷贝到 `Documents/houdiniXX.X/HouTools`（与 `packages/` 同级）；
2. 把项目根目录下的 `HouTools.json` 放入 `Documents/houdiniXX.X/packages/`；
3. 启动 Houdini，顶部菜单即出现 **HouTools**。

若需将文件夹放置到其他位置，修改 `packages/HouTools.json` 里的路径即可。

## 开发工作流

| 改动 | 生效方式 |
|------|----------|
| `houtools/` 下任意工具/核心/UI 代码 | 菜单 `HouTools → Reload Modules (Dev)`，立即生效 |
| 已打开的工具窗口 | Reload 时自动关闭，重新打开即新代码 |
| `python_panels/*.pypanel`（如新增） | 面板自带 Reload 按钮 |
| `MainMenuCommon.xml` / `NetworkViewMenu.xml` 菜单结构 | **重启 Houdini**（H22 硬约束，无法运行时重载） |
| `houtools/dev/` 热加载框架自身 | 重启 Houdini（刻意不参与重载） |

验收闭环：修改任意 `houtools/` 模块 → 保存 → 菜单点 Reload → 重新打开对应工具，改动即生效
（已打开的工具窗口会在 Reload 时自动关闭）。

## 工具列表

| 工具 | 菜单项 / 键位 | 说明 |
|------|--------|------|
| Automation | `HouTools → Automation`（Python Panel） | 自动化批处理：节点按钮点击 / Flipbook 拍屏 / HomeAssistant Webhook / 打开DW |
| 粘贴为 Object Merge | 网络编辑器 `HouTools` 菜单 / `Ctrl+Shift+V` | 复制节点后，按"目标上下文 × 源类别"在鼠标位置粘贴引用节点；键位在 `HouTools → Paste Hotkey Settings` 修改 |
| 拖放导入 Alembic | 拖 `.abc` 文件到网络编辑器 | 松手即在鼠标位置创建导入节点：obj 层级 = Alembic Archive（自动构建层级），SOP 层级 = alembic 节点；支持多文件（基于 Houdini 官方 externaldragdrop 钩子） |
| 视频转序列图 | `HouTools → Video to Sequence` | ffmpeg 提取视频为 JPG 序列（帧级进度，质量/起始帧/位数/前缀可调），可选自动设置相机 Background Image |
| Hdr Library | `HouTools → Hdr Library` | HDR 环境贴图库浏览器：缩略图网格（后台生成）、子文件夹分类 + 收藏，选中灯光后双击即贴图 |
| Recipe Library | `HouTools → Recipe Library` | recipes 资产库浏览器：官方 recipes 卡片网格浏览（缩略图/标签/主题色），树形侧栏过滤，双击/拖拽应用到网络，Markdown 文档，数据导入/导出 |
| About HouTools | `HouTools → About HouTools` | 用默认浏览器打开离线使用手册（`docs/about.html` + 本地截图，零外部网络资源）：全部工具的功能与用法 |

### Automation

以 Python Panel 在 Houdini 浮动面板中打开。

- QThread 后台执行；配置 JSON 存 `$HIP/HouTools_cfg/Automation_json/`，执行日志存
  `$HIP/HouTools_cfg/Automation_logs/`
- DW 软件路径在 `settings/Automation_Config.json` 中配置（gitignored 运行时数据，缺失时打开面板自动按默认值补建，可直接编辑）
- 节点可直接拖入参数路径框（原生投递、无视窗泄漏）
- `Reload Modules (Dev)` 不会自动重建已打开的面板（避免丢弃未 Start 保存的编辑），
  需点面板工具条自带的刷新按钮重建界面

### 粘贴为 Object Merge

网络编辑器里 Ctrl+C 复制节点后，切到目标网络按 `Ctrl+Shift+V`（或点网络
编辑器的 `HouTools` 菜单），在鼠标位置创建引用节点，颜色随源节点。键位在
主菜单 `HouTools → Paste Hotkey Settings` 中修改（捕获式输入，保存到
`settings/hotkeys.json`，每次启动自动应用）；也可在 Houdini 自带
Hotkey Manager 中修改，符号为 `h.pane.wsheet.houtools_paste_as_object_merge`。

| 复制的源 → 粘贴目标 | 创建的节点（XXX = 源节点名） |
|------|------|
| SOP → SOP | object_merge `Merge_XXX` |
| SOP → OBJ | geo `XXX`，内含 object_merge `Merge_XXX` |
| SOP → LOP | sopimport `SOP_XXX` |
| SOP → COP | sopimport `SOP_XXX`（仅新 COP，需置 usesoppath=1） |
| SOP → DOP | staticobject `Object_XXX` |
| SOP → ROP | fetch `SOP_XXX`（源为 File Cache 时路径追加 `/render`） |
| OBJ → SOP | object_merge `Merge_XXX` |
| OBJ → LOP | sopimport `SOP_XXX` |
| LOP → SOP | lopimport `LOP_XXX` |
| LOP → LOP | fetch `LOP_XXX` |
| LOP → ROP | usdrender `XXX` |

未列出的组合一律跳过；路径参数细则见 `houtools/tools/paste_as_object_merge.py` 模块 docstring。
源路径取 OS 剪贴板文本（引用语义必须有"原件路径"），文本失效时退回内部剪贴板粘贴副本兜底
（仅 SOP 网络）。整次操作占用单个 undo 槽。

ffmpeg 说明：优先使用 Houdini 自带的 `$HFS/bin/hffmpeg`，**无需单独安装**；如需指定版本，
把 `ffmpeg.exe` 放到项目根目录即可（已被 `.gitignore` 排除，不入库）。

### 拖放导入 Alembic

从资源管理器把 `.abc` 文件拖到网络编辑器，松手即在鼠标位置创建导入节点
（基于 Houdini 官方 `externaldragdrop` 钩子；obj 层级等价
File > Import > Alembic Scene...）：

| 拖放落点 | 创建的节点（名称取文件名主干） |
|------|------|
| Object 层级（`/obj`） | Alembic Archive，自动构建层级 |
| SOP 层级（geo 内部） | alembic SOP 节点 |

- 支持一次拖入多个文件，纵向排开；整批一个 undo 槽
- 节点名取文件名主干，中文等非法字符自动替换为 `_`，同名自动加数字后缀
- 非 `.abc` 文件，以及拖到参数框、视口等其他面板的行为与原生完全一致，不受影响
- 拖放钩子文件是 `scripts/externaldragdrop.py`（两行分发器），逻辑在
  `houtools/dragdrop.py`，改代码走 `Reload Modules (Dev)` 热加载，无需重启

### Hdr Library

HDR 环境贴图库浏览器（独立窗口，进任务栏）。库按"总目录 / 一级分类子文件夹"组织，
第一级子文件夹即分类，左侧栏切换 全部 / ★ 收藏 / 各分类（侧栏右键可新建/打开分类文件夹，
空分类也列出，嵌套子文件夹归入第一级）。

- **库目录**：窗口内"更换目录"指定（未配置时顶部路径显示为空、列表空置；
  默认 `~/HouTools/hdri` 或环境变量 `HDR_TOOL_LIB` 仅作选择对话框的起始位置）；
  目录、缩略图大小、全局置顶、收藏都存 `settings/hdr_library.json`（随机器各自保存）
- **缩略图**：后台线程池调 Houdini 自带 `$HFS/bin/hoiiotool` 按需生成（无需安装），
  缓存在库目录 `.thumb_cache/`；打开窗口不自动扫描，点「刷新」才比对缓存目录
  补缺失缩略图（底部有进度条，可暂停/停止），之后秒开；HDR 文件更新后旧缩略图自动失效重生成
- **贴图**：选中一个或多个灯光后**双击缩略图**，把 HDR 路径批量写入灯光环境贴图参数——
  支持 OBJ `envlight`（Karma，自动关 skymap 程序化天空）、RenderMan dome 灯、
  LOP `domelight` 全家族（Solaris）；其余灯型在状态栏给出友好提示
- **右键缩略图**：收藏 / 复制路径 / 打开所在文件夹；收藏项缩略图右上角带角标并计入侧栏计数
- 窗口可全局置顶；大小滑条决定缩略图尺寸，网格固定尺寸不随面板宽拉伸，面板宽窄只改变每列数量

### Recipe Library

Houdini 22 官方 recipes 资产库面板（独立窗口，进任务栏）。**只加载用户库**：在「设置 ▸ 库目录」
配置若干文件夹（递归扫描 `.hda`），出厂库 / Labs / 其他偏好目录一律不显示；库启动时自动安装，
Tab 菜单里的 session tool 从会话开始可用。recipe 的创建走 Houdini 官方保存流程，面板纯展示。

- **浏览**：卡片网格显示缩略图、标签、版本、节点数，卡片支持自定义主题色与收藏角标；
  树形侧栏按 全部 / 收藏 / 节点参数 / 子菜单 / 标签 / 网络层级 过滤；搜索框支持
  `#` 前缀只搜标签、多词 AND 匹配；预览缩略图点击放大查看（滚轮缩放、中键平移）
- **应用**：双击卡片 = 官方工具架体验（参数照抄，视角不拉远），拖拽到网络 = 在落点对齐创建；
  应用前做网络层级匹配检查（如 SOP recipe 进 LOP 网络会给出中文提示）
- **元数据**：自定义显示名（支持中文）、标签、颜色、收藏存 `settings/recipelib.json`，
  缩略图存 `settings/recipe_thumbs/`（相对路径存储，项目挪动不失效）；无文档时预览回退官方备注
- **文档**：每条 recipe 可配 Markdown 文档（存 `settings/recipe_docs/`），确认/取消模式编辑，
  渲染支持 GIF 动图与视频播放
- **数据交换**：「设置 ▸ 数据」导出/导入 zip 交换包（元数据 + 缩略图 + 文档 + HDA），
  打包带走、分享；导入按内部名合并，与本地同名定义的 HDA 整文件跳过

## 新增一个工具

1. 新建 `python3.13libs/houtools/tools/<tool_id>.py`，暴露 `run()` 入口；
2. 在两份菜单 XML 里各加一个 `scriptItem`（`MainMenuCommon.xml` 顶部菜单 /
   `NetworkViewMenu.xml` 网络编辑器菜单，按需取舍，惯例两份都加），
   `scriptCode` 只写两行薄分发器：

```xml
<scriptItem id="houtools.<tool_id>">
  <label>Tool Label</label>
  <scriptCode><![CDATA[
import houtools.dev.dispatcher as _houtools_dispatcher
_houtools_dispatcher.run("<tool_id>")
  ]]></scriptCode>
</scriptItem>
```

3. 重启 Houdini 让菜单项出现（仅此一次），之后该工具的代码迭代全部走热加载；
4. 同步文档：更新本 README 的「工具列表」与「结构」，并在 `AGENTS.md` 的 Where to Look 表补一行。

## 约定

- **窗口**：所有 PySide6 顶层窗口必须经 `houtools.ui.window_manager.open_window(tool_id, factory)`
  创建（单例 + 登记），否则 Reload 无法自动关闭旧窗口，会残留旧代码引用。
- **任务栏常驻**：独立工具窗口（videoseq / Hdr Library）构造时调
  `houtools.ui.taskbar.apply_appwindow_flags(self)`；不要用进程级 AppUserModelID
  （会连 Houdini 主窗的任务栏分组一起改）。
- **日志**：`from houtools.core.log import get_logger`，`get_logger("tools.xxx")`。
- **设置**：`houtools.core.settings.JsonStore("xxx.json", defaults={...})`，存到项目 `settings/` 目录。
- **线程**：QThread + Signal；回改 Houdini 的调用经 `hdefereval.executeDeferred` 派发主线程。
- **`import hou` 只放函数内**（或 try/except 包裹）：保证模块在 Houdini 外可导入，
  无头冒烟测试依赖这一点。
- **菜单栏标签一律英文/ASCII**：H22 菜单栏对中文字符渲染不可靠；工具窗口内部的中文 UI 不受影响。
- **二进制不入库**：`*.exe` 已被 `.gitignore` 排除。

## 测试

```
"C:\Program Files\Side Effects Software\Houdini 22.0.429\python313\python.exe" tests\smoke_test.py
```

（按本机 Houdini 安装位置调整路径，须用 Houdini 自带的 Python 3.13。）
无头验证菜单 XML、全包导入、各窗口实例化（Automation / 视频转序列图 / Hdr Library / Recipe Library）、
拖放导入的 .abc 过滤与节点名清洗，以及 `reload_all()`。

## 结构

```
HouTools/
├── MainMenuCommon.xml             # 顶部菜单（Houdini 规定文件名）
├── NetworkViewMenu.xml            # 网络编辑器面板菜单栏（HouTools 顶层菜单，注入机制同上）
├── HouTools.json                # 包清单副本（生效的一份在 packages/ 下）
├── scripts/externaldragdrop.py  # 官方拖放钩子分发器（拖 .abc 导入，逻辑在 houtools/dragdrop.py）
├── docs/                         # 离线使用手册（About HouTools 菜单用默认浏览器打开，零外部网络资源）
│   ├── about.html                # 手册页面（样式/轮播 JS 内联，轮播含工具截图）
│   └── about_shots/              # 轮播工具截图（tests/gen_about_shots.py 离屏生成，窗口 UI 变更后重跑）
├── python_panels/Automation.pypanel  # Automation 的 Python Panel 界面
├── python3.13libs/
│   ├── uiready.py                 # UI 启动钩子：装默认键位（会话启动时执行）
│   └── houtools/
│       ├── dev/                   # 热加载框架（reloader / dispatcher，不参与重载）
│       ├── core/                  # 路径常量 / 日志 / JSON 设置
│       ├── ui/                    # window_manager 窗口登记 + taskbar 任务栏常驻 + 字体/对话框/侧栏/角标等共享组件
│       ├── automation/            # Automation（任务类型/持久化/执行引擎/窗口）
│       ├── dragdrop.py            # 外部拖放导入 .abc（obj 层级 Alembic Archive / SOP 层级 alembic）
│       ├── videoseq/              # 视频转序列图（ffmpeg 查找 + 窗口）
│       ├── hdrlight/              # Hdr Library（HDR 库浏览 + 缩略图 + 灯光赋值）
│       ├── recipelib/             # Recipe Library（资产库浏览/元数据/文档/数据导入导出/缩略图裁剪）
│       ├── icons/                 # UI 图标（SVG）
│       ├── fonts/                 # 工具统一显示字体（阿里妈妈数黑体 Bold）
│       └── tools/                 # 工具入口（<tool_id>.py 暴露 run()）
├── tests/smoke_test.py
└── settings/                      # 运行时生成（gitignored）
    ├── Automation_Config.json     # Automation 的 DW 软件路径等配置（缺失时自动补建）
    ├── hotkeys.json               # Paste Hotkey Settings 自定义键位
    ├── hdr_library.json           # Hdr Library 的库目录/缩略图大小/收藏
    ├── recipelib.json             # Recipe Library 的库目录与元数据（标签/收藏/颜色/显示名）
    ├── recipe_thumbs/             # Recipe Library 缩略图
    └── recipe_docs/               # Recipe Library Markdown 文档
```

## 许可

本项目以 [GPL-3.0](LICENSE) 协议开源，Copyright (c) 2026 mahx_vfx。
任何基于本项目的二次分发须同样以 GPL-3.0 开源并保留版权声明。

> 本项目为个人开发的第三方工具，与 SideFX 无关、非官方产品；Houdini 及相关商标归
> SideFX Software 所有。运行本项目需要使用者自行持有合法的 Houdini 授权。

## Stargazers over time

![Stargazers over time](https://gitcode.com/mahx-vfx/HouTools/starcharts.svg?variant=adaptive)