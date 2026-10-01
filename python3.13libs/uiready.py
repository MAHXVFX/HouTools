# MAHX_Tools UI 启动钩子
# ======================
# Houdini 对路径上每个 pythonX.Ylibs/uiready.py 都会执行（UI 就绪后，
# 仅交互会话）。这里安装 MAHX 菜单项的默认键位 —— 必须等菜单 XML 注册
# 完热键符号，所以放 uiready 而不是 ready.py / 123.py。
#
# 注意：HFS 自带同名钩子（窗口预热等）。若 Houdini 的加载方式是"导入
# 首个匹配模块"，本文件会遮蔽官方那份 —— 因此把路径上后续的 uiready.py
# 链式执行一遍；若加载方式是"路径扫描全部执行"，官方文件会被重复执行，
# 其内容（预热 + prefs 刷新）幂等，无害。

import sys
from pathlib import Path

try:
    import mahx.core.hotkeys
    mahx.core.hotkeys.install_defaults()
except Exception as e:
    print(f"[MAHX] uiready 安装默认键位失败: {e}")


def _run_downstream_uiready():
    """执行路径上下一个 python3.13libs/uiready.py（通常是 $HH 官方那份）。"""
    try:
        ours = Path(__file__).resolve()
    except NameError:
        return
    for entry in sys.path:
        if not entry:
            continue
        candidate = Path(entry) / "python3.13libs" / "uiready.py"
        try:
            if candidate.is_file() and candidate.resolve() != ours:
                code = compile(candidate.read_text(encoding="utf-8"),
                               str(candidate), "exec")
                exec(code, {"__name__": "_mahx_downstream_uiready",
                            "__file__": str(candidate)})
                return
        except OSError:
            continue


_run_downstream_uiready()
