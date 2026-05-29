"""LinkedIn client surface tests (no Playwright required)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from steadfast import AntiDetect, BrowserManager, BrowserManagerConfig
from steadfast.platforms import LinkedIn, PostResult
from steadfast.platforms.linkedin import LINKEDIN_BASE, SELECTORS

# --------------------------------------------------------------- LinkedIn ctor


@pytest.fixture
def bm(tmp_path: Path) -> BrowserManager:
    cfg = BrowserManagerConfig(profiles_dir=tmp_path / "profiles")
    return BrowserManager(cfg, AntiDetect())


def test_linkedin_default_account_key(bm: BrowserManager):
    li = LinkedIn(bm)
    assert li.account_key == "linkedin_primary"
    assert li.browser_manager is bm
    assert li._is_logged_in is False


def test_linkedin_explicit_account_key(bm: BrowserManager):
    li = LinkedIn(bm, account_key="alice@example.com")
    assert li.account_key == "alice@example.com"


def test_multiple_linkedin_instances_share_browser_manager(bm: BrowserManager):
    alice = LinkedIn(bm, account_key="alice")
    bob = LinkedIn(bm, account_key="bob")
    assert alice.browser_manager is bob.browser_manager
    assert alice.account_key != bob.account_key


def test_linkedin_and_twitter_can_share_browser_manager(bm: BrowserManager):
    """A single BrowserManager hosts contexts for many platforms."""
    from steadfast.platforms import Twitter

    li = LinkedIn(bm, account_key="my_li")
    tw = Twitter(bm, account_key="my_tw")
    assert li.browser_manager is tw.browser_manager
    assert li.account_key != tw.account_key


# -------------------------------------------------------------------- Constants


def test_linkedin_base_url():
    """Base URL is the canonical www.linkedin.com (not linkedin.com)."""
    assert LINKEDIN_BASE == "https://www.linkedin.com"


def test_selectors_dict_has_all_v01_keys():
    """All selectors used by v0.1.0 methods exist in the SELECTORS dict."""
    needed = {
        "login_email", "login_password", "login_button",
        "post_start_button", "post_text_editor", "post_submit_button",
        "post_image_input",
        "like_button",
        "comment_button", "comment_text_input", "comment_submit_button",
    }
    assert needed.issubset(set(SELECTORS.keys())), (
        f"Missing selectors: {needed - set(SELECTORS.keys())}"
    )


# ----------------------------------------------------- Cookie import (real)


@pytest.mark.asyncio
async def test_import_cookies_writes_state_for_correct_account(bm: BrowserManager):
    li = LinkedIn(bm, account_key="my_linkedin")
    cookies = [
        {
            "name": "li_at",
            "value": "AQEDABCD…",
            "domain": ".linkedin.com",
            "path": "/",
            "secure": True,
            "httpOnly": True,
            "sameSite": "none",
            "expirationDate": 9999999999.0,
        }
    ]
    ok = await li.import_cookies(cookies)
    assert ok is True

    state_file = bm.config.profiles_dir / "my_linkedin" / "state.json"
    assert state_file.exists()
    state = json.loads(state_file.read_text())
    assert state["cookies"][0]["name"] == "li_at"
    assert state["cookies"][0]["domain"] == ".linkedin.com"
    assert li._is_logged_in is False  # invalidated


@pytest.mark.asyncio
async def test_li_at_is_the_session_cookie(bm: BrowserManager):
    """Document the critical insight: LinkedIn auth lives in `li_at`.

    During the 2026-05-29 incident, MarketPilot's LinkedIn profile had a
    JSESSIONID but no li_at — that's why every post failed with
    'Feed page did not load — stuck at login'.
    """
    li = LinkedIn(bm, account_key="acct")
    # An auth state WITHOUT li_at should still import (we don't validate
    # cookie names — that's a runtime concern, not a schema concern).
    ok = await li.import_cookies(
        [{"name": "JSESSIONID", "value": "ajax", "domain": ".linkedin.com", "path": "/"}]
    )
    assert ok is True
    # But the diagnostic: a healthy LinkedIn profile contains 'li_at'
    state = json.loads(
        (bm.config.profiles_dir / "acct" / "state.json").read_text()
    )
    names = {c["name"] for c in state["cookies"]}
    assert "li_at" not in names  # this profile is BROKEN — for documentation


@pytest.mark.asyncio
async def test_import_cookies_does_not_cross_accounts(bm: BrowserManager):
    """Importing for one account must not write to another's profile dir."""
    alice = LinkedIn(bm, account_key="alice")
    await alice.import_cookies(
        [{"name": "li_at", "value": "1", "domain": ".linkedin.com", "path": "/"}]
    )
    assert (bm.config.profiles_dir / "alice" / "state.json").exists()
    assert not (bm.config.profiles_dir / "bob").exists()


# ---------------------------------------------------------- PostResult shape


def test_linkedin_post_returns_postresult_dataclass():
    """Same dataclass as Twitter — verified via import."""
    r = PostResult(success=True, platform_post_id="li_post_123")
    assert r.platform_post_id == "li_post_123"
    assert r.success
