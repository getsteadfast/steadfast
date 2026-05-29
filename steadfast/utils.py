"""Small utility helpers used across Steadfast.

We deliberately keep this file small. Anything DB-, crypto-, or
MarketPilot-specific from the original `core/utils.py` is *not* extracted —
this library has no DB, no Fernet, no env-var coupling.
"""

from __future__ import annotations

import asyncio
import re
import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from functools import wraps
from typing import Any
from urllib.parse import urlparse


def utcnow() -> datetime:
    """Current UTC datetime (tz-aware)."""
    return datetime.now(timezone.utc)


def generate_id(prefix: str = "") -> str:
    """Generate a short random hex id, optionally prefixed."""
    rand = secrets.token_hex(8)
    return f"{prefix}_{rand}" if prefix else rand


def extract_domain(url: str) -> str:
    """Extract host/domain from a URL string."""
    parsed = urlparse(url)
    return parsed.netloc or parsed.path.split("/")[0]


def sanitize_filename(name: str) -> str:
    """Make a string safe for use as a filename (no slashes, no controls)."""
    name = re.sub(r"[^\w\s\-.]", "", name)
    name = re.sub(r"\s+", "_", name)
    return name[:200]


def async_retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Decorator: retry an async function with exponential backoff."""

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last: BaseException | None = None
            current_delay = delay
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as exc:
                    last = exc
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff
            assert last is not None
            raise last

        return wrapper

    return decorator
