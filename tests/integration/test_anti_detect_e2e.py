"""End-to-end smoke test for the anti-detect init script.

Launches a real Playwright chromium context via ``BrowserManager``, loads an
embedded HTML page that probes every property we override, and asserts the
override actually fired in-browser.

Marked ``@pytest.mark.integration`` so the default ``pytest`` run skips it.
Run explicitly with::

    pytest -m integration tests/integration/

Skips automatically if Playwright's chromium binary isn't installed.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from steadfast import AntiDetect, BrowserManager, BrowserManagerConfig

pytestmark = pytest.mark.integration

# Probe page: runs every override check, dumps results into <pre id="result">
# as JSON.  We read that pre's textContent and assert against the expected
# constants set in `_ANTI_DETECT_INIT_JS`.
_PROBE_HTML = """<!doctype html>
<html><body><pre id="result">pending</pre>
<script>
(async () => {
    const r = {};
    r.webdriver = navigator.webdriver === undefined ? 'undefined' : String(navigator.webdriver);
    r.has_chrome = typeof window.chrome === 'object' && window.chrome !== null;
    r.chrome_has_runtime = !!(window.chrome && window.chrome.runtime);
    r.plugins_len = navigator.plugins.length;
    r.plugin_names = Array.from(navigator.plugins).map(p => p.name);
    r.languages = Array.from(navigator.languages);
    r.max_touch_points = navigator.maxTouchPoints;
    r.hardware_concurrency = navigator.hardwareConcurrency;
    r.device_memory = navigator.deviceMemory;
    if (navigator.connection) {
        r.connection_rtt = navigator.connection.rtt;
    } else {
        r.connection_rtt = 'no-connection';
    }
    // WebGL — only meaningful if a context is available.
    try {
        const canvas = document.createElement('canvas');
        const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
        if (gl) {
            // UNMASKED_VENDOR_WEBGL = 0x9245, UNMASKED_RENDERER_WEBGL = 0x9246
            r.webgl_vendor = gl.getParameter(0x9245);
            r.webgl_renderer = gl.getParameter(0x9246);
        } else {
            r.webgl_vendor = 'no-gl';
            r.webgl_renderer = 'no-gl';
        }
    } catch (e) {
        r.webgl_vendor = 'error:' + e.message;
        r.webgl_renderer = 'error:' + e.message;
    }
    // permissions.query for notifications should resolve, not reject.
    try {
        const p = await navigator.permissions.query({name: 'notifications'});
        r.notifications_state = p.state;
    } catch (e) {
        r.notifications_state = 'error:' + e.message;
    }
    document.getElementById('result').textContent = JSON.stringify(r);
})();
</script></body></html>"""


def _chromium_available() -> bool:
    """Return True iff Playwright's chromium binary is present locally."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return False

    async def _check() -> bool:
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                await browser.close()
            return True
        except Exception:
            return False

    return asyncio.run(_check())


# Module-level skip if chromium isn't installed — avoids a slow failure when
# someone runs the integration suite in a clean env.
if not _chromium_available():
    pytest.skip("Playwright chromium not installed", allow_module_level=True)


@pytest.fixture
async def bm(tmp_path: Path):
    """Fresh BrowserManager backed by a tmp profiles dir.  Tears down cleanly."""
    config = BrowserManagerConfig(profiles_dir=tmp_path / "profiles", headless=True)
    manager = BrowserManager(config, AntiDetect())
    await manager.start()
    try:
        yield manager
    finally:
        await manager.shutdown()


async def _read_probe_result(page) -> dict:  # type: ignore[no-untyped-def]
    """Render the probe page via set_content and return the parsed result dict.

    ``set_content`` is used instead of a ``data:`` URL because the probe HTML
    contains characters that would need escaping in a URI, and ``set_content``
    still triggers the context's init script (which runs on every navigation,
    including the synthetic ``about:blank`` that ``set_content`` produces).
    """
    await page.set_content(_PROBE_HTML, wait_until="domcontentloaded")
    raw = await page.wait_for_function(
        "() => { const el = document.getElementById('result');"
        "        return el && el.textContent && el.textContent !== 'pending' ? el.textContent : null; }",
        timeout=10_000,
    )
    text = await raw.json_value()
    return json.loads(text)


async def test_init_script_patches_apply(bm: BrowserManager) -> None:
    """All anti-detect overrides take effect on the FIRST page of a NEW context."""
    ctx = await bm.get_context("probe_a")
    page = await ctx.new_page()
    try:
        result = await _read_probe_result(page)
    finally:
        await page.close()

    # ── navigator-API surface ────────────────────────────────────────────
    assert result["webdriver"] == "undefined", (
        f"navigator.webdriver leaked: {result['webdriver']}"
    )
    assert result["has_chrome"] is True, "window.chrome shim missing"
    assert result["chrome_has_runtime"] is True, "window.chrome.runtime missing"
    assert result["plugins_len"] == 3, f"plugins.length={result['plugins_len']} (expected 3)"
    assert "Chrome PDF Plugin" in result["plugin_names"]
    assert result["languages"] == ["en-US", "en"], f"languages={result['languages']}"
    assert result["max_touch_points"] == 0
    assert result["hardware_concurrency"] == 8, (
        f"hardwareConcurrency={result['hardware_concurrency']} (expected 8)"
    )
    assert result["device_memory"] == 8, (
        f"deviceMemory={result['device_memory']} (expected 8)"
    )

    # connection.rtt: real Chromium sometimes lacks navigator.connection.
    # If present, our override pins it to 50; if absent, that's also fine.
    if result["connection_rtt"] != "no-connection":
        assert result["connection_rtt"] == 50

    # ── WebGL vendor + renderer override ─────────────────────────────────
    # Headless chromium-headless-shell may not expose a WebGL context at all
    # (no GPU + no SwiftShader fallback in the headless shell build). When
    # there's no GL, our patch can't be exercised — accept that as a soft
    # pass and log so the test isn't silently weakened.
    if result["webgl_vendor"] == "no-gl":
        pytest.skip("WebGL context unavailable in headless shell — override path unexercised")
    assert result["webgl_vendor"] == "Intel Inc.", (
        f"WebGL vendor leaked: {result['webgl_vendor']}"
    )
    assert result["webgl_renderer"] == "Intel Iris OpenGL Engine", (
        f"WebGL renderer leaked: {result['webgl_renderer']}"
    )


async def test_permissions_query_resolves(bm: BrowserManager) -> None:
    """The permissions.query override returns Notification.permission, not error."""
    ctx = await bm.get_context("probe_b")
    page = await ctx.new_page()
    try:
        result = await _read_probe_result(page)
    finally:
        await page.close()

    # State should be one of the standard permission states, not an error.
    assert result["notifications_state"] in ("default", "granted", "denied"), (
        f"permissions.query failed or returned unexpected state: {result['notifications_state']}"
    )


async def test_init_script_persists_across_pages(bm: BrowserManager) -> None:
    """Overrides hold on a SECOND page of the SAME context (init script reused)."""
    ctx = await bm.get_context("probe_c")
    page1 = await ctx.new_page()
    page2 = await ctx.new_page()
    try:
        r1 = await _read_probe_result(page1)
        r2 = await _read_probe_result(page2)
    finally:
        await page1.close()
        await page2.close()

    for r in (r1, r2):
        assert r["webdriver"] == "undefined"
        assert r["hardware_concurrency"] == 8
