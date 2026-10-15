---
name: houtools-release
description: HouTools 发版流程（版本号更新、README/About 手册同步、截图重生成、提交打标签推送、打包 HouTools.zip 到系统下载文件夹、产出发行说明代码块）。当用户要求打包发布、发新版本、创建发行版/release、更新版本号、打 git 标签、打 zip 包或写发行说明时使用——即使用户只要了其中一步（如仅打包、仅改版本号、仅要发行说明）。版本号从调用参数读取（如 $houtools-release v1.4.0）；参数缺版本号时先弹选项卡向用户要，不得自行猜测。
---

# HouTools 发版流程

调用方式：`$houtools-release v1.4.0`——版本号取自调用参数（如 `v1.4.0`）。
对外一律带 `v` 前缀，代码内不带。

## 硬约束：版本号必须先确定，否则流程不得开始

- 参数带了版本号 → 直接采用，进入下方流程。
- 参数没带版本号（如只写了 `$houtools-release`）→ **禁止猜测或默认版本号，
  不得执行任何后续步骤**，必须先用 AskUserQuestion 弹选项卡让用户定版本：
  1. 先 `git describe --tags --abbrev=0` 取最新 tag（仓库还没有 tag 时从 v0.1.0 起步）；
  2. 推荐版本 = 最新 tag 次版本号 +1（如最新 v1.4.0 → 推荐 v1.5.0），作为选项卡
     第一个选项并在 label 标注「推荐」；再列补丁位（v1.4.1）与大版本（v2.0.0）两个备选；
  3. 用户要自定义版本号时，走选项卡自带的「Other」自由输入。
- 用户选定/输入之前，不读文件、不跑命令（取最新 tag 除外）。

## 机器无关：一切路径动态解析，禁止写死

换机器（安装盘符、Houdini 小版本、用户目录）都会变，本 skill 及手敲命令一律不含绝对路径：

- **Houdini 自带 python**：每条命令内联解析（shell 状态不跨调用存续）：
  `"$(bash .agents/skills/houtools-release/scripts/find_houdini_python.sh)" <脚本>`
  解析脚本按 `$HFS` → 各盘符 `Program Files/Side Effects Software/Houdini 22.*`
  （取最高版本）探测；失败时如实报告并请用户确认安装位置，禁止硬编码盘符或版本号。
- **gitcode 的 owner/repo**：从 `git remote get-url origin` 解析——SSH 形态
  `git@gitcode.com:<owner>/<repo>.git`、https 形态 `.../gitcode.com/<owner>/<repo>.git`，
  取 `.git` 前的 `<owner>/<repo>` 两段（本仓库当前为 `mahx-vfx`/`HouTools`）。
- **系统下载文件夹**：用户目录可能被重定向（作者机器实测 Documents 与 Downloads
  都不在 C: 默认位置）——一律走 pack_zip.py 内置的 SHGetKnownFolderPath 解析，
  勿假设 `C:\Users\<名>\Downloads`。

## 流程步骤

按以下步骤顺序执行；用户只要了其中一部分时只做对应步骤，但保持顺序语义
（如打包前版本号必须已更新——打包脚本会断言）。

## 1. 更新版本号（全仓库仅两处，改完 grep 验证）

| 位置 | 写法 |
|------|------|
| `python3.13libs/houtools/__init__.py` | `__version__ = "1.4.0"`（无 v） |
| `docs/index.html` 头部横幅 | `<h1>HouTools<span class="v">v1.4.0</span></h1>`（带 v） |

改完用旧版本号 grep 确认无残留：
`grep -rnE "v?旧版本号" --include="*.py" --include="*.html" --include="*.md" .`

## 2. 文档同步（自上一个 tag 以来的改动决定改什么）

- `git describe --tags --abbrev=0` 取上一版 tag，`git log <tag>..HEAD --oneline` 列出改动。
- 新增用户可见功能 → 双处同步：README.md（「工具列表」表格行 + 详细小节）与
  docs/index.html（工具目录 nav + section 小节；网络编辑器类功能加轮播 SVG 示意卡——
  离屏截不了网络编辑器，参照现有 slide 风格：880×320、#241d1c 底、标题栏三色点、
  #2d2d2d 网格线、左下角操作提示文字）。
