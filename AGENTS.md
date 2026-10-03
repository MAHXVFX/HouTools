# HouTools Knowledge Base

**Houdini 22.0 (Python 3.13 / PySide6 6.8.3) 插件工具集** — 包机制加载 + 开发期手动热加载。
环境事实：Houdini 22.0.429；`requests` 内置；`hffmpeg/hffprobe` 随 Houdini 附带（`$HFS/bin`）。

## Structure

```
root/
├── MainMenuCommon.xml             # Houdini 顶部菜单（薄分发器 scriptItem）
├── NetworkViewMenu.xml            # 网络编辑器面板菜单栏（HouTools 顶层菜单，注入机制同主菜单）
├── HouTools.json                # 包清单副本（生效的一份在 Documents/houdini22.0/packages/）
├── python_panels/Automation.pypanel  # Automation 的 Python Panel 界面定义
├── python3.13libs/uiready.py      # UI 启动钩子：装默认键位；会链式执行路径上后续 uiready.py
├── python3.13libs/houtools/           # 核心 Python 包（python3.13libs 由 Houdini 自动加入 sys.path）
│   ├── dev/                       # ★ 热加载框架：reloader / dispatcher（永不参与重载）
│   ├── core/                      # constants（路径自算）/ log / settings(JsonStore)
│   ├── ui/                        # window_manager（工具窗口单例登记，Reload 前统一关闭）/ taskbar（Win32 任务栏常驻）
│   ├── automation/                # Automation：task_types / data_manager / execution_engine / styles / window
│   ├── videoseq/                  # 视频转序列图：ffmpeg 查找 + 窗口（拖放 + 可编辑路径框）
│   ├── hdrlight/                  # Hdr Library：HDR 库浏览 + 缩略图 + 双击赋给灯光
│   ├── recipelib/                 # Recipe Library：官方 recipes 资产库（网格浏览/标签/收藏/Markdown 文档/拖入网络创建）
│   ├── icons/                     # UI 图标（SVG，文件名不含空格）
│   └── tools/                     # 工具入口：<tool_id>.py 暴露 run()
├── tests/smoke_test.py            # 无头回归：菜单 XML / 全包导入 / reload_all / TaskItem 往返 / 各窗口实例化（Automation/视频/HDR）
└── settings/                      # 运行时生成的用户设置（gitignored）
```

## Where to Look

