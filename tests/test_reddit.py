"""Reddit client surface tests (no Playwright required)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from steadfast import AntiDetect, BrowserManager, BrowserManagerConfig
from steadfast.platforms import PostResult, Reddit
from steadfast.platforms.reddit import OLD_REDDIT_BASE, REDDIT_BASE

# ----------------------------------------------------------------- Ctor


@pytest.fixture
def bm(tmp_path: Path) -> BrowserManager:
    cfg = BrowserManagerConfig(profiles_dir=tmp_path / "profiles")
    return BrowserManager(cfg, AntiDetect())


def test_reddit_default_account_key(bm: BrowserManager):
    reddit = Reddit(bm)
    assert reddit.account_key == "reddit_primary"
    assert reddit.browser_manager is bm
    assert reddit._is_logged_in is False


def test_reddit_explicit_account_key(bm: BrowserManager):
    reddit = Reddit(bm, account_key="u/myname")
    assert reddit.account_key == "u/myname"


def test_all_three_platforms_can_share_browser_manager(bm: BrowserManager):
    """A single BrowserManager hosts contexts for Twitter, LinkedIn, AND Reddit."""
    from steadfast.platforms import LinkedIn, Twitter

    tw = Twitter(bm, account_key="my_tw")
    li = LinkedIn(bm, account_key="my_li")
    rd = Reddit(bm, account_key="my_rd")
    assert tw.browser_manager is li.browser_manager is rd.browser_manager
    assert len({tw.account_key, li.account_key, rd.account_key}) == 3


# ---------------------------------------------------------------- Constants


def test_canonical_base_urls():
    assert REDDIT_BASE == "https://www.reddit.com"
    assert OLD_REDDIT_BASE == "https://old.reddit.com"


# --------------------------------------------------------------- URL helpers


@pytest.mark.parametrize(
    "input_url, expected",
    [
        # Already old reddit — pass through
        ("https://old.reddit.com/r/python/comments/abc/", "https://old.reddit.com/r/python/comments/abc/"),
        # www.reddit.com → old.reddit.com
        ("https://www.reddit.com/r/python/comments/abc/", "https://old.reddit.com/r/python/comments/abc/"),
        # bare reddit.com → old.reddit.com
        ("https://reddit.com/r/python/comments/abc/", "https://old.reddit.com/r/python/comments/abc/"),
    ],
)
def test_to_old_reddit_url(input_url: str, expected: str):
    assert Reddit._to_old_reddit_url(input_url) == expected


@pytest.mark.parametrize(
    "input_url, expected",
    [
        ("https://www.reddit.com/r/python/comments/abc/", "https://www.reddit.com/r/python/comments/abc/"),
        ("https://old.reddit.com/r/python/comments/abc/", "https://www.reddit.com/r/python/comments/abc/"),
        ("https://reddit.com/r/python/comments/abc/", "https://www.reddit.com/r/python/comments/abc/"),
    ],
)
def test_to_new_reddit_url(input_url: str, expected: str):
    assert Reddit._to_new_reddit_url(input_url) == expected


def test_url_helpers_round_trip():
    """new -> old -> new should equal the original for a well-formed URL."""
    original = "https://www.reddit.com/r/python/comments/abc/hello/"
    assert (
        Reddit._to_new_reddit_url(Reddit._to_old_reddit_url(original)) == original
    )


# ------------------------------------------------------- Cookie import


@pytest.mark.asyncio
async def test_import_cookies_writes_state(bm: BrowserManager):
    reddit = Reddit(bm, account_key="my_reddit")
    cookies = [
        {
            "name": "reddit_session",
            "value": "AQEDB…",
            "domain": ".reddit.com",
            "path": "/",
            "secure": True,
            "httpOnly": True,
            "sameSite": "none",
        },
        {
            "name": "token_v2",
            "value": "eyJhbG…",
            "domain": ".reddit.com",
            "path": "/",
            "secure": True,
            "httpOnly": False,
            "sameSite": "lax",
        },
    ]
    ok = await reddit.import_cookies(cookies)
    assert ok is True

    state_file = bm.config.profiles_dir / "my_reddit" / "state.json"
    assert state_file.exists()
    state = json.loads(state_file.read_text())
    names = {c["name"] for c in state["cookies"]}
    assert "reddit_session" in names
    assert "token_v2" in names


@pytest.mark.asyncio
async def test_reddit_session_plus_token_v2_are_critical(bm: BrowserManager):
    """Document the 2026-05-29 finding: Reddit Comments needs BOTH cookies.

    A profile that had `reddit_session` (valid) but missing `token_v2`
    failed every comment with "Not logged in" because new-reddit endpoints
    require token_v2 for the bff API.
    """
    reddit = Reddit(bm, account_key="acct")
    # Import only reddit_session (the historically-broken case)
    await reddit.import_cookies(
        [{"name": "reddit_session", "value": "x", "domain": ".reddit.com", "path": "/"}]
    )
    state = json.loads(
        (bm.config.profiles_dir / "acct" / "state.json").read_text()
    )
    names = {c["name"] for c in state["cookies"]}
    assert "reddit_session" in names
    assert "token_v2" not in names  # THIS profile is broken for comments


@pytest.mark.asyncio
async def test_cross_account_isolation(bm: BrowserManager):
    alice = Reddit(bm, account_key="alice")
    await alice.import_cookies(
        [{"name": "reddit_session", "value": "1", "domain": ".reddit.com", "path": "/"}]
    )
    assert (bm.config.profiles_dir / "alice" / "state.json").exists()
    assert not (bm.config.profiles_dir / "bob").exists()


# --------------------------------------------------------- PostResult shape


def test_reddit_post_returns_postresult_dataclass():
    """Confirm the shared dataclass works for Reddit too."""
    r = PostResult(
        success=True,
        platform_post_id="abc123",
        url="https://old.reddit.com/r/python/comments/abc123/hello/",
    )
    assert r.success
    assert r.platform_post_id == "abc123"
    assert "/comments/" in r.url
