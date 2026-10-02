"""Central logging for the houtools toolset.

All modules obtain their logger through get_logger() so handler setup
happens exactly once even across hot reloads: the logging module keeps
logger singletons, so reloading this file never duplicates handlers.
"""

import logging

LOGGER_NAME = "houtools"

_configured = False


def get_logger(child=""):
    """Return the 'houtools' logger, or a 'houtools.<child>' logger for a submodule."""
    logger = logging.getLogger(LOGGER_NAME)
    if not _configured:
        _configure(logger)
    if child:
        return logger.getChild(child)
    return logger


def _configure(logger):
    global _configured
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
    _configured = True