| Task | Location | Notes |
|------|----------|-------|
| 热加载机制 | `houtools/dev/reloader.py` | 按 sys.modules 插入序重载（=依赖序）；跳过 `houtools.dev*`；失败模块弹出 sys.modules 供下次重导 |
| 菜单点击入口 | `houtools/dev/dispatcher.py` | `run(tool_id)` → `importlib.import_module("houtools.tools." + tool_id)` → 调其 `run()` |
| 菜单定义 | `MainMenuCommon.xml` / `NetworkViewMenu.xml` | 顶层子菜单 id：`houtools_tools_menu` / `houtools_network_view_menu`；均插在 help_menu 前 |
| 新增工具 | `houtools/tools/<tool_id>.py` + 两份菜单 XML | 模块暴露 `run()`；菜单 scriptCode 只写两行分发器 |
| 默认热键 | `houtools/core/hotkeys.py`（清单+自定义存储）+ `python3.13libs/uiready.py`（执行） | 菜单 item id 须为 `pane.wsheet.<name>` 前缀，热键符号才是 `h.pane.wsheet.<name>`；`settings/hotkeys.json` 的自定义键每次启动强制应用，其次尊重 Hotkey Manager 已有键位，最后落默认值 |
| 粘贴为 Object Merge | `houtools/tools/paste_as_object_merge.py` | 网络编辑器菜单入口 / `Ctrl+Shift+V`（`Paste Hotkey Settings` 可改）；按"目标上下文×源类别"建引用（XXX=源名，颜色随源）：SOP→SOP object_merge(Merge_XXX)；SOP→OBJ geo(XXX) 内 object_merge(Merge_XXX)；SOP→LOP / OBJ→LOP sopimport(SOP_XXX)；LOP→SOP lopimport(LOP_XXX)；LOP→LOP fetch(LOP_XXX)；LOP→ROP usdrender(XXX)；SOP→ROP fetch(SOP_XXX, source；源为 File Cache 时路径追加 /render)；SOP→DOP staticobject(Object_XXX)；SOP→COP(新COP) sopimport(SOP_XXX, usesoppath=1)；未列组合跳过；单 undo 槽。源路径靠 OS 剪贴板文本（内部剪贴板无法反查原件），SOP 网络文本失效时 `pasteItemsFromClipboard` 粘贴副本兜底 |
| 快捷键设置 | `houtools/tools/paste_hotkey_settings.py` | 捕获式改键 → `hou.hotkeys.addAssignment` 会话即时生效 + `settings/hotkeys.json` 持久化；经 `window_manager` 单例 |
| 窗口单例 | `houtools/ui/window_manager.py` | `open_window(tool_id, factory)`；close_all 供 Reload 前调用 |
| 任务栏常驻 | `houtools/ui/taskbar.py` | `apply_appwindow_flags(window)`：Win32 `WS_EX_APPWINDOW`（Get+OR，保留原扩展样式位），失败走 debug 日志；独立工具窗口（videoseq / Hdr Library）构造时调用 |
| 项目路径 | `houtools/core/constants.py` | `PROJECT_ROOT = Path(__file__).parents[3]`，不依赖 cwd |
| 日志 | `houtools/core/log.py` | `get_logger("tools.xxx")`；handler 全局只配置一次，重载安全 |
| JSON 设置 | `houtools/core/settings.py` | `JsonStore(filename, defaults)`，defaults 合并语义，存项目 `settings/` |
| 自动化任务模型 | `houtools/automation/task_types.py` | TaskType 枚举 + dataclass 参数 + TaskItem.to_dict/from_dict |
| 自动化持久化 | `houtools/automation/data_manager.py` | 按 `$HIP/HouTools_cfg/Automation_json/` 存取（跟场景走，是有意设计）；多配置文件 |
| 自动化执行引擎 | `houtools/automation/execution_engine.py` | QThread；Houdini API 经 `hdefereval.executeDeferred` + Event 同步派发主线程；**无超时同步等待是有意设计、禁止加超时**（原因与取消语义见 Conventions「Automation 的同步等待」）；`dl_Submit` 特例：点击前强制 `hipFile.save()`，失败则跳过点击 |
| 界面接入（Python Panel） | `houtools/automation/window.py` | `create_panel_widget` / `open_floating_panel`（`hou.pypanel.installFile` + `createFloatingPanel`）；节点拖放为 Houdini 原生投递（MIME text 是逗号分隔的节点路径） |
| ffmpeg 查找 | `houtools/videoseq/ffmpeg.py` | 优先级：项目根 `ffmpeg.exe` → `$HFS/bin/hffmpeg` → `$HFS/bin/ffmpeg` → PATH（hffmpeg 优先） |
| 视频拖放/路径框 | `houtools/videoseq/window.py` | 整窗 + `_VideoSourceGroup` 双层接收拖放；`_load_video` 是浏览/拖放/手输共用入口；提取取消为协作式：探测阶段（ffprobe 30s/ffmpeg 回退 120s 超时，期间点停止 ffmpeg 不得启动）与 Popen 前各查一次 `_cancelled`，收尾 terminate+`wait(15s)`+kill 兜底——无人读 stdout 时 ffmpeg 会卡死在 progress 管道写上（缓冲区约 4KB 填满即停），裸 `wait()` 会永久挂起线程，而窗口 closeEvent 靠轮询 `isRunning()` 延迟关闭，线程挂起=隐藏的窗口永不关闭 |
| Recipe Library（recipes 资产库） | `houtools/recipelib/`（store/metadata/docs/browser）+ `tools/recipe_library.py` | 基于 H22 官方 recipes 的资产面板，**只加载用户库**：库 = 面板「库目录...」配置的若干文件夹（`settings/recipelib.json` 的 `lib_dirs`，可多个，递归扫 `.hda`），出厂（$HFS）/Labs/偏好目录默认库一律不显示；`uiready.py` 启动即装库（Tab 菜单里的 session tool 从会话开始可用）。**坑：`saveToolRecipe` 每次保存都在 `<库文件夹>/backup/` 写 `HouToolsRecipes_bakN.hda`**，扫描必须剪掉 backup 目录——装进备份会让已删 recipe"复活"并与现行定义同名冲突。「新建」落到第一个库文件夹的 `HouToolsRecipes.hda`（saveToolRecipe 自动建文件并安装）。**删除语义（无头实测）**：`HDADefinition.destroy()` 删掉文件里最后一个定义时 Houdini 自动卸载该库、`definitionsInFile` 对未加载文件抛 OperationFailed、磁盘留 83B 空壳——删后按"读不到定义（含 OperationFailed）= 已空"处理：卸载+删空壳；有幸存定义则文件保持加载、内容完好。扫描安装的防护：装不上且 <512B 的文件清掉（空壳自愈）、装不上的大文件保留+告警、误放进来的非 recipe .hda（无 data 类定义）装入后立即卸载还原（不删文件，实测验证）。枚举/元信息走公开 API：`recipeutils.recipeNames(RecipeCategory.xxx)` + `hou.nodeType(hou.dataNodeTypeCategory(), name).definition()` 直读 `data.recipe.json` section（不依赖内部包 hrecipes）；**label/submenu 的实际存储随 recipe_category 不同**（22.0.429 实测，与 recipe_format 文档页有出入）——tab_tool/preset 类在 `properties.label/submenu`，shelf 式 tool 在 `tool.tool_labels[]`/`tool.tab_submenus[]`，读不到时回退内部名。应用按类型分发：Tool=官方 click_to_place 点击放置流程（参数模板照抄 hrecipes 的 _tab_tool_script：pane/tool_inputs/drop_on_wire/skip_notes 等）；node/parm/decoration 预设需先选中目标节点。**拖拽入网是自研方案**（不依赖网络编辑器接受任何 MIME）：拖动期间装 QApplication 级事件过滤器吃 MouseMove/Release/Esc，松手用 `hou.ui.paneTabUnderCursor()` 判落点、`editor.cursorPosition()` 取网络坐标，apply（click_to_place=False）后按返回 `items` 里 `tags.target_tag` 锚点把整组节点平移对齐（官方不保证该模式落点是鼠标点，实测对齐精确）。元数据（标签/收藏/缩略图路径）存 `settings/recipelib.json`、缩略图文件复制进 `settings/recipe_thumbs/`（有意不写 HDA section：元数据与库文件解耦）；Markdown 文档存 `settings/recipe_docs/<safe名>/doc.md + assets/`（插入图片/视频复制进 assets 用相对引用，同名追加序号）。渲染 `QTextBrowser.setMarkdown`（**QtWebEngine 在 Houdini 内不可初始化**——Qt6 要求先于 QApplication，已实测；表格/图片/中文正常，代码块无高亮）；GIF：QImageWriter/hffmpeg 均无 GIF **编码器**（只能读），网格取首帧、预览区 QMovie、文档流后处理把 `![]()` 字符替换成 QMovie 内联控件；视频 `![x](a.mp4)` 替换成按钮弹 QMediaPlayer——**必须先 setVideoOutput 再 play 否则 InvalidMedia**（实测踩过）。窗口 `__init__` 零 hou 依赖（smoke test 直接实例化+打桩 list_recipes），首次 showEvent 才枚举 |
| HDR 库浏览器 | `houtools/hdrlight/browser.py` + `tools/hdr_library.py` | 菜单 Hdr Library；递归扫描 `.hdr/.hdri/.exr`，第一级子文件夹即分类（左侧侧栏切换 全部/收藏/各分类，侧栏右键可新建/打开分类文件夹，空分类也列出）；右键收藏 HDR（存 `settings/hdr_library.json` 的 `favorites`，normcase 路径），侧栏"★ 收藏"过滤；双击缩略图把路径赋给选中的灯光（可多选批量赋同一 HDR，左上角显示单个路径或选中数量（hou.ui.addSelectionCallback 推送驱动，无常驻轮询，closeEvent 注销回调））——支持 OBJ `envlight`（`env_map`，自动关 `skymap_enable` 程序化天空）、RenderMan dome 灯（`env_map`/`rman__EnvMap`）、LOP `domelight` 全家族（USD 输入 `inputs:texture:file`，参数名被 punycode 化成 `xn__inputstexturefile_*`，按"名字含 texturefile"特征匹配，`_control` 作者状态参数须为 `'set'` 才写入 stage；已实测 DomeLight_1 prim 的 `inputs:texture:file` 成功 authored）；灯光过滤器（`pxr*lightfilter` 等）在 is_light_node 排除，防 `map` 参数误匹配；非 dome 灯型报友好错误；网格列宽自适应铺满面板宽度（滑条只定列数，防抖重算，图标上限 400px；幂等计算防闪烁：输入用列表控件宽度——不随滚动条显隐变化——减系统滚动条宽度，结果未变即跳过；若用视口宽，滚动条显隐会翻转计算结果形成重排死循环）；分类过滤为**重建式**（只添加匹配条目——IconMode+gridSize 下 setHidden 的条目不绘制但仍占网格槽位，分类视图顶部会空一片）；缩略图就绪后 400ms 批量应用图标（每张 setIcon 都触发一次全网格延迟重排，逐张应用=持续闪烁）；缩略图后台 QThread 内部线程池（**无 parent**，Reload 销毁窗口不会连带销毁运行中的线程；并发按核数/4 夹在 2..4 自动定档；底部有进度条 + 暂停/继续/停止，暂停在每张开工前生效；单张异常兜底记 warning 不中止整批；刷新起用新线程池时进度/完成信号按 sender 过滤，旧线程余量不污染新一轮；hoiiotool 子进程等待 1s 粒度可中断，CREATE_NO_WINDOW 防止批量生成时连续弹控制台黑框）调 `$HFS/bin/hoiiotool` 生成（缓存 256px 存库目录 `.thumb_cache/`，按相对路径+扩展名命名避免同名/同名不同扩展冲突，mtime 旧于 HDR 自动失效，先写 `~tmp_<线程id>_*.jpg` 临时文件（线程标识防刷新时新旧两池并发覆写同一临时文件）再原子改名（临时名必须保留 `.jpg` 后缀——OIIO 靠扩展名选输出写入器，`.part` 之类未知后缀会让 hoiiotool 直接失败；坑：`--resize` 必须写全 WxH，`--colorconvert` 需 OCIO 配置、缺失时自动注入 `$HFS/packages/ocio/`））；库目录存 `settings/hdr_library.json`（未配置时顶部路径显示为空、打开不自动扫描，点「刷新」才扫描+补缩略图；默认 `~/HouTools/hdri`/`HDR_TOOL_LIB` 仅作「更换目录」对话框起始位置） |

