"""JSON file-backed settings storage."""

import json
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
            on_disk = {}
            if self.path.exists():
                try:
                    on_disk = json.loads(self.path.read_text(encoding="utf-8"))
                except Exception as exc:
                    log.warning("cannot read %s: %s", self.path, exc)
            merged = dict(self._defaults)
            merged.update(on_disk)
            self._data = merged

    def save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                self.path.write_text(
                    json.dumps(self._data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception as exc:
                log.error("cannot save %s: %s", self.path, exc)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value, save=True):
        with self._lock:
            self._data[key] = value
            if save:
                self.save()

    def as_dict(self):
        return dict(self._data)
