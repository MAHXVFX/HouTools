"""Menu entry point.

MainMenuCommon.xml scriptCode items call only this module (which is
never reloaded), so a menu click always executes the newest tool code:
import_module returns the freshly reloaded module, or re-imports it
from disk if a previous reload failed and dropped it.
"""

import importlib
import logging

from mahx.dev import status

log = logging.getLogger("mahx.dev.dispatcher")


def run(tool_id, *args, **kwargs):
    """Open mahx.tools.<tool_id> by calling its run(*args, **kwargs)."""
    module_name = "mahx.tools." + tool_id
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        log.error("cannot import %s", module_name, exc_info=True)
        status("MAHX: cannot import {} ({})".format(module_name, exc), error=True)
        return

    entry = getattr(module, "run", None)
    if not callable(entry):
        message = "MAHX: {} has no run() entry point".format(module_name)
        log.error(message)
        status(message, error=True)
        return

    try:
        entry(*args, **kwargs)
    except Exception as exc:
        log.error("tool %s crashed", module_name, exc_info=True)
        status("MAHX: {} crashed ({}) - see log".format(tool_id, exc), error=True)
