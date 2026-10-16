"""JSON file-backed settings storage."""

import json
import os
import threading

from houtools.core.constants import SETTINGS_DIR
from houtools.core.log import get_logger

log = get_logger("core.settings")


class JsonStore:
    """A single JSON file exposed as a dict.

    Usage: store = JsonStore("my_tool.json", defaults={"size": 100})
    Values are kept in memory; call save() (or use set(..., save=True),
    the default) to persist. Loaded values are merged over defaults, so
    adding new default keys never breaks existing settings files.
    """

    def __init__(self, filename, defaults=None):
        self.path = SETTINGS_DIR / filename
        self._defaults = dict(defaults or {})
        self._lock = threading.RLock()
        self._data = {}
        self.load()

    def load(self):
        with self._lock:
            if self.path.exists():
                try:
                    text = self.path.read_text(encoding="utf-8")
                except OSError as exc:
                    # 读不了 ≠ 文件坏了（杀软/同步盘瞬时占用等）：不动
                    # 原文件，本次先用默认值，下次还能读到
                    log.warning("cannot read %s: %s", self.path, exc)
                    text = None
                if text is not None:
                    try:
                        on_disk = json.loads(text)
                        if not isinstance(on_disk, dict):
                            raise TypeError(
                                "顶层必须是 JSON 对象，实际是 "
                                + type(on_disk).__name__)
                    except Exception as exc:
                        # 解析失败或顶层不是对象：留底回退默认，防止下次
                        # save() 把尚可抢救的内容直接覆盖掉
                        log.warning("cannot read %s: %s", self.path, exc)
                        self._backup_corrupt()
                        on_disk = {}
            else:
                on_disk = {}
            merged = dict(self._defaults)
            merged.update(on_disk)
            self._data = merged

    def _backup_corrupt(self):
        """读不了的文件改名为 .bak 留底再回退默认，防止下次 save() 把
        尚可抢救的内容直接覆盖掉。"""
        bak = self.path.with_name(self.path.name + ".bak")
        try:
            os.replace(self.path, bak)
            log.warning("corrupt settings file kept as %s", bak)
        except OSError as exc:
            log.warning("cannot back up corrupt %s: %s", self.path, exc)

    def save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # 临时名带 PID/线程 id：固定名在并发写时会互踩（两个 Houdini
            # 开同一 settings 目录、或 reload 后同文件的新旧 JsonStore
            # 实例并存），os.replace 可能把对方半写的文件搬上正式名
            tmp = self.path.with_name(
                self.path.name + ".~tmp%d_%d"
                % (os.getpid(), threading.get_ident()))
            try:
                # 临时文件 + 原子替换：写盘中途崩溃/断电不会截断原文件
                tmp.write_text(
                    json.dumps(self._data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                os.replace(tmp, self.path)
            except Exception as exc:
                log.error("cannot save %s: %s", self.path, exc)
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass

    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, default)

    def set(self, key, value, save=True):
        with self._lock:
            self._data[key] = value
            if save:
                self.save()

    def remove(self, key, save=True):
        with self._lock:
            self._data.pop(key, None)
            if save:
                self.save()

    def as_dict(self):
        with self._lock:
            return dict(self._data)
