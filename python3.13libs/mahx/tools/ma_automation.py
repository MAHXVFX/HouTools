"""MA Automation - 自动化批处理工具入口。

任务类型：节点按钮点击 / Flipbook 拍屏 / HomeAssistant Webhook。
界面以 Python Panel 形式在 Houdini 浮动面板中打开（节点拖放原生投递）。
"""


def run():
    from mahx.automation.window import open_floating_panel
    open_floating_panel()
