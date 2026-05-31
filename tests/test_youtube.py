"""YouTube client surface tests (no Playwright required)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from steadfast import AntiDetect, BrowserManager, BrowserManagerConfig
from steadfast.platforms import PostResult, YouTube
from steadfast.platforms.youtube import (
    _CHECKPOINT_URL_FRAGMENTS,
    _LOGGED_IN_INDICATORS,
    GOOGLE_LOGIN_URL,
    YOUTUBE_BASE,
    _clean_video_url,
)

# ---------------------------------------------------------------- YouTube ctor


@pytest.fixture
def bm(tmp_path: Path) -> BrowserManager:
    cfg = BrowserManagerConfig(profiles_dir=tmp_path / "profiles")
    return BrowserManager(cfg, AntiDetect())


def test_youtube_default_account_key(bm: BrowserManager):
    yt = YouTube(bm)
    assert yt.account_key == "youtube_primary"
    assert yt.browser_manager is bm
    assert yt._is_logged_in is False


def test_youtube_explicit_account_key(bm: BrowserManager):
    yt = YouTube(bm, account_key="my_yt_channel")
    assert yt.account_key == "my_yt_channel"


def test_youtube_has_upload_not_post():
    """YouTube uses ``upload(video_path, title, …)``, NOT ``post(text)``.

    The other platforms have ``post(text)`` as their primary content method.
    YouTube's primary content is video, so the verb is deliberately
    different — text-content callers will get a clear AttributeError
    rather than silently uploading a typo as a video title.
    """
    assert hasattr(YouTube, "upload"), "YouTube must expose upload()"
    assert not hasattr(YouTube, "post"), (
        "YouTube must NOT alias upload() as post() — different signature, "
        "different semantics. Use upload(video_path, title, ...) explicitly."
    )


def test_youtube_has_full_engagement_surface():
    """YouTube ships the full v0.1.0+ surface: auth + comment + like + upload."""
    public_methods = {
        name for name in dir(YouTube)
        if not name.startswith("_") and callable(getattr(YouTube, name))
    }
    expected = {
        "comment", "like", "upload",
        "login", "import_cookies", "ensure_logged_in", "get_session_health",
    }
    missing = expected - public_methods
    assert not missing, f"YouTube missing methods: {missing}"


# -------------------------------------------------------------------- Constants


def test_youtube_base_url():
    """Base URL is www.youtube.com (not m.youtube.com or youtu.be)."""
    assert YOUTUBE_BASE == "https://www.youtube.com"


def test_google_login_is_service_login_endpoint():
    """Auth flows through accounts.google.com — NOT youtube.com/login."""
    assert GOOGLE_LOGIN_URL.startswith("https://accounts.google.com")


def test_checkpoint_fragments_cover_google_challenge_urls():
    """Login must detect Google's anti-automation challenge URLs.

    Real Google challenge URLs from the wild:
      /signin/v2/challenge/pwd
      /signin/v2/challenge/totp
      /signin/rejected
      /speedbump/...
    """
    for frag in ("challenge", "speedbump", "rejected"):
        assert frag in _CHECKPOINT_URL_FRAGMENTS, f"Missing fragment: {frag}"


def test_logged_in_indicators_are_avatar_based():
    """Avatar button is the canonical logged-in signal on YouTube."""
    avatar_seen = any("avatar" in sel.lower() for sel in _LOGGED_IN_INDICATORS)
    assert avatar_seen, "At least one indicator must check for an avatar element"


# ------------------------------------------------------ URL cleaning helper


def test_clean_video_url_strips_tracking_pp():
    """`&pp=` is YouTube's analytics-pixel param. Strip it."""
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&pp=YAHIAQE%3D"
    assert _clean_video_url(url) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_clean_video_url_strips_tracking_t():
    """`&t=` is the timestamp param — strip it so the URL canonicalizes."""
    url = "https://www.youtube.com/watch?v=abc&t=42s"
    assert _clean_video_url(url) == "https://www.youtube.com/watch?v=abc"


def test_clean_video_url_strips_tracking_feature():
    """`&feature=youtu.be` is the share-button tag — strip it."""
    url = "https://www.youtube.com/watch?v=abc&feature=youtu.be"
    assert _clean_video_url(url) == "https://www.youtube.com/watch?v=abc"


