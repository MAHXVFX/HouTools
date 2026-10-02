"""
Automation — Data Manager 数据持久化模块
==============================================
提供 AutomationDataManager 类，负责 JSON 文件的读写和任务对象的
序列化/反序列化。

所有方法均为 @classmethod，因为全局只需一个逻辑实例，
但数据路径依赖 $HIP（动态计算），不使用 BaseJsonManager。

**多文件支持**:三方法 ``get_data_path`` / ``load`` / ``save`` 均接受
可选的 ``filename`` 参数(无 ``.json`` 后缀),用于支持 UI 的多配置
下拉——用户可选择配置目录下任一现存配置,或经"新建配置"对话框
输入新名,让 ``save()`` 在 Start 时创建新文件。``filename=None``
走默认 ``Automation.json``,保持向后兼容。
"""

import os
import json
import tempfile
import logging
from dataclasses import asdict, fields
from typing import Optional

from .task_types import (
    TaskType,
    TaskItem,
    ButtonClickParams,
    FlipbookParams,
    HomeAssistantParams,
    OpenDWParams,
)

from houtools.core.constants import PROJECT_ROOT
from houtools.core.log import get_logger
logger = get_logger("automation.data")

# 打开DW 任务的默认可执行文件路径（写入 Automation_Config.json 的初始值）
DW_EXE_PATH_DEFAULT = "C:/Program Files/Thinkbox/Deadline10/bin/deadlineworker.exe"


