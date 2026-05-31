"""Facebook client surface tests (no Playwright required)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from steadfast import AntiDetect, BrowserManager, BrowserManagerConfig
from steadfast.platforms import Facebook, PostResult
from steadfast.platforms.facebook import (
    _CHECKPOINT_URL_FRAGMENTS,
    _LOGGED_IN_INDICATORS,
    FACEBOOK_BASE,
)

# --------------------------------------------------------------- Facebook ctor


@pytest.fixture
def bm(tmp_path: Path) -> BrowserManager:
    cfg = BrowserManagerConfig(profiles_dir=tmp_path / "profiles")
    return BrowserManager(cfg, AntiDetect())


def test_facebook_default_account_key(bm: BrowserManager):
    fb = Facebook(bm)
    assert fb.account_key == "facebook_primary"
    assert fb.browser_manager is bm
    assert fb._is_logged_in is False


def test_facebook_explicit_account_key(bm: BrowserManager):
    fb = Facebook(bm, account_key="my_fb_account")
    assert fb.account_key == "my_fb_account"


def test_multiple_facebook_instances_share_browser_manager(bm: BrowserManager):
    alice = Facebook(bm, account_key="alice")
    bob = Facebook(bm, account_key="bob")
    assert alice.browser_manager is bob.browser_manager
    assert alice.account_key != bob.account_key


def test_facebook_can_coexist_with_other_platforms(bm: BrowserManager):
    """A single BrowserManager hosts contexts for all platforms simultaneously."""
    from steadfast.platforms import LinkedIn, Reddit, Twitter

    fb = Facebook(bm, account_key="my_fb")
    li = LinkedIn(bm, account_key="my_li")
    tw = Twitter(bm, account_key="my_tw")
    rd = Reddit(bm, account_key="my_rd")
    assert fb.browser_manager is li.browser_manager is tw.browser_manager is rd.browser_manager
    keys = {fb.account_key, li.account_key, tw.account_key, rd.account_key}
    assert len(keys) == 4  # all distinct


# -------------------------------------------------------------------- Constants


def test_facebook_base_url():
    """Base URL is www.facebook.com — not m.facebook.com or mobile.facebook.com."""
    assert FACEBOOK_BASE == "https://www.facebook.com"


def test_checkpoint_fragments_cover_2fa_variants():
    """Login flow must detect every known checkpoint URL.

    Real FB checkpoint URLs from the wild include:
      /checkpoint/?next=...
      /checkpoint/two_step_verification/...
      /login_attempt/...
    """
    for frag in ("checkpoint", "two_step_verification", "code_generator", "login_attempt"):
        assert frag in _CHECKPOINT_URL_FRAGMENTS, (
            f"Missing checkpoint fragment: {frag}"
        )


def test_logged_in_indicators_are_aria_label_based():
    """Indicators should use aria-label or data-testid — these survive FB's

    weekly DOM churn better than CSS class names.
    """
    for sel in _LOGGED_IN_INDICATORS:
        assert "aria-label" in sel or "data-testid" in sel, (
            f"Indicator should be aria-label-based for stability: {sel}"
        )


# ----------------------------------------------------- Cookie import (real)


@pytest.mark.asyncio
async def test_import_cookies_writes_state_for_correct_account(bm: BrowserManager):
    fb = Facebook(bm, account_key="my_fb")
    cookies = [
        {
            "name": "c_user",
            "value": "100012345678",
            "domain": ".facebook.com",
            "path": "/",
            "secure": True,
            "httpOnly": False,
            "sameSite": "none",
            "expirationDate": 9999999999.0,
        },
        {
            "name": "xs",
            "value": "abcd:1234:5678",
            "domain": ".facebook.com",
            "path": "/",
            "secure": True,
            "httpOnly": True,
        },
    ]
    ok = await fb.import_cookies(cookies)
    assert ok is True

    state_file = bm.config.profiles_dir / "my_fb" / "state.json"
    assert state_file.exists()
    state = json.loads(state_file.read_text())
    names = {c["name"] for c in state["cookies"]}
    # c_user + xs are the two cookies FB actually uses to verify session.
    assert "c_user" in names
    assert "xs" in names
    assert fb._is_logged_in is False  # invalidated by import


@pytest.mark.asyncio
async def test_c_user_and_xs_are_the_session_cookies(bm: BrowserManager):
    """Document the critical insight: Facebook auth lives in `c_user` + `xs`.

    A cookie export missing either of these will fail session-health checks
    even though Cookie-Editor will export it without complaining.
    """
    fb = Facebook(bm, account_key="acct")
    ok = await fb.import_cookies(
        [{"name": "datr", "value": "abc", "domain": ".facebook.com", "path": "/"}]
    )
    assert ok is True
    state = json.loads(
        (bm.config.profiles_dir / "acct" / "state.json").read_text()
    )
    names = {c["name"] for c in state["cookies"]}
    # This profile is missing both auth cookies — documented as broken.
    assert "c_user" not in names
    assert "xs" not in names


@pytest.mark.asyncio
async def test_import_cookies_does_not_cross_accounts(bm: BrowserManager):
    """Importing for one account must not write to another's profile dir."""
    alice = Facebook(bm, account_key="alice")
    await alice.import_cookies(
        [{"name": "c_user", "value": "1", "domain": ".facebook.com", "path": "/"}]
    )
    assert (bm.config.profiles_dir / "alice" / "state.json").exists()
    assert not (bm.config.profiles_dir / "bob").exists()


# ---------------------------------------------------------- PostResult shape


def test_facebook_post_returns_postresult_dataclass():
    """Same dataclass as the other platforms — verified via the import path."""
    r = PostResult(success=True, platform_post_id="fb_post_1700000000")
    assert r.platform_post_id == "fb_post_1700000000"
    assert r.success


def test_composer_trigger_selectors_include_aria_label_variants():
    """The composer-trigger chain must include both quoted and starts-with
    aria-label variants — FB ships A/B-tested label text often.
    """
    fb_triggers = Facebook._COMPOSER_TRIGGERS  # noqa: SLF001 — exposing for test
    assert any("aria-label=" in sel for sel in fb_triggers)
    assert any("aria-label*=" in sel for sel in fb_triggers)


def test_dialog_textbox_selectors_are_dialog_scoped():
    """CRITICAL: every dialog-textbox selector must be scoped to
    ``div[role="dialog"]``.  Bare contenteditable selectors will pick up
    COMMENT boxes on the feed — the bug that took MarketPilot 3 weeks to
    diagnose.  This test is the canary.
    """
    for sel in Facebook._DIALOG_TEXTBOX:  # noqa: SLF001
        assert sel.startswith('div[role="dialog"]'), (
            f"Dialog-textbox selector NOT scoped to dialog (would pick up comments): {sel}"
        )