## Conventions

- **新增工具三步**：`houtools/tools/<tool_id>.py`（暴露 `run()`）→ 两份菜单 XML 各加 `scriptItem`（scriptCode 仅 `import houtools.dev.dispatcher` + `_houtools_dispatcher.run("<tool_id>")` 两行）→ 重启 Houdini 一次让菜单出现；此后该工具代码迭代全部走 Reload 热加载。
- **菜单栏标签一律英文/ASCII**（H22 菜单栏对中文渲染不可靠）；工具窗口内部 UI 可用中文。
- **顶层窗口与 Python Panel**：独立 QDialog 必须经 `window_manager.open_window()` 创建（如视频转序列图），否则 Reload 无法自动关闭旧窗口；Automation 是 Python Panel 部件（pane 托管），**不得**注册进 window_manager。
- **Reload 与 pypanel**：Reload Modules 不重建已打开的 Python Panel（避免丢弃未 Start 保存的编辑），由用户点面板工具条自带的刷新按钮重建界面。
- **`import hou` 只放函数内**或 try/except，保证模块在 Houdini 外可导入（冒烟测试依赖这一点）。
- **Houdini MCP（fxhoudini）使用协议**：需要连 GUI 会话前，先向用户说明本次要在 Houdini 里做什么，等用户手动打开 MCP 再动；GUI 内探针一律小步拆分、先无头（hython + 隔离 `HOUDINI_USER_PREF_DIR`）验证，把会话里长/重的操作（HDA destroy、全量 rescan 等）放在无头环境做——曾有 destroy 链路把 GUI 会话搞崩的先例。测试用节点/配方用完即清，用户设置（lib_dirs 等）测前测后保持一致。
- **线程**：QThread + Signal；任何回改 Houdini 的调用经 `hdefereval.executeDeferred` 派发主线程（参考 `execution_engine._run_deferred`）。
- **Automation 的同步等待是有意设计，勿"修复"**：引擎对主线程调用做**无超时**阻塞等待（`execution_engine._run_deferred` 的 `ready.wait()`）。本工具本质是逐个触发 Houdini 按钮，顺序执行的唯一保证是"上一任务的主线程调用返回后再派发下一个"——主线程被解算/缓存阻塞多久就等多久（数小时是正常值），加超时会在任务实际完成前放行下一任务、破坏顺序语义；模态对话框等阻塞在本语义下同样属于"当前任务未完成，继续等"。取消是协作式，仅在任务边界生效、在途任务不可打断；因此 UI（`window._cancel_execution`）**不立即复位**：按钮保持「停止中…」并禁用，直到引擎真正发出 `all_completed` 才回空闲——否则留下"假取消"窗口，期间二次 Start 会被旧引擎迟到的 `all_completed` 打翻新引擎状态。运行中引擎由 `window._LIVE_ENGINES` 模块级留引用（引擎无 parent），防 GC 销毁运行中的 QThread。已知会弹阻塞对话框的按钮逐案消除弹框根源（`dl_Submit` 点击前强制存盘），不改等待机制。
- **菜单结构改动**（增删菜单项）需重启 Houdini——H22 硬约束，`menurefresh` 只覆盖 OPmenu/PARMmenu 等右键菜单。
- **reload 语义**：`houtools/dev` 自身永不重载（改 dev 框架需重启）；重载前自动 close 所有登记窗口；状态反馈走 `hou.ui.setStatusMessage`（`houtools.dev.status`）。
- **二进制不入库**：`*.exe` 已被 .gitignore 排除。
- **提交**：git 仓库在 gitcode（`mahx-vfx/HouTools`，SSH 远程），main 分支；无构建步骤，纯 Python 即发布。

