"""
Structured JSON logging for all platform services.
Outputs logs in a format compatible with Loki / Promtail.
"""
from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

# Keys whose values must never reach a log sink (matched case-insensitively,
# anywhere in the key, at any nesting depth).
_SENSITIVE_KEY_RE = re.compile(
    r"password|passphrase|private[_-]?key|secret|token|authorization",
    re.IGNORECASE,
)
REDACTED = "***REDACTED***"


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: REDACTED if isinstance(k, str) and _SENSITIVE_KEY_RE.search(k) else _redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(_redact(v) for v in value)
    return value


def redact_sensitive_values(_logger: Any, _method_name: str, event_dict: dict) -> dict:
    """structlog processor: replace values of credential-like keys with a marker.

    Applies recursively to nested dicts, lists and tuples so that, for example,
    ``log.info("connect", request={"host": h, "password": p})`` never writes the
    password. The ``event`` message itself is left untouched.
    """
    return {
        k: (
            REDACTED
            if k != "event" and isinstance(k, str) and _SENSITIVE_KEY_RE.search(k)
            else _redact(v)
        )
        for k, v in event_dict.items()
    }


def configure_logging(
    level: str = "INFO",
    service_name: str = "platform-service",
    environment: str = "development",
) -> None:
    """
    Configure structlog for structured JSON logging.
    Call once at application startup.
    """
    # Shared processors applied to every log event
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        redact_sensitive_values,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # In development: pretty-print logs. In production: JSON.
    renderer: Any
    if environment == "development":
        renderer = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level)
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure stdlib logging to route through structlog
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.getLevelName(level))

    # Bind service context to every future log call
    structlog.contextvars.bind_contextvars(
        service=service_name,
        environment=environment,
    )
