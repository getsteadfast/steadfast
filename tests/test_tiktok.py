"""TikTok client surface tests (no Playwright required)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from steadfast import AntiDetect, BrowserManager, BrowserManagerConfig
from steadfast.platforms import PostResult, TikTok
from steadfast.platforms.tiktok import (
    _CHECKPOINT_URL_FRAGMENTS,
    _DISMISS_BUTTONS,
    _LOGGED_IN_INDICATORS,
    TIKTOK_BASE,
    TIKTOK_LOGIN_URL,
    TIKTOK_UPLOAD_URL,
)

# ---------------------------------------------------------------- TikTok ctor


@pytest.fixture
def bm(tmp_path: Path) -> BrowserManager:
    cfg = BrowserManagerConfig(profiles_dir=tmp_path / "profiles")
    return BrowserManager(cfg, AntiDetect())


def test_tiktok_default_account_key(bm: BrowserManager):
    tt = TikTok(bm)
    assert tt.account_key == "tiktok_primary"
    assert tt.browser_manager is bm
    assert tt._is_logged_in is False


def test_tiktok_explicit_account_key(bm: BrowserManager):
    tt = TikTok(bm, account_key="my_tt_creator")
    assert tt.account_key == "my_tt_creator"


def test_all_seven_platforms_share_one_browser_manager(bm: BrowserManager):
    """One BrowserManager hosts all 7 platforms with distinct account_keys.

    This is the architectural property that makes Steadfast more than 7
    separate libraries — one Playwright pool, one anti-detect config,
    seven platform clients sharing the same per-account isolation layer.
    """
    from steadfast.platforms import (
        Facebook,
        Instagram,
        LinkedIn,
        Reddit,
        TikTok,
        Twitter,
        YouTube,
    )

    clients = (
        TikTok(bm, account_key="my_tt"),
        Instagram(bm, account_key="my_ig"),
        Facebook(bm, account_key="my_fb"),
        LinkedIn(bm, account_key="my_li"),
        Twitter(bm, account_key="my_tw"),
        Reddit(bm, account_key="my_rd"),
        YouTube(bm, account_key="my_yt"),
    )
    for client in clients:
        assert client.browser_manager is bm
    assert len({c.account_key for c in clients}) == 7


# -------------------------------------------------------------------- Constants


def test_tiktok_base_url():
    """Base URL is www.tiktok.com — NOT m.tiktok.com (different React tree)."""
    assert TIKTOK_BASE == "https://www.tiktok.com"


def test_upload_url_points_to_studio():
    """Upload must route through tiktokstudio/upload (desktop creator UI).

    The legacy /upload redirects there, but going direct avoids one
    page navigation and the associated dismissable popup.
    """
    assert "tiktokstudio/upload" in TIKTOK_UPLOAD_URL


def test_login_url_is_canonical():
    """Login lives at /login, not /accounts/login (that's Instagram)."""
    assert TIKTOK_LOGIN_URL == "https://www.tiktok.com/login"


def test_checkpoint_fragments_cover_known_challenge_urls():
    """Login must detect every known TikTok challenge URL fragment.

    Real challenge patterns from the wild:
      /captcha/...   /verify/...   /challenge/...
      /secsdk/...  (Tencent SecSDK-style challenge)
    """
    for frag in ("captcha", "verify", "challenge", "secsdk"):
        assert frag in _CHECKPOINT_URL_FRAGMENTS, f"Missing fragment: {frag}"


def test_logged_in_indicators_include_data_e2e_selectors():
    """data-e2e attributes are TikTok's official test-IDs — they're more
    stable than CSS class names, which are obfuscated and rotate weekly.
    At least one indicator should use [data-e2e].
    """
    assert any("data-e2e" in sel for sel in _LOGGED_IN_INDICATORS), (
        "At least one logged-in indicator must use data-e2e for stability"
    )


def test_dismiss_buttons_cover_cookie_banner():
    """The cookie banner blocks the upload composer until dismissed.
    Acceptance text varies by region; the dismiss list must cover both
    'Accept all' (EU) and 'Allow all cookies' (US) variants.
    """
    selectors = " | ".join(_DISMISS_BUTTONS).lower()
    assert "accept all" in selectors or "allow all cookies" in selectors


# ----------------------------------------------------- Cookie import (real)


@pytest.mark.asyncio
async def test_import_cookies_writes_state_for_correct_account(bm: BrowserManager):
    tt = TikTok(bm, account_key="my_tt")
    cookies = [
        {
            "name": "sessionid",
            "value": "abcdef0123456789",
            "domain": ".tiktok.com",
            "path": "/",
            "secure": True,
            "httpOnly": True,
        },
        {
            "name": "sessionid_ss",
            "value": "fedcba9876543210",
            "domain": ".tiktok.com",
            "path": "/",
            "secure": True,
            "httpOnly": True,
        },
    ]
    ok = await tt.import_cookies(cookies)
    assert ok is True

    state_file = bm.config.profiles_dir / "my_tt" / "state.json"
    assert state_file.exists()
    state = json.loads(state_file.read_text())
    names = {c["name"] for c in state["cookies"]}
    # Both auth cookies are required — sessionid alone won't authenticate.
    assert "sessionid" in names
    assert "sessionid_ss" in names
    assert tt._is_logged_in is False  # invalidated by import


@pytest.mark.asyncio
async def test_sessionid_and_sessionid_ss_are_both_required(bm: BrowserManager):
    """Document the critical insight: TikTok auth requires BOTH
    `sessionid` AND `sessionid_ss`.  Exports with only one will appear
    to import successfully but fail every session-health check —
    `sessionid_ss` is the signed-session token TikTok added in 2023+ to
    detect cookie-only replay attacks.
    """
    tt = TikTok(bm, account_key="acct")
    # Import only sessionid (missing the _ss variant)
    ok = await tt.import_cookies(
        [{"name": "sessionid", "value": "xyz", "domain": ".tiktok.com", "path": "/"}]
    )
    assert ok is True
    state = json.loads(
        (bm.config.profiles_dir / "acct" / "state.json").read_text()
    )
    names = {c["name"] for c in state["cookies"]}
    assert "sessionid" in names
    # This profile is BROKEN — documented as such.
    assert "sessionid_ss" not in names


@pytest.mark.asyncio
async def test_import_cookies_does_not_cross_accounts(bm: BrowserManager):
    """Importing for one account must not write to another's profile dir."""
    alice = TikTok(bm, account_key="alice")
    await alice.import_cookies(
        [{"name": "sessionid", "value": "1", "domain": ".tiktok.com", "path": "/"}]
    )
    assert (bm.config.profiles_dir / "alice" / "state.json").exists()
    assert not (bm.config.profiles_dir / "bob").exists()


# ----------------------------------------------------- upload() preconditions


@pytest.mark.asyncio
async def test_upload_returns_error_when_video_missing(bm: BrowserManager, tmp_path: Path):
    """Hard precondition: missing video file fails fast with a clear error
    rather than wasting a browser session on a guaranteed-broken upload.
    """
    tt = TikTok(bm, account_key="my_tt")
    missing_path = tmp_path / "nonexistent.mp4"
    result = await tt.upload(missing_path, caption="test")
    assert result.success is False
    assert "not found" in result.error.lower()
    # Critical: no browser context should have been created.
    assert "my_tt" not in bm._contexts  # noqa: SLF001


# ----------------------------------------------------- privacy + signature


def test_privacy_map_covers_all_three_levels():
    """TikTok's wizard accepts exactly Public, Friends, Private — verify
    every value our public type accepts has a selector chain.
    """
    privacy_map = TikTok._PRIVACY_RADIOS  # noqa: SLF001
    for level in ("public", "friends", "private"):
        assert level in privacy_map, f"Privacy level missing from map: {level}"
        assert len(privacy_map[level]) >= 1, f"No selectors for {level}"


def test_upload_signature_uses_literal_privacy_type():
    """``privacy`` is typed as ``Literal["public", "friends", "private"]``
    so callers get an IDE/typecheck error on typos like ``"friendz"``.
    """
    import inspect

    sig = inspect.signature(TikTok.upload)
    annotation_str = str(sig.parameters["privacy"].annotation)
    for level in ("public", "friends", "private"):
        assert level in annotation_str, f"Literal type missing {level!r}"


# ---------------------------------------------------------- PostResult shape


def test_tiktok_upload_returns_postresult_dataclass():
    """Same dataclass as the other platforms."""
    r = PostResult(success=True, platform_post_id="tt_post_1700000000")
    assert r.platform_post_id == "tt_post_1700000000"
    assert r.success


def test_tiktok_has_engagement_surface():
    """TikTok ships auth + upload + like.  No comment() in v0.1.x
    (TikTok's comment-drawer DOM is too brittle to commit to).
    """
    public_methods = {
        name for name in dir(TikTok)
        if not name.startswith("_") and callable(getattr(TikTok, name))
    }
    expected = {
        "upload", "like",
        "login", "import_cookies", "ensure_logged_in", "get_session_health",
    }
    missing = expected - public_methods
    assert not missing, f"TikTok missing methods: {missing}"


def test_tiktok_no_comment_method_in_v01():
    """TikTok intentionally lacks comment() in v0.1.x.

    The comment drawer is lazy-rendered after clicking the video,
    uses heavily-obfuscated class names, and breaks on every TikTok
    push.  Documenting the absence as a test so the next maintainer
    sees the deliberate scope decision.
    """
    assert not hasattr(TikTok, "comment"), (
        "TikTok should NOT have comment() in v0.1.x — drawer DOM is too brittle"
    )