## Anti-Patterns (This Project)

- **模块级 `_window` 单例**：必须用 `window_manager`（它处理已删除 C++ 对象的 RuntimeError 重建）。
- **`houtools/dev` 内 import `houtools.core/ui/tools`**：dev 框架必须独立于被重载代码，否则持旧引用。
- **import 期副作用**：包 `__init__` 保持薄，重活交给 dispatcher 懒加载——否则热加载会重放副作用。
- **静默异常**：禁止无日志的 `except: pass`；捕获后至少 `log.warning`。
- **把业务逻辑写进菜单 scriptCode**：只允许两行分发器；逻辑一律进 `houtools/tools/` 与模块。
- **在 reload 顺序上做假设**：新增模块间依赖时保持"先被 import 的是依赖"，插入序重载才成立；避免模块级循环依赖。
- **给 `execution_engine._run_deferred` 的 `ready.wait()` 加超时**：那是顺序执行语义本身，不是缺陷（见 Conventions「Automation 的同步等待」）；"Stop 按了没立刻停"同理——取消只在任务边界生效，UI 会如实显示「停止中…」。

## Unique Styles

- **暗色主题**：主色 `#18181b`/`#1D1D20`/`#2d2d2d`，强调 `#0d6399`(蓝)/`#8a5cf5`(紫)，错误 `#d1283e`；各模块 styles 常量共享这套配色。
- **配置语义**（Automation）：JSON 只在点 Start 时落盘 = "用户决定执行的任务"，关窗不保存是有意为之。
- **长驻窗口进 Houdini 任务栏**：Win32 `WS_EX_APPWINDOW`，统一走 `houtools/ui/taskbar.py`（Get+OR 保留原样式位；不做进程级 AppUserModelID——那会连 Houdini 主窗的任务栏分组一起改），失败静默。

## Commands

```
# 无头回归测试（不需要打开 Houdini）
"C:\Program Files\Side Effects Software\Houdini 22.0.429\python313\python.exe" tests\smoke_test.py

# 提交推送
git add -A && git commit -m "..." && git push
```

（无构建/打包步骤；热加载验证 = 改代码 → 菜单 Reload Modules (Dev) → 重开工具窗口）
