"""JSON logging for the whole app, built on structlog.

`configure_logging()` is called once at startup (by `create_app()`, later also by
the worker and CLI). After that, both `structlog.get_logger()` and plain stdlib
loggers (uvicorn, SQLAlchemy) print one JSON object per line to stdout.

Every line carries `correlation_id`: the middleware in `correlation.py` binds it
per request; lines outside a request get `null`.
"""

import logging
import sys

import structlog
from structlog.types import EventDict, Processor


def _ensure_correlation_id(_logger: object, _method: str, event_dict: EventDict) -> EventDict:
    event_dict.setdefault("correlation_id", None)
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        _ensure_correlation_id,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Stdlib loggers (uvicorn etc.) go through the same processors via `foreign_pre_chain`.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Uvicorn adds its own plain-text handlers; drop them so its lines go through ours.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True

    # The correlation middleware already logs every request, so silence uvicorn's access log.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers = []
    access_logger.propagate = False