class AutomationDataManager:
    """Automation 数据持久化管理器。

    所有方法均为类方法，无需实例化即可使用。
    数据以 JSON 格式存储在 ``{HIP}/HouTools_cfg/Automation_json/Automation.json``，
    $HIP 不可用时 fallback 到系统临时目录。
    """

    # ── 路径管理 ─────────────────────────────────────────

    @classmethod
    def _hip_base(cls) -> str:
        """返回配置根目录：Houdini 内为 $HIP，否则 fallback 系统临时目录。"""
        try:
            import hou  # noqa: N812 — only available inside Houdini
            hip = hou.getenv("HIP")
            if hip:
                return hip
        except ImportError:
            pass
        return tempfile.gettempdir()

    @classmethod
    def get_data_path(cls, filename: Optional[str] = None) -> str:
        """返回数据文件的绝对路径(纯计算,不创建目录)。

        优先级:
          1. ``hou.getenv("HIP")`` — Houdini $HIP 环境变量
          2. ``tempfile.gettempdir()`` — 系统临时目录(fallback)

        Args:
            filename: 配置文件 basename(**无** ``.json`` 后缀)。
                      ``None`` 时返回默认 ``Automation.json``。

        ``hou`` 只在函数内部 try/except 导入,避免 Houdini 外 ImportError。

        **无副作用**:不创建任何文件/目录。配置目录只在 ``save()`` 真正
        写入时才创建(由调用方负责)。
        """
        base = cls._hip_base()

        name = filename if filename else "Automation"
        return os.path.join(base, "HouTools_cfg", "Automation_json", f"{name}.json")

    # ── 应用级配置(Automation_Config.json) ─────────────

    @classmethod
    def get_app_config_path(cls) -> str:
        """返回应用级配置文件路径: ``{项目根}/Automation_Config.json``。

        随插件项目一起发布、版本化管理,不随 $HIP 走 —— DW 软件路径
        虽是机器相关的,但插件安装目录本来就是每台机器一份,直接改
        安装目录里的这个文件即可。
        """
        return str(PROJECT_ROOT / "Automation_Config.json")

    @classmethod
    def ensure_dw_config(cls) -> dict:
        """确保应用级配置文件可用,返回其内容 dict。

        配置文件随项目发布;意外缺失(被删除等)或缺 ``dw_exe_path``
        字段时,以默认路径补建。面板打开时调用,兜底保证文件始终可编辑。
        """
        path = cls.get_app_config_path()
        data: dict = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
            except Exception:
                data = {}

        if "dw_exe_path" not in data:
            data["dw_exe_path"] = DW_EXE_PATH_DEFAULT
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                logger.warning("无法写入应用配置文件: %s", path)

        return data

    @classmethod
    def load_dw_exe_path(cls) -> str:
        """返回打开DW 任务使用的软件路径(只读,不修改文件)。

        文件不存在 / 字段缺失 / 值为空时返回 ``DW_EXE_PATH_DEFAULT``。
        """
        path = cls.get_app_config_path()
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                value = data.get("dw_exe_path")
                if isinstance(value, str) and value.strip():
                    return value.strip()
        except Exception:
            pass
        return DW_EXE_PATH_DEFAULT

    # ── 核心 IO ──────────────────────────────────────────

    @classmethod
    def load(cls, filename: Optional[str] = None) -> list[dict]:
        """从 JSON 文件读取任务数据列表。

        Args:
            filename: 配置文件 basename(**无** ``.json`` 后缀)。
                      ``None`` 时加载默认 ``Automation.json``。

        返回 ``data["tasks"]``,若文件不存在或 JSON 损坏则返回空列表。
        """
        path = cls.get_data_path(filename)
        try:
            if not os.path.exists(path):
                return []
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("tasks", [])
        except json.JSONDecodeError as exc:
            logger.warning("JSON 解析失败: %s (%s)", path, exc)
            return []
        except Exception as exc:
            logger.warning("读取配置失败: %s (%s)", path, exc)
            return []

    @classmethod
    def save(cls, tasks_data: list[dict], filename: Optional[str] = None) -> bool:
        """将任务 dict 列表写入 JSON 文件。

        Args:
            tasks_data: 任务数据列表
            filename: 配置文件 basename(**无** ``.json`` 后缀)。
                      ``None`` 时保存到默认 ``Automation.json``。

        写入结构: ``{"tasks": tasks_data}``
        使用 ``ensure_ascii=False``(支持中文)和 ``indent=2``。

        **副作用**:首次调用会创建 ``{HIP}/HouTools_cfg/Automation_json/`` 目录
        (``os.makedirs(exist_ok=True)``)。这是 DataManager 中**唯一**允许
        创建配置目录的入口,与"仅在 Start 时落盘"的语义配合
        —— 打开面板不会产生任何文件。

        Returns:
            True 写入成功,False 写入异常。
        """
        try:
            path = cls.get_data_path(filename)
            os.makedirs(os.path.dirname(path), exist_ok=True)  # 仅此处创建配置目录
            # 读取现有数据（保留 settings）
            existing_data = {}
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        existing_data = json.load(f)
                except Exception:
                    pass
            # 合并数据
            existing_data["tasks"] = tasks_data
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    existing_data,
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            return True
        except Exception:
            return False

    @classmethod
    def load_settings(cls, filename: Optional[str] = None) -> dict:
        """从 JSON 文件读取设置数据。

        Args:
            filename: 配置文件 basename(**无** ``.json`` 后缀)。

        返回 ``data["settings"]``,若文件不存在或 JSON 损坏则返回空 dict。
        """
        path = cls.get_data_path(filename)
        try:
            if not os.path.exists(path):
                return {}
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("settings", {})
        except Exception:
            return {}

    @classmethod
    def save_settings(cls, settings_data: dict, filename: Optional[str] = None) -> bool:
        """将设置 dict 写入 JSON 文件。

        Args:
            settings_data: 设置数据
            filename: 配置文件 basename(**无** ``.json`` 后缀)。

        写入结构: ``{"tasks": [...], "settings": settings_data}``
        """
        try:
            path = cls.get_data_path(filename)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # 读取现有数据（保留 tasks）
            existing_data = {}
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        existing_data = json.load(f)
                except Exception:
                    pass
            # 合并数据
            existing_data["settings"] = settings_data
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    existing_data,
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            return True
        except Exception:
            return False

    @classmethod
    def list_configs(cls) -> list[str]:
        """返回配置目录中所有 ``.json`` 配置文件的 basename 列表(**无后缀**)。

        行为约定:
          - **不创建** 配置目录(若不存在返回空列表,与 ``load()`` 一致)
          - 只看顶层文件,排除子目录
          - 自动去 ``.json`` 后缀,直接作为 ``load(filename)`` /
            ``save(data, filename)`` 的入参
          - **不验证 JSON 有效性**(空文件 / 损坏文件也会出现在列表中,
            由 ``load()`` 容错处理返回 ``[]``)
          - 文件名按字典序排序(sorted)——保证 UI 下拉显示稳定
          - 出错时静默返回空列表(权限错误等)

        Returns:
            排序后的 basename 列表,例 ``["Automation", "MAtest1", "MAtest2"]``。
        """
        json_dir = os.path.dirname(cls.get_data_path())
        if not os.path.isdir(json_dir):
            return []
        try:
            return sorted(
                name[:-5]
                for name in os.listdir(json_dir)
                if name.endswith(".json")
                and os.path.isfile(os.path.join(json_dir, name))
            )
        except Exception:
            return []

    # ── 序列化 / 反序列化 ─────────────────────────────────

    @classmethod
    def serialize_task_type(cls, task_type: TaskType) -> str:
        """TaskType 枚举 → 字符串（枚举的 value）。"""
        return task_type.value

    @classmethod
    def deserialize_task_type(cls, type_str: str) -> TaskType:
        """字符串 → TaskType 枚举。"""
        return TaskType(type_str)

    @classmethod
    def serialize_params(cls, params) -> dict:
        """Params dataclass → dict。"""
        return asdict(params)

    @classmethod
    def deserialize_params(
        cls,
        type_str: str,
        params_dict: dict,
    ):
        """dict → Params dataclass。

        根据 ``type_str``（匹配 ``TaskType`` 的 value）决定返回的参数类型。
        ``FlipbookParams`` 只恢复当前 dataclass 定义的字段：旧 JSON 文件里
        的遗留字段（如 frame_range）被忽略，缺失字段走 dataclass 默认值。
        """
        task_type = TaskType(type_str)

        if task_type == TaskType.BUTTON_CLICK:
            return ButtonClickParams(**params_dict)
        elif task_type == TaskType.FLIPBOOK:
            known = {f.name for f in fields(FlipbookParams)}
            return FlipbookParams(**{
                k: v for k, v in params_dict.items() if k in known
            })
        elif task_type == TaskType.HOME_ASSISTANT:
            return HomeAssistantParams(**params_dict)
        elif task_type == TaskType.OPEN_DW:
            return OpenDWParams()
        else:
            raise ValueError(f"未知参数类型: {type_str}")

    # ── 高层便捷方法 ─────────────────────────────────────

    @classmethod
    def save_tasks(cls, tasks: list[TaskItem]) -> bool:
        """直接接受 ``TaskItem`` 列表，自动序列化后保存。"""
        tasks_data = [t.to_dict() for t in tasks]
        return cls.save(tasks_data)

    @classmethod
    def load_tasks(cls) -> list[TaskItem]:
        """加载并自动反序列化为 ``TaskItem`` 列表。"""
        data_list = cls.load()
        return [TaskItem.from_dict(d) for d in data_list]
