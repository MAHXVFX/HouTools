"""
Automation — 执行引擎
=========================
QThread 子类，在后台线程中逐个执行任务列表。
支持 4 种任务类型：BUTTON_CLICK / FLIPBOOK / HOME_ASSISTANT / OPEN_DW。

执行器通过 ``hdefereval.executeDeferred`` 将 Houdini API 调用
派发到主线程执行，确保线程安全。

★ 设计意图：对主线程的同步等待（``_run_deferred`` 里的 ``ready.wait()``）
是无超时的，这是有意设计，不是缺陷，禁止加超时。本工具的本质是"逐个触发
Houdini 按钮"，顺序执行的唯一保证就是上一个任务的主线程调用返回后再派发
下一个——主线程被解算/缓存阻塞多久，就等多久（数小时是正常值）。任何超时
都会在任务实际完成前放行下一个任务，破坏顺序语义。因此：

- modal 对话框、长渲染等阻塞主线程的情形，在本语义下都属于"当前任务未
  完成，继续等"，与等解算跑完是同一条规则；
- 取消是协作式的，只能在任务边界生效（见 ``cancel``），在途任务不可打断；
- 已知会弹阻塞对话框的按钮（如 ``dl_Submit``）靠逐案消除弹框根源解决
  （见 ``_execute_button_click`` 的存盘特例），而不是改等待机制。
"""

import logging
import os
import subprocess
import threading
from datetime import datetime

from PySide6.QtCore import QThread, Signal

from .task_types import (
    TaskItem,
    TaskType,
    ButtonClickParams,
    FlipbookParams,
    HomeAssistantParams,
    OpenDWParams,
)
from .data_manager import AutomationDataManager

# 尝试导入 hdefereval — Houdini 环境外不可用，此时为 None
try:
    import hdefereval  # noqa: N812 — only available inside Houdini
except ImportError:
    hdefereval = None

from houtools.core.log import get_logger
logger = get_logger("automation.engine")


