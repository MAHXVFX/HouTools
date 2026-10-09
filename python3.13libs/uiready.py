# HouTools UI 启动钩子
# ======================
# Houdini 官方机制（docs: Python script locations）：Houdini 会执行 Houdini
# 路径上**所有** pythonX.Ylibs/uiready.py（UI 就绪后，仅交互会话）——各包
# 同名钩子互不遮蔽，HFS 自带那份（窗口预热 + prefs 刷新）会自己运行，
# 本文件无需也无法代劳，也不要在这里补执行其他 uiready.py。
#
# 这里只做 HouTools 自己的两件事。装默认键位必须等菜单 XML 注册完热键
# 符号，所以放 uiready 而不是 ready.py / 123.py。

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
