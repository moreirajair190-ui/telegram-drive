from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .paths import LOG_DIR, ensure_dirs


def setup_logging(level: int = logging.INFO) -> None:
    ensure_dirs()
    log_path = LOG_DIR / "app.log"

    handler = RotatingFileHandler(
        log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)

    handlers: list[logging.Handler] = [handler]

    # Em desenvolvimento (não congelado) também mostra logs no console.
    if not getattr(sys, "frozen", False):
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        handlers.append(stream)

    root = logging.getLogger()
    root.setLevel(level)
    # Evita handlers duplicados se setup_logging for chamado mais de uma vez.
    for existing in list(root.handlers):
        root.removeHandler(existing)
    for h in handlers:
        root.addHandler(h)

    # Reduz o ruído de bibliotecas de rede.
    logging.getLogger("pyrogram").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
