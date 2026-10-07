# HouTools UI 启动钩子
# ======================
# Houdini 对路径上每个 pythonX.Ylibs/uiready.py 都会执行（UI 就绪后，
# 仅交互会话）。这里安装 HouTools 菜单项的默认键位 —— 必须等菜单 XML 注册
# 完热键符号，所以放 uiready 而不是 ready.py / 123.py。
#
# 注意：HFS 自带同名钩子（窗口预热等）。若 Houdini 的加载方式是"导入
# 首个匹配模块"，本文件会遮蔽官方那份 —— 因此把路径上后续的 uiready.py
# 链式执行一遍；若加载方式是"路径扫描全部执行"，官方文件会被重复执行，
# 其内容（预热 + prefs 刷新）幂等，无害。

import sys
from pathlib import Path

try:
    import houtools.core.hotkeys
    houtools.core.hotkeys.install_defaults()
except Exception as e:
    print(f"[HouTools] uiready 安装默认键位失败: {e}")

# Recipe Library 的用户库（settings/recipelib.json 的 lib_dirs）在 UI 启动
# 时即安装：面板打开前 Tab 菜单里就能出现这些 recipe 的 session tool。
# 未配置库文件夹时静默跳过；失败只打印不阻塞启动。
try:
    from houtools.recipelib import metadata as _rl_meta
    from houtools.recipelib import store as _rl_store
    _rl_dirs = _rl_meta.get_lib_dirs()
    if _rl_dirs:
        _rl_store.ensure_libraries_installed(_rl_dirs)
except Exception as e:
    print(f"[HouTools] uiready 安装 recipe 库失败: {e}")


def _run_downstream_uiready():
    """执行路径上其他 python3.13libs/uiready.py（通常是 $HH 官方那份）。

    sys.path 上放的是各个 python3.13libs 目录本身（houtools 能被导入即证），
    官方文件即 ``<entry>/uiready.py``；同时兼容 entry 为包根的挂载方式。
    Houdini 只导入首个匹配的 uiready 模块（即本文件），路径上其余同名
    钩子都会被遮蔽 —— 因此逐个执行全部下游，而不是只补第一个；单个
    下游失败不阻塞其他下游与 HouTools 自身启动。
    """
    try:
        ours = Path(__file__).resolve()
    except NameError:
        return
    seen = {ours}
    for entry in sys.path:
        if not entry:
            continue
        root = Path(entry)
        for candidate in (root / "uiready.py",
                          root / "python3.13libs" / "uiready.py"):
            if not candidate.is_file():
                continue
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                code = compile(candidate.read_text(encoding="utf-8"),
                               str(candidate), "exec")
                exec(code, {"__name__": "_houtools_downstream_uiready",
                            "__file__": str(candidate)})
            except Exception as e:
                print(f"[HouTools] 下游 uiready 执行失败 {candidate}: {e}")


_run_downstream_uiready()
