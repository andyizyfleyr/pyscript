"""Configuration des logs structurés (structlog).

Deux sorties :
  * console — rendu coloré lisible en développement ;
  * fichier  — JSON ligne à ligne dans le dossier de données, exploitable
    (``jq -c 'select(.level=="error")' tikvault.log``).
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

import structlog

_CONFIGURED = False


def setup_logging(
    *,
    log_file: Path | None = None,
    level: int = logging.INFO,
    json_console: bool = False,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
) -> None:
    """Configure structlog + le logging standard (idempotent)."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    # En application fenêtrée PyInstaller (console=False), sys.stderr vaut None :
    # il n'y a tout simplement pas de console. On saute alors la sortie console
    # (le fichier de log reste actif) au lieu de planter sur `.isatty()`.
    if sys.stderr is not None:
        console_renderer = (
            structlog.processors.JSONRenderer(ensure_ascii=False)
            if json_console
            else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty(), sort_keys=False)
        )
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processors=[
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    console_renderer,
                ]
            )
        )
        root.addHandler(console_handler)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        file_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processors=[
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.JSONRenderer(ensure_ascii=False),
                ]
            )
        )
        root.addHandler(file_handler)

    # Les bibliothèques tierces sont bavardes : on les calme.
    for noisy in ("yt_dlp", "httpx", "httpcore", "asyncio", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def bind(**context: object) -> None:
    """Ajoute des clés à tous les logs suivants (ex. ``bind(session="abc")``)."""
    structlog.contextvars.bind_contextvars(**context)
