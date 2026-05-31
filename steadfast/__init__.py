"""Steadfast — browser agents that don't lose their sessions.

Quick start::

    from pathlib import Path
    from steadfast import (
        AntiDetect, BrowserManager, BrowserManagerConfig,
        configure_logging,
    )

    configure_logging("INFO")
    cfg = BrowserManagerConfig(profiles_dir=Path("./profiles"))
    bm = BrowserManager(cfg, AntiDetect())
    await bm.start()
    page = await bm.get_page("twitter_main")
    # ... use page ...
    await bm.save_state("twitter_main")
    await bm.shutdown()
"""

from __future__ import annotations

__version__ = "0.1.0"

from ._log import configure_logging, get_logger
from .anti_detect import (
    COMMON_VIEWPORTS,
    DEFAULT_USER_AGENTS,
    AntiDetect,
    ProxyInfo,
)
from .browser_manager import BrowserManager, BrowserManagerConfig
from .exceptions import (
    AccountSuspended,
    BrowserError,
    LoginFailed,
    PlatformError,
    ProxyError,
    RateLimited,
    SteadfastError,
)

# RemoteDisplay shells out to Xvfb/x11vnc — optional, only available on
# servers with those binaries installed. Always importable; .start() will
# raise if the binaries are missing.
from .remote_display import RemoteDisplay

__all__ = [
    "__version__",
    # logging
    "configure_logging",
    "get_logger",
    # anti-detect
    "AntiDetect",
    "ProxyInfo",
    "COMMON_VIEWPORTS",
    "DEFAULT_USER_AGENTS",
    # browser manager
    "BrowserManager",
    "BrowserManagerConfig",
    # remote display
    "RemoteDisplay",
    # exceptions
    "SteadfastError",
    "BrowserError",
    "ProxyError",
    "PlatformError",
    "LoginFailed",
    "RateLimited",
    "AccountSuspended",
]
