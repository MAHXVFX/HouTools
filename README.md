# MAHX_Tools (Houdini 22 / Python 3.13)

从零构建的 Houdini 22 插件工具集，开发期支持**手动热加载**：改完代码无需重启 Houdini。
完整规划与环境核实结论见 [PLAN.md](PLAN.md)。

## 安装

本机已配置完毕：`Documents/houdini22.0/packages/MAHX_Tools.json` 已就位，项目根目录已通过
`HOUDINI_PATH` 挂载。启动 Houdini 即可在顶部菜单看到 **MAHX Tools**。

分发到其他机器时：拷贝本文件夹，再把项目根目录下的 `MAHX_Tools.json` 放入目标环境
`Documents/houdiniXX.X/packages/`（纯 Python 插件无需编译）。

## 开发工作流

| 改动 | 生效方式 |
|------|----------|
| `mahx/` 下任意工具/核心/UI 代码 | 菜单 `MAHX Tools → Reload Modules (Dev)`，立即生效 |
| 已打开的工具窗口 | Reload 时自动关闭，重新打开即新代码 |
| `python_panels/*.pypanel`（如新增） | 面板自带 Reload 按钮 |
| `MainMenuCommon.xml` 菜单结构 | **重启 Houdini**（H22 硬约束，无法运行时重载） |
| `mahx/dev/` 热加载框架自身 | 重启 Houdini（刻意不参与重载） |

验收闭环：修改任意 `mahx/` 模块 → 保存 → 菜单点 Reload → 重新打开对应工具，改动即生效
（已打开的工具窗口会在 Reload 时自动关闭）。

## 工具列表

| 工具 | 菜单项 | 说明 |
|------|--------|------|
| MA Automation | `MAHX Tools → MA Automation` | 自动化批处理：节点按钮点击 / Flipbook 拍屏 / HomeAssistant Webhook；QThread 后台执行，配置 JSON 按 `$HIP` 存储 |
| 视频转序列图 | `MAHX Tools → Video to Sequence` | ffmpeg 提取视频为 JPG 序列（帧级进度、质量/起始帧/位数/前缀可调），可选自动设置相机 Background Image |

> 菜单栏标签一律用英文：H22 菜单栏对中文字符渲染不可靠；工具窗口内部的中文 UI 不受影响。

ffmpeg 说明：优先使用 Houdini 自带的 `$HFS/bin/hffmpeg`，**无需单独安装**；如需指定版本，
把 `ffmpeg.exe` 放到项目根目录即可（已被 `.gitignore` 排除，不入库）。

## 新增一个工具

1. 新建 `python3.13libs/mahx/tools/<tool_id>.py`，暴露 `run()` 入口；
2. 在 `MainMenuCommon.xml` 里加一个 `scriptItem`，`scriptCode` 只写两行薄分发器：

```xml
<scriptItem id="mahx.<tool_id>">
  <label>Tool Label</label>
  <scriptCode><![CDATA[
import mahx.dev.dispatcher as _mahx_dispatcher
_mahx_dispatcher.run("<tool_id>")
  ]]></scriptCode>
</scriptItem>
```

3. 重启 Houdini 让菜单项出现（仅此一次），之后该工具的代码迭代全部走热加载。

## 约定

- **窗口**：所有 PySide6 顶层窗口必须经 `mahx.ui.window_manager.open_window(tool_id, factory)`
  创建（单例 + 登记），否则 Reload 无法自动关闭旧窗口，会残留旧代码引用。
- **日志**：`from mahx.core.log import get_logger`，`get_logger("tools.xxx")`。
- **设置**：`mahx.core.settings.JsonStore("xxx.json", defaults={...})`，存到项目 `settings/` 目录。
- **线程**：QThread + Signal；回改 Houdini 的调用经 `hdefereval.executeDeferred` 派发主线程。

## 测试

```
"C:\Program Files\Side Effects Software\Houdini 22.0.429\python313\python.exe" tests\smoke_test.py
```

无头验证菜单 XML、全包导入与 `reload_all()`。

## 结构

```
MAHX_Tools/
├── MainMenuCommon.xml             # 顶部菜单（Houdini 规定文件名）
├── MAHX_Tools.json                # 包清单副本（生效的一份在 packages/ 下）
├── python3.13libs/mahx/
│   ├── dev/                       # 热加载框架（reloader / dispatcher，不参与重载）
│   ├── core/                      # 路径常量 / 日志 / JSON 设置
│   ├── ui/                        # window_manager 窗口登记
│   ├── automation/                # MA Automation（任务类型/持久化/执行引擎/窗口）
│   ├── videoseq/                  # 视频转序列图（ffmpeg 查找 + 窗口）
│   ├── icons/                     # UI 图标（SVG）
│   └── tools/                     # 工具入口（<tool_id>.py 暴露 run()）
├── tests/smoke_test.py
├── PLAN.md                        # 完整规划
└── settings/                      # 运行时生成
```
