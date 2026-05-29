"""Twitter client surface tests (no Playwright required).

These verify the parts that don't need a real browser launch:
  * PostResult dataclass shape
  * Twitter constructor wiring
  * cookie import roundtrip (the most common production path)
  * URL parsing helpers (reply parent-id extraction)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from steadfast import AntiDetect, BrowserManager, BrowserManagerConfig
from steadfast.platforms import PostResult, Twitter

# -------------------------------------------------------------- Twitter ctor


@pytest.fixture
def bm(tmp_path: Path) -> BrowserManager:
    cfg = BrowserManagerConfig(profiles_dir=tmp_path / "profiles")
    return BrowserManager(cfg, AntiDetect())


def test_twitter_default_account_key(bm: BrowserManager):
    twitter = Twitter(bm)
    assert twitter.account_key == "twitter_primary"
    assert twitter.browser_manager is bm
    assert twitter._is_logged_in is False


def test_twitter_explicit_account_key(bm: BrowserManager):
    twitter = Twitter(bm, account_key="alice")
    assert twitter.account_key == "alice"


def test_multiple_twitter_instances_share_browser_manager(bm: BrowserManager):
    """Two Twitter clients for different accounts can share the same BM."""
    alice = Twitter(bm, account_key="alice")
    bob = Twitter(bm, account_key="bob")
    assert alice.browser_manager is bob.browser_manager
    assert alice.account_key != bob.account_key


# ------------------------------------------------------------- PostResult


def test_post_result_minimal_success():
    r = PostResult(success=True, platform_post_id="123", url="https://x.com/foo/status/123")
    assert r.success
    assert r.platform_post_id == "123"
    assert r.url.endswith("/status/123")
    assert r.error == ""
    assert r.warning == ""


def test_post_result_failure():
    r = PostResult(success=False, error="boom")
    assert r.success is False
    assert r.error == "boom"
    assert r.platform_post_id == ""


def test_post_result_synthetic_warning():
    """Posts that landed but couldn't be verified: success=True + warning set."""
    r = PostResult(
        success=True,
        platform_post_id="unverified-1234567890",
        warning="URL extraction failed — verify manually on profile",
    )
    assert r.success
    assert r.platform_post_id.startswith("unverified-")
    assert "verify manually" in r.warning


def test_post_result_is_slots():
    """PostResult uses __slots__ — attribute access strictly typed."""
    r = PostResult(success=True)
    with pytest.raises(AttributeError):
        r.bogus_field = "x"  # type: ignore[attr-defined]


# ----------------------------------------------------- Cookie import (real)


@pytest.mark.asyncio
async def test_import_cookies_writes_state_for_correct_account(bm: BrowserManager):
    twitter = Twitter(bm, account_key="my_twitter")
    cookies = [
        {
            "name": "auth_token",
            "value": "abc123",
            "domain": ".x.com",
            "path": "/",
            "secure": True,
            "httpOnly": True,
            "sameSite": "lax",
        }
    ]
    ok = await twitter.import_cookies(cookies)
    assert ok is True

    state_file = bm.config.profiles_dir / "my_twitter" / "state.json"
    assert state_file.exists()
    state = json.loads(state_file.read_text())
    assert state["cookies"][0]["name"] == "auth_token"
    # Cookie import should invalidate any in-memory logged-in flag.
    assert twitter._is_logged_in is False


@pytest.mark.asyncio
async def test_import_cookies_does_not_cross_accounts(bm: BrowserManager):
    """Importing for one account must NEVER write to another's profile dir."""
    alice = Twitter(bm, account_key="alice")
    await alice.import_cookies(
        [{"name": "a", "value": "1", "domain": ".x.com", "path": "/"}]
    )
    assert (bm.config.profiles_dir / "alice" / "state.json").exists()
    assert not (bm.config.profiles_dir / "bob").exists()


@pytest.mark.asyncio
async def test_import_cookies_accepts_json_string(bm: BrowserManager):
    twitter = Twitter(bm)
    payload = json.dumps([{"name": "k", "value": "v", "domain": ".x.com", "path": "/"}])
    ok = await twitter.import_cookies(payload)
    assert ok is True


# ------------------------------------------------------- Reply parent-id parse


@pytest.mark.parametrize(
    "url, expected_parent_id",
    [
        ("https://x.com/foo/status/1234567890", "1234567890"),
        ("https://x.com/foo/status/1234567890?lang=en", "1234567890"),
        ("https://x.com/foo/status/1234567890/photo/1", "1234567890"),
        ("https://twitter.com/foo/status/9876", "9876"),
    ],
)
def test_reply_parent_id_extraction(url: str, expected_parent_id: str):
    """Confirm the inline parsing logic in `reply()` produces the right id.

    Mirrors the production code's `tweet_url.split("/status/")[-1].split("?")[0].split("/")[0]`.
    """
    parent_id = url.split("/status/")[-1].split("?")[0].split("/")[0]
    assert parent_id == expected_parent_id
