"""
Automation — 任务类型定义
=============================
TaskType 枚举 + TaskParams 数据类 + TaskItem 容器（可序列化）。
"""

from enum import Enum
from dataclasses import dataclass
from typing import Union


class TaskType(Enum):
    """自动化任务类型枚举。"""
    BUTTON_CLICK = "BUTTON_CLICK"
    FLIPBOOK = "FLIPBOOK"
    HOME_ASSISTANT = "HOME_ASSISTANT"
    OPEN_DW = "OPEN_DW"


@dataclass
class ButtonClickParams:
    """按钮点击参数：指定节点路径和参数名。"""
    node_path: str
    parm_name: str


@dataclass
class FlipbookParams:
    """Flipbook 渲染参数：帧范围、输出路径、是否保存到磁盘。"""
    start_frame: str = "$RFSTART"
    end_frame: str = "$RFEND"
    output_path: str = "$HIP/FlipBook/$HIPNAME/$HIPNAME.$F4.jpg"
    save_to_disk: bool = True


@dataclass
class HomeAssistantParams:
    """Home Assistant 自动化参数：Webhook URL。"""
    webhook_url: str


@dataclass
class OpenDWParams:
    """打开DW 参数：软件路径来自应用级配置文件，任务本身无参数。"""


TaskParams = Union[ButtonClickParams, FlipbookParams, HomeAssistantParams, OpenDWParams]


@dataclass
class TaskItem:
    """任务项 —— 组合了类型、参数和启用状态。"""
    task_type: TaskType
    params: TaskParams
    enabled: bool

    def to_dict(self) -> dict:
        """序列化为 dict，frame_range 转为列表。"""
        base = {
            "type": self.task_type.value,
            "enabled": self.enabled,
        }

        if self.task_type == TaskType.BUTTON_CLICK:
            assert isinstance(self.params, ButtonClickParams)
            base["params"] = {
                "node_path": self.params.node_path,
                "parm_name": self.params.parm_name,
            }
        elif self.task_type == TaskType.FLIPBOOK:
            assert isinstance(self.params, FlipbookParams)
            base["params"] = {
                "start_frame": self.params.start_frame,
                "end_frame": self.params.end_frame,
                "output_path": self.params.output_path,
                "save_to_disk": self.params.save_to_disk,
            }
        elif self.task_type == TaskType.HOME_ASSISTANT:
            assert isinstance(self.params, HomeAssistantParams)
            base["params"] = {
                "webhook_url": self.params.webhook_url,
            }
        elif self.task_type == TaskType.OPEN_DW:
            assert isinstance(self.params, OpenDWParams)
            base["params"] = {}
        else:
            raise ValueError(f"Unknown task type: {self.task_type}")

        return base

    @staticmethod
    def from_dict(data: dict) -> "TaskItem":
        """从 dict 反序列化，frame_range 列表恢复为 tuple。

        容错语义与 data_manager.deserialize_params 对齐：params 缺失/
        多余字段走默认值，只有 type 缺失或未知、enabled 缺失才报错——
        公开 API load_tasks 直接调本方法，硬下标会把手改坏的配置文件
        变成 KeyError。
        """
        if not isinstance(data, dict) or "type" not in data:
            raise ValueError(f"任务缺少 type 字段: {data!r}")
        type_str = data["type"]
        params_data = data.get("params") or {}

        if type_str == "BUTTON_CLICK":
            task_type = TaskType.BUTTON_CLICK
            params: TaskParams = ButtonClickParams(
                node_path=params_data.get("node_path", ""),
                parm_name=params_data.get("parm_name", ""),
            )
        elif type_str == "FLIPBOOK":
            task_type = TaskType.FLIPBOOK
            params = FlipbookParams(
                start_frame=params_data.get("start_frame", "$RFSTART"),
                end_frame=params_data.get("end_frame", "$RFEND"),
                output_path=params_data.get("output_path", "$HIP/FlipBook/$HIPNAME/$HIPNAME.$F4.jpg"),
                save_to_disk=params_data.get("save_to_disk", True),
            )
        elif type_str == "HOME_ASSISTANT":
            task_type = TaskType.HOME_ASSISTANT
            params = HomeAssistantParams(
                webhook_url=params_data.get("webhook_url", ""),
            )
        elif type_str == "OPEN_DW":
            task_type = TaskType.OPEN_DW
            params: TaskParams = OpenDWParams()
        else:
            raise ValueError(f"Unknown task type string: {type_str}")

        return TaskItem(
            task_type=task_type,
            params=params,
            enabled=data.get("enabled", True),
        )
