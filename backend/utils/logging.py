"""Structured logging — one line per fetch/event, readable by judges
who tail the logs during evaluation."""
from __future__ import annotations

import logging
import sys

from backend.config import LOG_LEVEL


def get_logger(name: str) -> logging.Logger:
    """Return a logger that prints to stdout with a clean single-line format."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured
    logger.setLevel(LOG_LEVEL)
    handler = logging.StreamHandler(sys.stdout)
    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    logger.propagate = False
    return logger
