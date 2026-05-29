"""Exception hierarchy for Steadfast."""


class SteadfastError(Exception):
    """Base exception for all Steadfast errors."""


class BrowserError(SteadfastError):
    """Browser automation error (Playwright launch, context creation, etc.)."""


class ProxyError(SteadfastError):
    """Proxy connection or pool error."""


class PlatformError(SteadfastError):
    """Base error for platform operations (Twitter, LinkedIn, Reddit, ...).

    Carries the platform name so error messages render uniformly.
    """

    def __init__(self, platform: str, message: str):
        self.platform = platform
        super().__init__(f"[{platform}] {message}")


class LoginFailed(PlatformError):
    """Session-not-logged-in, expired cookies, or login-page redirect."""


class RateLimited(PlatformError):
    """Platform rate-limit hit. `retry_after` is seconds to wait."""

    def __init__(self, platform: str, retry_after: int = 0):
        self.retry_after = retry_after
        super().__init__(platform, f"Rate limited. Retry after {retry_after}s")


class AccountSuspended(PlatformError):
    """Account has been banned, shadowbanned, or suspended by the platform."""
