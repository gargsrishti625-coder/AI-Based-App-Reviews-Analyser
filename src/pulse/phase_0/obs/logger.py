from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

import structlog

# Context vars injected into every log line by the context processor below.
_run_id: ContextVar[str] = ContextVar("run_id", default="")
_product: ContextVar[str] = ContextVar("product", default="")
_iso_week: ContextVar[str] = ContextVar("iso_week", default="")
_phase: ContextVar[int] = ContextVar("phase", default=-1)


def _inject_run_context(
    logger: logging.Logger,  # noqa: ARG001
    method: str,             # noqa: ARG001
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    if run_id := _run_id.get():
        event_dict["run_id"] = run_id
    if product := _product.get():
        event_dict["product"] = product
    if iso_week := _iso_week.get():
        event_dict["iso_week"] = iso_week
    phase = _phase.get()
    if phase >= 0:
        event_dict["phase"] = phase
    return event_dict


def configure(*, json: bool = True) -> None:
    """Call once at process startup to configure structlog."""
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        _inject_run_context,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    if json:
        shared_processors.append(structlog.processors.JSONRenderer())
    else:
        shared_processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=shared_processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def bind_run_context(run_id: str, product: str, iso_week: str) -> None:
    _run_id.set(run_id)
    _product.set(product)
    _iso_week.set(iso_week)


def bind_phase(phase: int) -> None:
    _phase.set(phase)


def get_logger(name: str = __name__) -> structlog.BoundLogger:
    return structlog.get_logger(name)