- 工具窗口 UI 有改动 → 用 Houdini 自带 python 重生成四张轮播截图（offscreen，
  脚本自带打桩演示数据，改的是哪个窗口就全量重跑）：
  `"$(bash .agents/skills/houtools-release/scripts/find_houdini_python.sh)" tests/gen_about_shots.py`
- 跑无头回归，必须全绿再继续：
  `"$(bash .agents/skills/houtools-release/scripts/find_houdini_python.sh)" tests/smoke_test.py`

## 3. 提交 + 打标签 + 推送

提交拆两个（与仓库历史一致）：docs 改动一个 commit（`docs: ...`），版本号一个
（`chore: 更新版本号至X.Y.Z`）。

附注标签的附注消息就是发行说明（历史发行版均如此，gitcode 发行说明即照抄它），
格式见第 6 步模板。标签创建后与 main 一起推送：`git push origin main vX.Y.Z`。

## 4. 打包 HouTools.zip（文件名硬性要求）

用本 skill 附带的脚本（清单/排除/验证都在脚本里，勿手搓命令）：

```
"$(bash .agents/skills/houtools-release/scripts/find_houdini_python.sh)" .agents/skills/houtools-release/scripts/pack_zip.py v1.4.0
```

脚本行为与原因：

- 只打运行必需文件：README.md（进包，供用户对照安装/用法）+ 三份菜单 XML +
  `python3.13libs/` + `python_panels/` + `scripts/` + `docs/`（About 页面用）；
  排除 tests、LICENSE、AGENTS.md、settings/、`__pycache__`。
- zip 顶层恰好两项：`HouTools.json`（包清单，顶层）+ `HouTools/` 文件夹
  （其余全部文件都在里面）。整个 zip 直接解压进 `Documents/houdiniXX.X/packages/`
  即完成安装——清单引用 `$HOUDINI_PACKAGE_PATH/HouTools`，清单与包目录一次落位，
  与 `HouTools.json` 的路径配置（2026-10-15 修正）一致。
- 输出到**系统真实下载文件夹**。本机用户目录重定向过（Documents 在 D:、
  Downloads 实测在 `E:\Users\pc\Downloads`）——脚本经 SHGetKnownFolderPath
  解析，勿假设 `C:\Users\<名>\Downloads`。
- 用 Python zipfile 打包，条目名保证正斜杠；勿用 PowerShell 5.1 的
  Compress-Archive（条目名写反斜杠，部分解压工具打不开）。
- 自带验证：zip 完整性、`v<版本号>` 已带入 index.html、`__init__.py` 版本已更新、
  无缓存/开发文件混入。dry-run 加 `--out <路径>`，避免覆盖真包。

汇报时提一句：按"仅必要文件"原则 LICENSE 不入包，About 页脚 GPL 链接
（`../LICENSE`）在 zip 安装环境下失效——用户已接受此取舍（2026-10-14）。

## 5. 读上一版发行信息（gitcode MCP）

调 `mcp__gitcode__list_tags`，owner/repo 从 `git remote get-url origin` 解析
（见「机器无关」节）。MCP 没有
release 端点——**附注 tag 的 message 就是上一版发行说明**，列表最新一条即上一版；
可对照本地 `git tag -l -n99 <上一版tag>` 核实。

## 6. 给出发行说明（代码块，用户自行创建发行版）

把当前版发行说明放 markdown 代码块给用户复制。**不要**代替用户创建发行版
（MCP 无此能力，用户明确要求自行创建）。收尾提示：附件上传第 4 步的
HouTools.zip，标签选 vX.Y.Z。

模板（与 tag 附注一致）：

```markdown
HouTools vX.Y.Z

<一句话概括本版主题>。

- <模块/工具>: <要点>；<要点>
```

要点从第 2 步 `git log <tag>..HEAD` 的提交提炼，用用户视角措辞（功能效果而非
实现细节），3~6 条；首行概括 + 空行 + 条目列表的骨架不可变。
