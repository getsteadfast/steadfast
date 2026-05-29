"""Logging adapter."""

import logging

from steadfast._log import _KVAdapter, configure_logging, get_logger


def test_get_logger_returns_adapter():
    log = get_logger("test")
    assert isinstance(log, _KVAdapter)


def test_kv_adapter_renders_kwargs(caplog):
    """Structured kwargs should render as key=value after the message."""
    log = get_logger("steadfast.test")
    with caplog.at_level(logging.INFO, logger="steadfast.test"):
        log.info("hello", count=3, name="alice")
    msgs = [r.getMessage() for r in caplog.records]
    assert any("hello" in m and "count=3" in m and "name='alice'" in m for m in msgs)


def test_configure_logging_attaches_handler_once():
    """Calling configure_logging twice should not double-attach handlers."""
    root = logging.getLogger("steadfast")
    # Reset for the test
    for h in list(root.handlers):
        root.removeHandler(h)
    configure_logging("DEBUG")
    n = len(root.handlers)
    configure_logging("DEBUG")
    assert len(root.handlers) == n  # idempotent
