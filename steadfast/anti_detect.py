"""Anti-detection measures for browser automation.

Stateless, file-free, no MarketPilot/Settings dependency. Construct an
AntiDetect with an optional list of ProxyInfo and user agents, OR call the
classmethods directly for one-off operations.
"""

from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._log import get_logger

log = get_logger("steadfast.anti_detect")


# Common screen resolutions seen in the wild.
COMMON_VIEWPORTS: list[dict[str, int]] = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 720},
    {"width": 1600, "height": 900},
]


DEFAULT_USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) "
    "Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


@dataclass
class ProxyInfo:
    """Connection details for an outbound HTTP/SOCKS proxy."""

    protocol: str
    host: str
    port: int
    username: str = ""
    password: str = ""

    @property
    def url(self) -> str:
        """Return the full proxy URL, e.g. `http://user:pass@host:port`."""
        auth = f"{self.username}:{self.password}@" if self.username else ""
        return f"{self.protocol}://{auth}{self.host}:{self.port}"

    def to_playwright_proxy(self) -> dict[str, str]:
        """Convert to the dict Playwright's `context_options['proxy']` wants."""
        out: dict[str, str] = {"server": f"{self.protocol}://{self.host}:{self.port}"}
        if self.username:
            out["username"] = self.username
            out["password"] = self.password
        return out

    @classmethod
    def parse(cls, line: str) -> ProxyInfo:
        """Parse a proxy line: `protocol://user:pass@host:port` or `host:port`."""
        line = line.strip()
        if "://" in line:
            protocol, rest = line.split("://", 1)
        else:
            protocol, rest = "http", line

        username, password = "", ""
        if "@" in rest:
            auth, rest = rest.rsplit("@", 1)
            if ":" in auth:
                username, password = auth.split(":", 1)

        host, port = rest.rsplit(":", 1)
        return cls(protocol, host, int(port), username, password)


