"""Exception hierarchy + message formatting."""

import pytest

from steadfast.exceptions import (
    AccountSuspended,
    BrowserError,
    LoginFailed,
    PlatformError,
    ProxyError,
    RateLimited,
    SteadfastError,
)


def test_hierarchy_is_sane():
    """Every Steadfast error should subclass SteadfastError."""
    assert issubclass(BrowserError, SteadfastError)
    assert issubclass(ProxyError, SteadfastError)
    assert issubclass(PlatformError, SteadfastError)
    assert issubclass(LoginFailed, PlatformError)
    assert issubclass(RateLimited, PlatformError)
    assert issubclass(AccountSuspended, PlatformError)


def test_platform_error_carries_platform_name():
    err = PlatformError("twitter", "session expired")
    assert err.platform == "twitter"
    assert "[twitter]" in str(err)
    assert "session expired" in str(err)


def test_rate_limited_includes_retry_after():
    err = RateLimited("reddit", retry_after=60)
    assert err.platform == "reddit"
    assert err.retry_after == 60
    assert "60s" in str(err)


def test_rate_limited_default_retry_after():
    err = RateLimited("linkedin")
    assert err.retry_after == 0


def test_caught_as_base_error():
    """All errors must be catchable as the base."""
    for ExcCls in (BrowserError, ProxyError, LoginFailed, RateLimited):
        with pytest.raises(SteadfastError):
            if ExcCls is RateLimited or ExcCls is LoginFailed:
                raise ExcCls("test", "boom")
            else:
                raise ExcCls("boom")