class ExecutionEngine(QThread):
    """自动化任务执行引擎。

    接收 ``list[TaskItem]``，在 ``run()`` 中逐项派发给对应的桩执行器。
    通过信号向 UI 层报告进度和结果。
    """

    task_started = Signal(int, str, str)        # (task_index, task_type_value, timestamp_str)
    task_completed = Signal(int, bool, str, float)  # (task_index, success, message, elapsed_seconds)
    all_completed = Signal(int, int, str, float)    # (success_count, fail_count, timestamp_str, total_elapsed)

    def __init__(self, tasks: list[TaskItem], parent=None):
        super().__init__(parent)
        self._tasks = tasks
        self._cancelled = False
        # 主线程预读 DW 软件路径:hou.getenv 严格说不该在 QThread 里调,
        # 每次运行读一次(而非每任务读)也足够
        self._dw_exe_path = AutomationDataManager.load_dw_exe_path()

    # ── 主循环 ────────────────────────────────────────────

    def run(self):
        """遍历所有任务，派发给对应的执行器。

        ``all_completed`` 无论正常跑完还是被取消都会发出（取消只是提前
        break），UI 层依赖它复位状态，不可去掉。
        """
        success_count = 0
        fail_count = 0
        run_start_time = None

        for idx, task in enumerate(self._tasks):
            if self._cancelled:
                break
            if not task.enabled:
                continue

            start_time = datetime.now()
            if run_start_time is None:
                run_start_time = start_time
            self.task_started.emit(idx, task.task_type.value, start_time.strftime("%H:%M:%S"))

            try:
                if task.task_type == TaskType.BUTTON_CLICK:
                    self._execute_button_click(task.params)
                elif task.task_type == TaskType.FLIPBOOK:
                    self._execute_flipbook(task.params)
                elif task.task_type == TaskType.HOME_ASSISTANT:
                    self._execute_home_assistant(task.params)
                elif task.task_type == TaskType.OPEN_DW:
                    self._execute_open_dw(task.params)
                else:
                    raise ValueError(f"未知任务类型: {task.task_type}")

                elapsed = (datetime.now() - start_time).total_seconds()
                self.task_completed.emit(idx, True, "执行成功", elapsed)
                success_count += 1
            except Exception as e:
                self.task_completed.emit(idx, False, str(e), 0)
                fail_count += 1

            self.msleep(100)

        timestamp = datetime.now().strftime("%Y/%m/%d/%H:%M:%S")
        total_elapsed = (datetime.now() - run_start_time).total_seconds() if run_start_time else 0
        self.all_completed.emit(success_count, fail_count, timestamp, total_elapsed)

    # ── 取消 ──────────────────────────────────────────────

    def cancel(self):
        """请求取消，``run()`` 将在当前任务完成后提前退出。

        协作式取消：标志位只在任务循环的边界检查，在途任务（及其对主线程
        的同步等待）不可被打断——这是有意设计，见模块文档。UI 层不得因
        调用了本方法就把界面复位成空闲，应等 ``all_completed`` 到达。
        """
        self._cancelled = True

    # ── 主线程派发 ────────────────────────────────────────

    def _run_deferred(self, fn):
        """通过 ``hdefereval.executeDeferred`` 在主线程执行 ``fn``，并阻塞
        后台线程直到其返回。

        ★ ``ready.wait()`` 不带超时是有意设计，禁止"修复"成带超时等待：
        顺序执行依赖"上一个任务的主线程调用返回后再派发下一个"，主线程
        被解算/缓存/渲染阻塞多久就等多久，任何超时都会提前放行下一个任务
        破坏顺序语义。详见模块文档。

        若 ``hdefereval`` 不可用（测试环境等），则直接执行。
        """
        if hdefereval is not None:
            exc_info: list[BaseException | None] = [None]
            ready = threading.Event()

            def wrapper():
                try:
                    fn()
                except BaseException as e:
                    exc_info[0] = e
                finally:
                    ready.set()

            hdefereval.executeDeferred(wrapper)
            ready.wait()

            if exc_info[0] is not None:
                raise exc_info[0]  # type: ignore[misc]
        else:
            fn()

    # ── 执行器 ─────────────────────────────────────────────

    def _execute_button_click(self, params: ButtonClickParams):
        """执行按钮点击。

        接收 ``ButtonClickParams(node_path, parm_name)``，
        通过 ``hou.node()`` 查找节点，验证并按下按钮。

        Raises:
            ValueError: 节点不存在、参数不存在、非 Button 类型
        """
        def impl():
            import hou
            target_node = hou.node(params.node_path)
            if target_node is None:
                raise ValueError(f"节点不存在: {params.node_path}")

            target_parm = target_node.parm(params.parm_name)
            if target_parm is None:
                raise ValueError(f"参数不存在: {params.parm_name}")

            parm_template = target_parm.parmTemplate()
            if parm_template.type() != hou.parmTemplateType.Button:
                raise ValueError(f"不是按钮参数: {params.parm_name}")

            # dl_Submit（Deadline 提交）的回调要求点击前工程已保存到磁盘，
            # 否则会弹"保存工程"对话框卡住自动化流程；保存失败则不点击。
            # 从未保存过的工程连默认路径都没有,save() 必弹模态对话框问
            # 存放位置 —— 同样按"失败跳过点击"语义处理,请用户先手动保存
            if params.parm_name == "dl_Submit":
                if hou.hipFile.hasNeverSaved():
                    raise ValueError("工程从未保存过，已跳过 dl_Submit：请先手动保存")
                try:
                    hou.hipFile.save()
                except hou.OperationFailed as e:
                    raise ValueError(
                        f"保存工程失败，已跳过 dl_Submit 点击：{e}"
                    ) from e

            target_parm.pressButton()

        self._run_deferred(impl)

    def _execute_flipbook(self, params: FlipbookParams):
        """执行 Flipbook 拍屏。

        使用用户提供的帧范围和输出路径，写入 Houdini 的 flipbook settings，
        保留其他默认设置（分辨率、质量等）。

        Raises:
            RuntimeError: 找不到 Scene Viewer
        """
        def impl():
            import hou
            scene = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
            if scene is None:
                raise RuntimeError("未找到 Scene Viewer")

            # 读取当前 flipbook 设置
            settings = scene.flipbookSettings()

            # 解析帧范围表达式
            try:
                start_str = hou.text.expandString(params.start_frame)
                end_str = hou.text.expandString(params.end_frame)
                start = int(float(start_str))
                end = int(float(end_str))
                settings.frameRange((start, end))
            except (hou.OperationFailed, ValueError, TypeError, OverflowError) as e:
                logger.warning("帧范围无效，已沿用原范围: %s", e)

            # 设置输出路径（不展开 $F4 等表达式，让 Houdini 逐帧展开）
            if params.save_to_disk:
                settings.output(params.output_path)
            else:
                settings.output("")

            scene.flipbook(scene.curViewport(), settings)

        self._run_deferred(impl)

    def _execute_home_assistant(self, params: HomeAssistantParams):
        """执行 HomeAssistant Webhook 调用。

        接收 ``HomeAssistantParams(webhook_url)``，
        通过 ``requests.post()`` 发送 HTTP POST 请求。
        网络请求可直接在后台线程执行，无需 ``executeDeferred``。

        Raises:
            ValueError: webhook_url 为空
            ImportError: requests 模块不存在
            TimeoutError: 请求超时
            ConnectionError: 连接失败
        """
        if not params.webhook_url:
            raise ValueError("Webhook URL 为空")

        try:
            import requests
        except ImportError:
            raise ImportError("requests 模块未安装，请执行 pip install requests")

        try:
            with requests.post(params.webhook_url, timeout=5) as resp:
                # 底层连接随 with 块退出即归还连接池,不再依赖 GC 兜底
                resp.raise_for_status()
        except requests.Timeout:
            raise TimeoutError(f"请求超时: {params.webhook_url}")
        except requests.ConnectionError:
            raise ConnectionError(f"连接失败: {params.webhook_url}")

    def _execute_open_dw(self, params: OpenDWParams):
        """执行打开DW：启动 Deadline Worker。

        软件路径从应用配置文件 ``{项目根}/settings/Automation_Config.json``
        的 ``dw_exe_path`` 字段读取（用户可手动编辑），任务参数为空。
        ``subprocess.Popen`` 非阻塞启动，无需派发主线程。

        Raises:
            ValueError: 路径为空 / 文件不存在 / 启动失败
        """
        # 路径已在引擎构造时(主线程)预读,避免后台线程触碰 hou.getenv
        exe_path = self._dw_exe_path
        if not exe_path:
            raise ValueError(
                "DW 软件路径为空，请在 settings/Automation_Config.json 中配置 dw_exe_path"
            )
        if not os.path.isfile(exe_path):
            raise ValueError(f"DW 软件路径不存在: {exe_path}")

        try:
            subprocess.Popen([exe_path])
        except OSError as e:
            raise ValueError(f"启动 DW 失败: {exe_path} ({e})") from e
