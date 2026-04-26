from __future__ import annotations

import logging
import sys

import structlog


def configure(level: str = "INFO") -> structlog.stdlib.BoundLogger:
    logging.basicConfig(
        format="%(message)s", stream=sys.stderr, level=getattr(logging, level)
    )
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.KeyValueRenderer(
                key_order=["timestamp", "level", "event"]
            ),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    return structlog.get_logger()


def get() -> structlog.stdlib.BoundLogger:
    return structlog.get_logger()
