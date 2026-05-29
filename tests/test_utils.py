"""Utility helpers."""


import pytest

from steadfast.utils import (
    async_retry,
    extract_domain,
    generate_id,
    sanitize_filename,
    utcnow,
)


def test_utcnow_is_tz_aware():
    now = utcnow()
    assert now.tzinfo is not None


def test_generate_id_no_prefix():
    out = generate_id()
    assert len(out) == 16  # 8 bytes hex


def test_generate_id_with_prefix():
    out = generate_id("usr")
    assert out.startswith("usr_")
    assert len(out) == 20


def test_extract_domain():
    assert extract_domain("https://x.com/home") == "x.com"
    assert extract_domain("http://www.example.com/path?q=1") == "www.example.com"
    assert extract_domain("not-a-real-url") == "not-a-real-url"


def test_sanitize_filename_strips_unsafe():
    assert sanitize_filename("hello world/with:bad?chars*") == "hello_worldwithbadchars"


def test_sanitize_filename_caps_length():
    out = sanitize_filename("x" * 500)
    assert len(out) == 200


@pytest.mark.asyncio
async def test_async_retry_succeeds_first_try():
    calls = 0

    @async_retry(max_attempts=3, delay=0.01)
    async def f():
        nonlocal calls
        calls += 1
        return "ok"

    assert await f() == "ok"
    assert calls == 1


@pytest.mark.asyncio
async def test_async_retry_eventually_succeeds():
    calls = 0

    @async_retry(max_attempts=3, delay=0.01, backoff=1.0)
    async def f():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("fail")
        return "ok"

    assert await f() == "ok"
    assert calls == 3


@pytest.mark.asyncio
async def test_async_retry_gives_up_after_max_attempts():
    calls = 0

    @async_retry(max_attempts=2, delay=0.01, backoff=1.0)
    async def f():
        nonlocal calls
        calls += 1
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError, match="nope"):
        await f()
    assert calls == 2
