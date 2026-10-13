"""Logging setup. Everything goes to stderr: stdout belongs to the MCP stdio transport."""

from __future__ import annotations

import logging
import sys

ROOT_LOGGER = "jevcrawl"
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a logger under the ``jevcrawl`` namespace."""
    if not name or name == ROOT_LOGGER:
        return logging.getLogger(ROOT_LOGGER)
    return logging.getLogger(f"{ROOT_LOGGER}.{name.removeprefix(ROOT_LOGGER + '.')}")


def configure_logging(level: int | str = logging.WARNING) -> None:
    """Attach one stderr handler to the ``jevcrawl`` logger. Safe to call repeatedly."""
    logger = logging.getLogger(ROOT_LOGGER)
    logger.setLevel(level)
    if not any(getattr(h, "_jevcrawl", False) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_FORMAT))
        handler._jevcrawl = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.propagate = False
    for noisy in ("httpx", "httpcore", "hpack"):  # they log every URL at INFO
        logging.getLogger(noisy).setLevel(max(logging.WARNING, logger.level))
