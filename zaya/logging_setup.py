"""Logging local em arquivo, sem vazar segredos no terminal."""

from __future__ import annotations

import logging
from pathlib import Path


def setup_logging(log_dir: Path, level: str = "INFO", verbose: bool = False) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("zaya")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = logging.FileHandler(log_dir / "zaya.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    if verbose:
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        logger.addHandler(stream)

    logger.propagate = False
    return logger