@dataclass
class AntiDetect:
    """Centralized anti-detection state and helpers.

    Construct with optional proxies + user agents. All inputs are plain
    Python data — no file paths or env vars required.
    """

    proxies: list[ProxyInfo] = field(default_factory=list)
    user_agents: list[str] = field(default_factory=lambda: list(DEFAULT_USER_AGENTS))
    # Sticky assignments so repeat calls return the same fingerprint.
    _proxy_assignments: dict[str, ProxyInfo] = field(default_factory=dict)
    _ua_assignments: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_proxy_file(cls, path: Path | str) -> AntiDetect:
        """Build by parsing a text file of proxies (one per line, `#` comments)."""
        p = Path(path)
        proxies: list[ProxyInfo] = []
        if p.exists():
            for raw in p.read_text().splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                proxies.append(ProxyInfo.parse(line))
        return cls(proxies=proxies)

    # ------------------------------------------------------------------ Delays

    async def random_delay(self, min_sec: float = 1.0, max_sec: float = 5.0) -> None:
        """Human-like delay, Gaussian-clamped to [min_sec, max_sec]."""
        mean = (min_sec + max_sec) / 2
        std = (max_sec - min_sec) / 4
        delay = max(min_sec, min(max_sec, random.gauss(mean, std)))
        await asyncio.sleep(delay)

    async def typing_delay(self, text_length: int) -> None:
        """Simulate human typing speed (~40-80 WPM)."""
        wpm = random.uniform(40, 80)
        chars_per_second = (wpm * 5) / 60
        base_time = text_length / chars_per_second
        actual = base_time * random.uniform(0.8, 1.3)
        await asyncio.sleep(min(actual, 30))

    async def short_pause(self) -> None:
        """0.3-1.5s between-action pause."""
        await asyncio.sleep(random.uniform(0.3, 1.5))

    async def page_read_delay(self, content_length: int = 500) -> None:
        """Simulate page-reading time (200-300 WPM)."""
        wpm = random.uniform(200, 300)
        words = content_length / 5
        read_time = (words / wpm) * 60
        await asyncio.sleep(min(read_time, 15))

    # ----------------------------------------------------------------- Identity

    def get_proxy_for_key(self, account_key: str) -> ProxyInfo | None:
        """Return a proxy sticky-assigned to `account_key`.

        Deterministic per (account_key, current proxy pool).  Returns None
        when no proxies are configured.
        """
        if not self.proxies:
            return None
        if account_key not in self._proxy_assignments:
            idx = hash(account_key) % len(self.proxies)
            self._proxy_assignments[account_key] = self.proxies[idx]
        return self._proxy_assignments[account_key]

    def get_proxy_by_dict(self, proxy_dict: dict[str, Any] | None) -> ProxyInfo | None:
        """Reconstruct a ProxyInfo from a serialized fingerprint dict."""
        if not proxy_dict:
            return None
        return ProxyInfo(
            protocol=proxy_dict.get("protocol", "http"),
            host=proxy_dict["host"],
            port=int(proxy_dict["port"]),
            username=proxy_dict.get("username", ""),
            password=proxy_dict.get("password", ""),
        )

    def is_proxy_in_pool(self, proxy: ProxyInfo) -> bool:
        """True if `proxy`'s host:port matches one in the configured pool."""
        return any(p.host == proxy.host and p.port == proxy.port for p in self.proxies)

    def get_user_agent(self, account_key: str | None = None) -> str:
        """Return a sticky user-agent for the given `account_key`."""
        if account_key is None:
            return random.choice(self.user_agents)
        if account_key not in self._ua_assignments:
            idx = hash(account_key) % len(self.user_agents)
            self._ua_assignments[account_key] = self.user_agents[idx]
        return self._ua_assignments[account_key]

    def get_viewport(self) -> dict[str, int]:
        """Return a random viewport size from COMMON_VIEWPORTS."""
        return random.choice(COMMON_VIEWPORTS).copy()

    # ------------------------------------------------------------ Proxy health

    async def check_proxy_health(self, proxy: ProxyInfo, timeout: float = 10.0) -> bool:
        """Probe a proxy via httpbin.org/ip. True iff it returns HTTP 200."""
        # Import lazily so aiohttp stays optional.
        try:
            # The unused-ignore silences mypy when [health] IS installed
            # (CI scenario). The import-not-found silences mypy when it
            # isn't (the lazy-import scenario this whole try/except exists for).
            import aiohttp  # type: ignore[import-not-found, unused-ignore]
        except ImportError as exc:
            raise RuntimeError(
                "Proxy health checks require `aiohttp`. "
                "Install with `pip install steadfast[health]` or add aiohttp."
            ) from exc

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as session, session.get(
                "https://httpbin.org/ip", proxy=proxy.url
            ) as resp:
                return bool(resp.status == 200)
        except Exception:
            return False

    async def check_all_proxies(self) -> dict[str, bool]:
        """Probe every proxy and return `{"host:port": healthy_bool}`."""
        if not self.proxies:
            return {}
        results: dict[str, bool] = {}
        for proxy in self.proxies:
            key = f"{proxy.host}:{proxy.port}"
            results[key] = await self.check_proxy_health(proxy)
        healthy = sum(1 for v in results.values() if v)
        log.info(
            "Proxy health check",
            total=len(results),
            healthy=healthy,
            dead=len(results) - healthy,
        )
        return results

    def replace_dead_proxy(
        self,
        account_key: str,
        dead_proxy: ProxyInfo,
        fingerprint_path: Path,
    ) -> ProxyInfo | None:
        """Swap a dead proxy for a healthy one and persist to fingerprint.json.

        Returns None when no alternative exists.
        """
        alternatives = [
            p for p in self.proxies
            if not (p.host == dead_proxy.host and p.port == dead_proxy.port)
        ]
        if not alternatives:
            return None

        new_proxy = random.choice(alternatives)
        self._proxy_assignments[account_key] = new_proxy

        try:
            fp_data: dict[str, Any] = {}
            if fingerprint_path.exists():
                fp_data = json.loads(fingerprint_path.read_text())
            fp_data["proxy"] = {
                "protocol": new_proxy.protocol,
                "host": new_proxy.host,
                "port": new_proxy.port,
                "username": new_proxy.username,
                "password": new_proxy.password,
            }
            fingerprint_path.write_text(json.dumps(fp_data, indent=2))
        except Exception as exc:
            log.error("Failed to update fingerprint proxy", error=str(exc))

        return new_proxy

    # ----------------------------------------------------------- Behavior helpers

    async def human_like_scroll(self, page: object) -> None:  # Playwright Page
        """Scroll in 3-8 small steps with small variance."""
        scroll_amount = random.randint(200, 600)
        steps = random.randint(3, 8)
        for _ in range(steps):
            await page.mouse.wheel(0, scroll_amount + random.randint(-100, 100))  # type: ignore[attr-defined]
            await asyncio.sleep(random.uniform(0.3, 1.0))

    async def human_like_click(self, page: object, selector: str) -> None:
        """Click at a randomized point inside the element's bounding box."""
        element = await page.wait_for_selector(selector, timeout=10000)  # type: ignore[attr-defined]
        if not element:
            return
        box = await element.bounding_box()
        if not box:
            await element.click()
            return
        x = box["x"] + random.uniform(box["width"] * 0.2, box["width"] * 0.8)
        y = box["y"] + random.uniform(box["height"] * 0.2, box["height"] * 0.8)
        await page.mouse.move(x, y, steps=random.randint(5, 15))  # type: ignore[attr-defined]
        await asyncio.sleep(random.uniform(0.05, 0.2))
        await page.mouse.click(x, y)  # type: ignore[attr-defined]

    async def human_like_type(self, page: object, selector: str, text: str) -> None:
        """Type with per-character random delay; occasional thinking pause."""
        element = await page.wait_for_selector(selector, timeout=10000)  # type: ignore[attr-defined]
        if not element:
            return
        await element.click()
        await asyncio.sleep(random.uniform(0.1, 0.3))
        for char in text:
            await element.type(char, delay=random.randint(30, 150))
            if random.random() < 0.05:
                await asyncio.sleep(random.uniform(0.3, 0.8))

    async def random_mouse_movement(self, page: object) -> None:
        """Move mouse to a random interior point (idle behavior)."""
        viewport = page.viewport_size or {"width": 1920, "height": 1080}  # type: ignore[attr-defined]
        x = random.randint(100, viewport["width"] - 100)
        y = random.randint(100, viewport["height"] - 100)
        await page.mouse.move(x, y, steps=random.randint(10, 30))  # type: ignore[attr-defined]
