"""Playwright browser pool with persistent per-account sessions.

The core of Steadfast.  Manages a pool of Playwright browser *contexts*,
one per `account_key`, each with:

    * Sticky viewport + user-agent + proxy (persisted as `fingerprint.json`)
    * Persistent cookies + localStorage (`state.json`)
    * Anti-detection init script applied at context creation
    * Concurrency-limited via a semaphore

Typical usage:

    from steadfast import AntiDetect, BrowserManager, BrowserManagerConfig

    cfg = BrowserManagerConfig(profiles_dir=Path("./profiles"))
    bm = BrowserManager(cfg, AntiDetect())
    await bm.start()

    page = await bm.get_page("twitter_main")
    await page.goto("https://x.com/home")
    # ... use page ...

    await bm.save_state("twitter_main")  # cookies persisted to disk
    await bm.shutdown()
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._log import get_logger
from .anti_detect import AntiDetect
from .exceptions import BrowserError

if TYPE_CHECKING:  # pragma: no cover — typing only
    from playwright.async_api import Browser, BrowserContext, Page, Playwright

log = get_logger("steadfast.browser_manager")


# Init script applied to every context — defeats common automation detection.
_ANTI_DETECT_INIT_JS = r"""
    // Override navigator.webdriver
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    delete navigator.__proto__.webdriver;

    // Override chrome detection
    window.chrome = {
        runtime: {
            onMessage: { addListener: function() {}, removeListener: function() {} },
            onConnect: { addListener: function() {}, removeListener: function() {} },
            sendMessage: function() {},
            connect: function() { return { onMessage: { addListener: function() {} }, postMessage: function() {} }; }
        },
        loadTimes: function() { return {}; },
        csi: function() { return {}; },
    };

    // Override permissions
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) =>
        parameters.name === 'notifications'
            ? Promise.resolve({state: Notification.permission})
            : originalQuery(parameters);

    // Realistic plugins array
    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const plugins = [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
            ];
            plugins.length = 3;
            return plugins;
        }
    });

    // Override languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en']
    });

    // Hide automation indicators
    Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });

    // Override connection info
    if (navigator.connection) {
        Object.defineProperty(navigator.connection, 'rtt', { get: () => 50 });
    }

    // Prevent iframe detection of automation
    const originalAttachShadow = Element.prototype.attachShadow;
    Element.prototype.attachShadow = function() {
        return originalAttachShadow.apply(this, arguments);
    };
