"""MA Automation - 自动化批处理工具入口。

任务类型：节点按钮点击 / Flipbook 拍屏 / HomeAssistant Webhook。
"""


def run():
    from mahx.automation.window import show_automation_window
    show_automation_window()