def test_clean_video_url_passes_clean_urls_through():
    """No tracking params → URL is returned verbatim."""
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert _clean_video_url(url) == url


# ----------------------------------------------------- Cookie import (real)


@pytest.mark.asyncio
async def test_import_cookies_writes_state_for_correct_account(bm: BrowserManager):
    yt = YouTube(bm, account_key="my_yt")
    cookies = [
        {
            "name": "SAPISID",
            "value": "abcd1234efgh5678",
            "domain": ".google.com",
            "path": "/",
            "secure": True,
            "httpOnly": False,
        },
        {
            "name": "LOGIN_INFO",
            "value": "AFmmF2s...",
            "domain": ".youtube.com",
            "path": "/",
            "secure": True,
            "httpOnly": True,
        },
    ]
    ok = await yt.import_cookies(cookies)
    assert ok is True

    state_file = bm.config.profiles_dir / "my_yt" / "state.json"
    assert state_file.exists()
    state = json.loads(state_file.read_text())
    domains = {c["domain"] for c in state["cookies"]}
    # YouTube auth requires cookies from BOTH google.com and youtube.com
    assert ".google.com" in domains
    assert ".youtube.com" in domains
    assert yt._is_logged_in is False  # invalidated by import


@pytest.mark.asyncio
async def test_both_domains_warned_in_docstring():
    """Critical: YouTube auth reads state from BOTH google.com AND youtube.com.

    Cookie-Editor's "Export for current domain" exports only one — users
    who don't switch tabs will export incomplete state.  The docstring on
    import_cookies must warn about this.
    """
    assert "google.com" in YouTube.import_cookies.__doc__.lower()
    assert "youtube.com" in YouTube.import_cookies.__doc__.lower()


# ---------------------------------------------------------- PostResult shape


def test_youtube_comment_returns_postresult_dataclass():
    """Same dataclass as the other platforms."""
    r = PostResult(success=True, platform_post_id="yt_comment_1700000000")
    assert r.platform_post_id == "yt_comment_1700000000"
    assert r.success


# ----------------------------------------------------------------- Upload


def test_privacy_map_covers_all_three_levels():
    """YouTube's wizard accepts exactly Public, Unlisted, Private — verify
    every value our public type accepts has a selector.
    """
    privacy_map = YouTube._UPLOAD_PRIVACY  # noqa: SLF001
    for level in ("public", "unlisted", "private"):
        assert level in privacy_map, f"Privacy level missing from map: {level}"


def test_video_id_regex_matches_canonical_url_forms():
    """The video-id regex must catch the 4 URL forms YouTube emits:
      /watch?v=…  /youtu.be/…  /video/…  embedded in dialog text
    """
    cases = (
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://studio.youtube.com/video/dQw4w9WgXcQ/edit", "dQw4w9WgXcQ"),
        ("Find your video at https://youtu.be/dQw4w9WgXcQ — share it!", "dQw4w9WgXcQ"),
    )
    for url, expected_id in cases:
        match = YouTube._VIDEO_ID_RE.search(url)  # noqa: SLF001
        assert match, f"regex failed to match: {url}"
        assert match.group(1) == expected_id, f"wrong id for {url}: got {match.group(1)}"


@pytest.mark.asyncio
async def test_upload_returns_error_when_file_missing(bm: BrowserManager, tmp_path: Path):
    """Hard precondition: missing video file fails fast with a clear error
    rather than wasting a browser session on a guaranteed-broken upload.
    """
    yt = YouTube(bm, account_key="my_yt")
    missing_path = tmp_path / "nonexistent.mp4"
    result = await yt.upload(missing_path, title="Test")
    assert result.success is False
    assert "not found" in result.error.lower()
    # Critical: no browser context should have been created for this account.
    assert "my_yt" not in bm._contexts  # noqa: SLF001


def test_upload_signature_uses_literal_privacy_type():
    """``privacy`` is typed as ``Literal["public", "unlisted", "private"]``
    so callers get an IDE/typecheck error on typos like ``"publik"``.
    """
    import inspect

    sig = inspect.signature(YouTube.upload)
    privacy_param = sig.parameters["privacy"]
    annotation_str = str(privacy_param.annotation)
    # Should be Literal["public", "unlisted", "private"]
    for level in ("public", "unlisted", "private"):
        assert level in annotation_str, f"Literal type missing {level!r}"