"""


_DEFAULT_BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-infobars",
    "--window-size=1920,1080",
    "--disable-extensions",
    "--disable-component-extensions-with-background-pages",
    "--disable-default-apps",
    "--enable-features=NetworkService,NetworkServiceInProcess",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-hang-monitor",
    "--disable-ipc-flooding-protection",
    "--disable-renderer-backgrounding",
]


@dataclass
class BrowserManagerConfig:
    """Configuration for :class:`BrowserManager`.

    All paths are plain `pathlib.Path` — no env vars, no settings system.
    """

    profiles_dir: Path
    headless: bool = True
    max_concurrent: int = 4
    timezone: str = "UTC"
    extra_browser_args: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.profiles_dir = Path(self.profiles_dir)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)


class BrowserManager:
    """Pool of Playwright contexts, one per `account_key`."""

    def __init__(
        self,
        config: BrowserManagerConfig,
        anti_detect: AntiDetect | None = None,
    ) -> None:
        self.config = config
        self.anti_detect = anti_detect or AntiDetect()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._contexts: dict[str, BrowserContext] = {}
        self._semaphore = asyncio.Semaphore(config.max_concurrent)
        self._started = False

    # -------------------------------------------------------------- Lifecycle

    async def start(self) -> None:
        """Launch Playwright + the underlying browser. Idempotent."""
        if self._started:
            return
        try:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            args = list(_DEFAULT_BROWSER_ARGS) + list(self.config.extra_browser_args)
            self._browser = await self._playwright.chromium.launch(
                headless=self.config.headless,
                args=args,
            )
            self._started = True
            log.info("Browser launched", headless=self.config.headless)
        except Exception as exc:
            log.error("Failed to launch browser", error=str(exc))
            raise BrowserError(f"Failed to launch browser: {exc}") from exc

    async def shutdown(self) -> None:
        """Close every context, then the browser, then Playwright."""
        log.info("Shutting down browser manager")
        for key in list(self._contexts.keys()):
            await self.close_context(key)
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._browser = None
        self._playwright = None
        self._started = False
        log.info("Browser manager stopped")

    # ---------------------------------------------------------------- Contexts

    async def get_context(
        self,
        account_key: str,
        extra_context_options: dict[str, Any] | None = None,
    ) -> BrowserContext:
        """Get an existing context for `account_key` or create a new one.

        `account_key` may include `/` for nested profiles
        (e.g. `"project_3/twitter_primary"`).
        """
        if not self._started:
            await self.start()

        if account_key in self._contexts:
            try:
                _ = self._contexts[account_key].pages
                return self._contexts[account_key]
            except Exception:
                del self._contexts[account_key]

        await self._semaphore.acquire()
        try:
            ctx = await self._create_context(account_key, extra_context_options)
            self._contexts[account_key] = ctx
            return ctx
        except Exception as exc:
            self._semaphore.release()
            raise BrowserError(
                f"Failed to create browser context for {account_key}: {exc}"
            ) from exc

    async def _create_context(
        self,
        account_key: str,
        extra_context_options: dict[str, Any] | None,
    ) -> BrowserContext:
        assert self._browser is not None, "BrowserManager must be started first"

        storage_dir = self.config.profiles_dir / account_key
        storage_dir.mkdir(parents=True, exist_ok=True)

        # Sticky fingerprint: load if exists, otherwise generate + persist.
        fingerprint_file = storage_dir / "fingerprint.json"
        fp: dict[str, Any] = {}
        if fingerprint_file.exists():
            try:
                fp = json.loads(fingerprint_file.read_text())
            except Exception:
                fp = {}

        viewport = fp.get("viewport") or self.anti_detect.get_viewport()
        user_agent = fp.get("user_agent") or self.anti_detect.get_user_agent(account_key)

        # Proxy: prefer persisted (and still in pool); else sticky-assign.
        proxy = None
        proxy_dict = fp.get("proxy")
        if proxy_dict:
            candidate = self.anti_detect.get_proxy_by_dict(proxy_dict)
            if candidate and self.anti_detect.is_proxy_in_pool(candidate):
                proxy = candidate
            else:
                proxy = self.anti_detect.get_proxy_for_key(account_key)
        else:
            proxy = self.anti_detect.get_proxy_for_key(account_key)

        # Persist fingerprint (best effort — failure here is non-fatal).
        fp_save: dict[str, Any] = {"viewport": viewport, "user_agent": user_agent}
        if proxy:
            fp_save["proxy"] = {
                "protocol": proxy.protocol,
                "host": proxy.host,
                "port": proxy.port,
                "username": proxy.username,
                "password": proxy.password,
            }
        with contextlib.suppress(Exception):
            fingerprint_file.write_text(json.dumps(fp_save))

        state_file = storage_dir / "state.json"
        context_options: dict[str, Any] = {
            "viewport": viewport,
            "user_agent": user_agent,
            "storage_state": str(state_file) if state_file.exists() else None,
            "locale": "en-US",
            "timezone_id": self.config.timezone,
            "permissions": ["geolocation"],
            "color_scheme": "light",
        }
        if proxy:
            context_options["proxy"] = proxy.to_playwright_proxy()
        if extra_context_options:
            context_options.update(extra_context_options)
        context_options = {k: v for k, v in context_options.items() if v is not None}

        ctx = await self._browser.new_context(**context_options)
        await ctx.add_init_script(_ANTI_DETECT_INIT_JS)

        log.info(
            "Browser context created",
            account_key=account_key,
            viewport=f"{viewport['width']}x{viewport['height']}",
            has_proxy=proxy is not None,
        )
        return ctx

    async def get_page(self, account_key: str) -> Page:
        """Open a fresh page in the account's context, with sane timeouts."""
        ctx = await self.get_context(account_key)
        page = await ctx.new_page()
        page.set_default_timeout(30000)
        page.set_default_navigation_timeout(60000)
        return page

    async def save_state(self, account_key: str) -> None:
        """Persist cookies + localStorage for `account_key` to disk."""
        if account_key not in self._contexts:
            return
        storage_dir = self.config.profiles_dir / account_key
        storage_dir.mkdir(parents=True, exist_ok=True)
        state = await self._contexts[account_key].storage_state()
        (storage_dir / "state.json").write_text(json.dumps(state))
        log.info("Browser state saved", account_key=account_key)

    async def close_context(self, account_key: str) -> None:
        """Save state then close this account's context."""
        if account_key not in self._contexts:
            return
        try:
            await self.save_state(account_key)
            await self._contexts[account_key].close()
        except Exception as exc:
            log.error("Error closing context", account_key=account_key, error=str(exc))
        finally:
            del self._contexts[account_key]
            self._semaphore.release()

    # ----------------------------------------------------------- Cookie import

    async def import_cookies_to_profile(
        self,
        account_key: str,
        cookies_json: str | list[dict[str, Any]],
    ) -> bool:
        """Import cookies (e.g. exported from a browser extension) into a profile.

        Accepts either a JSON string or an already-parsed list of dicts.
        Each cookie may use either Chrome-extension (`expirationDate`) or
        Playwright (`expires`) field names — both are normalized.

        Any existing context for `account_key` is closed so it picks up the
        new state next time `get_context` is called.
        """
        try:
            cookies = (
                json.loads(cookies_json)
                if isinstance(cookies_json, str)
                else cookies_json
            )
        except (json.JSONDecodeError, TypeError) as exc:
            raise BrowserError(f"Invalid cookies JSON: {exc}") from exc

        if not cookies:
            raise BrowserError("Empty cookies list")

        storage_dir = self.config.profiles_dir / account_key
        storage_dir.mkdir(parents=True, exist_ok=True)
        state_file = storage_dir / "state.json"

        existing_state: dict[str, Any] = {"cookies": [], "origins": []}
        if state_file.exists():
            with contextlib.suppress(Exception):
                existing_state = json.loads(state_file.read_text())

        pw_cookies: list[dict[str, Any]] = []
        for c in cookies:
            raw_ss = str(c.get("sameSite", "None")).lower().strip()
            if raw_ss == "strict":
                same_site = "Strict"
            elif raw_ss == "lax":
                same_site = "Lax"
            else:
                same_site = "None"

            cookie: dict[str, Any] = {
                "name": c.get("name", ""),
                "value": c.get("value", ""),
                "domain": c.get("domain", ""),
                "path": c.get("path", "/"),
                "secure": c.get("secure", False),
                "httpOnly": c.get("httpOnly", False),
                "sameSite": same_site,
            }
            if c.get("expirationDate"):
                cookie["expires"] = c["expirationDate"]
            elif c.get("expires"):
                cookie["expires"] = c["expires"]
            pw_cookies.append(cookie)

        existing_state["cookies"] = pw_cookies
        state_file.write_text(json.dumps(existing_state))

        # Drop any existing context so next get_context() picks up new state.
        if account_key in self._contexts:
            with contextlib.suppress(Exception):
                await self._contexts[account_key].close()
            del self._contexts[account_key]
            self._semaphore.release()

        log.info(
            "Cookies imported to profile",
            account_key=account_key,
            cookie_count=len(pw_cookies),
        )
        return True

    # ----------------------------------------------------------------- Stats

    @property
    def active_contexts(self) -> int:
        """Number of currently-open browser contexts."""
        return len(self._contexts)

    @property
    def is_started(self) -> bool:
        """True iff Playwright + browser are launched."""
        return self._started
