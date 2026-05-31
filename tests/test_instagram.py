"""Instagram client surface tests (no Playwright required)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from steadfast import AntiDetect, BrowserManager, BrowserManagerConfig
from steadfast.platforms import Instagram, PostResult
from steadfast.platforms.instagram import (
    _CHECKPOINT_URL_FRAGMENTS,
    _DISMISS_BUTTONS,
    _LOGGED_IN_INDICATORS,
    INSTAGRAM_BASE,
)

# -------------------------------------------------------------- Instagram ctor


@pytest.fixture
def bm(tmp_path: Path) -> BrowserManager:
    cfg = BrowserManagerConfig(profiles_dir=tmp_path / "profiles")
    return BrowserManager(cfg, AntiDetect())


def test_instagram_default_account_key(bm: BrowserManager):
    ig = Instagram(bm)
    assert ig.account_key == "instagram_primary"
    assert ig.browser_manager is bm
    assert ig._is_logged_in is False


def test_instagram_explicit_account_key(bm: BrowserManager):
    ig = Instagram(bm, account_key="my_ig")
    assert ig.account_key == "my_ig"


def test_all_six_platforms_can_share_one_browser_manager(bm: BrowserManager):
    """Single BrowserManager hosts all 6 platforms simultaneously, distinct keys."""
    from steadfast.platforms import (
        Facebook,
        Instagram,
        LinkedIn,
        Reddit,
        Twitter,
        YouTube,
    )

    ig = Instagram(bm, account_key="my_ig")
    fb = Facebook(bm, account_key="my_fb")
    li = LinkedIn(bm, account_key="my_li")
    tw = Twitter(bm, account_key="my_tw")
    rd = Reddit(bm, account_key="my_rd")
    yt = YouTube(bm, account_key="my_yt")
    all_clients = (ig, fb, li, tw, rd, yt)
    # Same browser manager backs every platform.
    for client in all_clients:
        assert client.browser_manager is bm
    # Account keys are all distinct.
    keys = {client.account_key for client in all_clients}
    assert len(keys) == 6


# -------------------------------------------------------------------- Constants


def test_instagram_base_url():
    """Base URL is www.instagram.com — NOT m.instagram.com (different React tree)."""
    assert INSTAGRAM_BASE == "https://www.instagram.com"


def test_checkpoint_fragments_cover_challenges():
    """Login flow must detect every known Instagram challenge fragment.

    Real challenge URLs from the wild:
      /challenge/?next=...
      /accounts/suspended/
      /accounts/two_factor/...
    """
    for frag in ("challenge", "suspicious", "two_factor", "verify"):
        assert frag in _CHECKPOINT_URL_FRAGMENTS, f"Missing fragment: {frag}"


def test_logged_in_indicators_include_canonical_home_svg():
    """``svg[aria-label="Home"]`` is the canonical logged-in signal —
    it ONLY renders in the navbar when an authenticated session exists.

    If this selector drifts out of the indicator list during a refactor,
    session-health detection will quietly degrade.
    """
    assert any('aria-label="Home"' in sel for sel in _LOGGED_IN_INDICATORS), (
        "Home navbar svg must be among the logged-in indicators"
    )


def test_dismiss_buttons_cover_three_modal_classes():
    """Three classes of nag modal must be dismissable: cookies, save-login,
    notifications.  Each shows up at different points in the navigation
    flow, so each must have at least one selector in the dismiss list.
    """
    selectors = " | ".join(_DISMISS_BUTTONS).lower()
    assert "cookies" in selectors, "No cookie-banner dismiss selector"
    assert "save info" in selectors, "No Save Login Info dismiss selector"
    assert "not now" in selectors, "No Notifications-permission dismiss selector"


# ----------------------------------------------------- Cookie import (real)


@pytest.mark.asyncio
async def test_import_cookies_writes_state_for_correct_account(bm: BrowserManager):
    ig = Instagram(bm, account_key="my_ig")
    cookies = [
        {
            "name": "sessionid",
            "value": "12345%3Aabcdefghij%3A0",
            "domain": ".instagram.com",
            "path": "/",
            "secure": True,
            "httpOnly": True,
        },
        {
            "name": "ds_user_id",
            "value": "100012345678",
            "domain": ".instagram.com",
            "path": "/",
            "secure": True,
        },
    ]
    ok = await ig.import_cookies(cookies)
    assert ok is True

    state_file = bm.config.profiles_dir / "my_ig" / "state.json"
    assert state_file.exists()
    state = json.loads(state_file.read_text())
    names = {c["name"] for c in state["cookies"]}
    # sessionid is the ONLY cookie Instagram actually uses to verify auth.
    assert "sessionid" in names
    assert ig._is_logged_in is False  # invalidated by import


@pytest.mark.asyncio
async def test_sessionid_is_the_only_cookie_that_matters(bm: BrowserManager):
    """Document the critical insight: Instagram auth lives in ``sessionid``.

    A cookie export missing sessionid will appear to import successfully
    (we don't validate cookie names — that's a runtime concern), but every
    session-health check will fail.  This test documents that pattern.
    """
    ig = Instagram(bm, account_key="acct")
    ok = await ig.import_cookies(
        [{"name": "csrftoken", "value": "abc", "domain": ".instagram.com", "path": "/"}]
    )
    assert ok is True
    state = json.loads(
        (bm.config.profiles_dir / "acct" / "state.json").read_text()
    )
    names = {c["name"] for c in state["cookies"]}
    # This profile is missing sessionid — documented as broken.
    assert "sessionid" not in names


@pytest.mark.asyncio
async def test_import_cookies_does_not_cross_accounts(bm: BrowserManager):
    """Importing for one account must not write to another's profile dir."""
    alice = Instagram(bm, account_key="alice")
    await alice.import_cookies(
        [{"name": "sessionid", "value": "1", "domain": ".instagram.com", "path": "/"}]
    )
    assert (bm.config.profiles_dir / "alice" / "state.json").exists()
    assert not (bm.config.profiles_dir / "bob").exists()


# ---------------------------------------------------------- post() preconditions


@pytest.mark.asyncio
async def test_post_returns_error_when_image_missing(bm: BrowserManager, tmp_path: Path):
    """Hard precondition: missing image file fails fast with a clear error
    rather than wasting a browser session on a guaranteed-broken upload.
    """
    ig = Instagram(bm, account_key="my_ig")
    missing_path = tmp_path / "nonexistent.jpg"
    result = await ig.post(missing_path, caption="test")
    assert result.success is False
    assert "not found" in result.error.lower()
    # Critical: no browser context should have been created.
    assert "my_ig" not in bm._contexts  # noqa: SLF001


# ---------------------------------------------------------- PostResult shape


def test_instagram_post_returns_postresult_dataclass():
    """Same dataclass as the other platforms."""
    r = PostResult(success=True, platform_post_id="ig_post_1700000000")
    assert r.platform_post_id == "ig_post_1700000000"
    assert r.success


def test_instagram_has_full_engagement_surface():
    """Instagram ships the full v0.1.0+ surface: auth + post + comment + like."""
    public_methods = {
        name for name in dir(Instagram)
        if not name.startswith("_") and callable(getattr(Instagram, name))
    }
    expected = {
        "post", "comment", "like",
        "login", "import_cookies", "ensure_logged_in", "get_session_health",
    }
    missing = expected - public_methods
    assert not missing, f"Instagram missing methods: {missing}"
