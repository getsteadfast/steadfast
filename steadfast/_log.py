"""Logging helper for Steadfast.

We use stdlib `logging` (no structlog dependency) but accept structured
keyword arguments via a simple LoggerAdapter so call sites read the same:

    log = get_logger(__name__)
    log.info("Browser context created", account_key=key, viewport=v)

Apps that want a fully configured root logger can call
`steadfast.configure_logging()`.  Library users are not forced to.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import TYPE_CHECKING, Any

# `logging.LoggerAdapter` became a Generic in Python 3.11.  On 3.10 the
# subscripted form `LoggerAdapter[logging.Logger]` raises TypeError at
# class-definition time.  Split the base class so mypy still gets the
# parameterized type but the runtime always sees the bare class.
if TYPE_CHECKING:
    _LoggerAdapterBase = logging.LoggerAdapter[logging.Logger]
else:
    _LoggerAdapterBase = logging.LoggerAdapter


class _KVAdapter(_LoggerAdapterBase):
    """LoggerAdapter that renders kwargs as `key=value` after the message."""

    def process(
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> tuple[Any, MutableMapping[str, Any]]:
        extra = kwargs.pop("extra", {})
        merged = {**(self.extra or {}), **extra}
        # Treat any non-stdlib-logging kwargs as structured fields
        std = {"exc_info", "stack_info", "stacklevel"}
        struct = {k: v for k, v in list(kwargs.items()) if k not in std}
        for k in struct:
            kwargs.pop(k, None)
        struct = {**merged, **struct}
        if struct:
            rendered = " ".join(f"{k}={v!r}" for k, v in struct.items())
            msg = f"{msg} {rendered}"
        return msg, kwargs


def get_logger(name: str | None = None) -> _KVAdapter:
    """Return a Steadfast logger.

    Names default to "steadfast" so all package logs share a single root.
    Library users can attach their own handlers to that root.
    """
    return _KVAdapter(logging.getLogger(name or "steadfast"), {})


def configure_logging(level: str | int = "INFO") -> None:
    """Optional: configure a basic stdout handler for the "steadfast" root.

    Calling this is *not* required to use the library — by default Steadfast
    writes to whatever loggers the host application has configured.
    """
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger("steadfast")
    if root.handlers:
        return  # already configured
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(level)
