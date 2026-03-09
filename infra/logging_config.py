"""
Structured logging configuration for Crypto Spread Monitor.

Usage:
    from infra.logging_config import setup_logging

    log = setup_logging("collectors", config["logging"])
    log.info("connected", exchange="binance", market="spot")
"""

import logging
import logging.handlers
import os
import sys
from typing import Any

import structlog


def _get_level(component: str, logging_cfg: dict[str, Any]) -> int:
    """Return numeric log level for the given component."""
    levels: dict[str, str] = logging_cfg.get("levels", {})
    level_name: str = levels.get(component, logging_cfg.get("level", "INFO"))
    return getattr(logging, level_name.upper(), logging.INFO)


def setup_logging(
    component: str,
    logging_cfg: dict[str, Any],
    log_dir: str = "logs",
) -> structlog.stdlib.BoundLogger:
    """
    Configure structlog for *component* and return a bound logger.

    Parameters
    ----------
    component:
        Module/component name (must match a key in config.yaml logging.levels).
    logging_cfg:
        The ``logging`` section of config.yaml.
    log_dir:
        Directory where rotating log files are written.

    Returns
    -------
    structlog.stdlib.BoundLogger
        Logger pre-bound with ``component=component``.
    """
    level = _get_level(component, logging_cfg)

    # --- stdlib root handler setup (done once per process) ---
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        _configure_handlers(root_logger, log_dir, level)
    else:
        root_logger.setLevel(min(root_logger.level or logging.WARNING, level))

    # Per-component logger so its level can differ from root
    std_logger = logging.getLogger(component)
    std_logger.setLevel(level)

    # --- structlog processors ---
    is_tty = sys.stderr.isatty()
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if is_tty:
        renderer: Any = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Attach ProcessorFormatter to root handler if not already done
    for handler in root_logger.handlers:
        if not isinstance(handler.formatter, structlog.stdlib.ProcessorFormatter):
            handler.setFormatter(
                structlog.stdlib.ProcessorFormatter(
                    processors=[
                        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                        renderer,
                    ],
                    foreign_pre_chain=shared_processors,
                )
            )

    return structlog.get_logger(component).bind(component=component)


def _configure_handlers(
    root_logger: logging.Logger,
    log_dir: str,
    level: int,
) -> None:
    """Attach console + rotating file handlers to *root_logger*."""
    root_logger.setLevel(level)

    # Console (stderr)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(level)
    root_logger.addHandler(console_handler)

    # Rotating file (daily, keep 7 days)
    os.makedirs(log_dir, exist_ok=True)
    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=os.path.join(log_dir, "csm.log"),
        when="midnight",
        backupCount=7,
        encoding="utf-8",
        utc=True,
    )
    file_handler.setLevel(logging.DEBUG)  # file captures everything
    root_logger.addHandler(file_handler)
