"""Centralized logging using loguru.

All agents use this — never use stdlib `logging` directly.

Usage:
    from utils.logger import get_logger
    log = get_logger(__name__)
    log.info("Order placed: {}", order_id)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from datetime import date

from loguru import logger

_LOGGER_INITIALIZED = False


def _init_logger() -> None:
    """Configure loguru once — called lazily on first get_logger()."""
    global _LOGGER_INITIALIZED
    if _LOGGER_INITIALIZED:
        return

    log_level = os.getenv("LOG_LEVEL", "INFO")
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    # Remove default handler
    logger.remove()

    # Console — colorized
    logger.add(
        sys.stderr,
        level=log_level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
               "<cyan>{name}</cyan> | <level>{message}</level>",
        colorize=True,
    )

    # File — one per day, retained for 90 days
    log_file = log_dir / f"trading_{date.today():%Y-%m-%d}.log"
    logger.add(
        log_file,
        level="DEBUG",
        rotation="00:00",                # New file at midnight
        retention="90 days",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
        enqueue=True,                    # Thread-safe
    )

    # Errors — separate file for quick triage
    logger.add(
        log_dir / "errors.log",
        level="ERROR",
        rotation="10 MB",
        retention="180 days",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} | {message}",
    )

    _LOGGER_INITIALIZED = True


def get_logger(name: str = "trading"):
    """Return a logger instance bound to a module name."""
    _init_logger()
    return logger.bind(name=name)
