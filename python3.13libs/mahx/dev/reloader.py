"""Hot-reload every mahx module.

Invoked from the main menu (MAHX Tools -> Reload Modules (Dev)).

Modules are reloaded in sys.modules insertion order, which is dependency
order: an import is registered before the module that imports it, so
reloading in this sequence re-executes every module against already
refreshed dependencies (constants before settings, core before tools).
"""

import importlib
import logging
import sys

from mahx.dev import status

log = logging.getLogger("mahx.dev.reloader")


def _candidates():
    """(name, module) pairs to reload, in dependency order."""
    found = []
    for name, module in list(sys.modules.items()):
        if module is None:
            continue
        if not (name == "mahx" or name.startswith("mahx.")):
            continue
        # The reload machinery must not reload itself mid-run.
        if name == "mahx.dev" or name.startswith("mahx.dev."):
            continue
        found.append((name, module))
    return found


def reload_all():
    """Reload all mahx modules. Returns a one-line summary.

    One failing module does not block the rest; a failed module is
    dropped from sys.modules so the next reload (or the next menu click
    through the dispatcher) re-imports it fresh from disk.
    """
    _close_windows()

    reloaded = []
    failures = []

    for name, module in _candidates():
        try:
            importlib.reload(module)
            reloaded.append(name)
            log.debug("reloaded %s", name)
        except Exception as exc:
            log.error("reload failed for %s", name, exc_info=True)
            failures.append((name, str(exc)))
            sys.modules.pop(name, None)

    if failures:
        detail = "; ".join("{} ({})".format(n, e) for n, e in failures)
        message = "MAHX reload: {} ok, {} FAILED - {}".format(
            len(reloaded), len(failures), detail
        )
        status(message, error=True)
    else:
        message = "MAHX reload OK ({} modules)".format(len(reloaded))
        status(message)
    log.info(message)
    return message


def _close_windows():
    """Close registered tool windows so they cannot keep pre-reload code alive.

    Deliberately imports the *currently loaded* window_manager (not a
    fresh import): its registry holds the live windows.
    """
    try:
        import mahx.ui.window_manager as window_manager
    except Exception:
        return
    try:
        window_manager.close_all()
    except Exception as exc:
        log.warning("closing windows failed: %s", exc)
