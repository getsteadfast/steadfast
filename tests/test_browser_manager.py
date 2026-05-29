"""BrowserManagerConfig + import_cookies_to_profile (no Playwright required)."""

import json
from pathlib import Path

import pytest

from steadfast.browser_manager import BrowserManager, BrowserManagerConfig
from steadfast.exceptions import BrowserError

# -------------------------------------------------------- BrowserManagerConfig


def test_config_creates_profiles_dir(tmp_path: Path):
    target = tmp_path / "profiles"
    assert not target.exists()
    cfg = BrowserManagerConfig(profiles_dir=target)
    assert target.exists()
    assert cfg.profiles_dir == target


def test_config_defaults():
    cfg = BrowserManagerConfig(profiles_dir="/tmp/steadfast_test_defaults")
    assert cfg.headless is True
    assert cfg.max_concurrent == 4
    assert cfg.timezone == "UTC"
    assert cfg.extra_browser_args == []


def test_config_accepts_string_path(tmp_path: Path):
    target = tmp_path / "as_string"
    cfg = BrowserManagerConfig(profiles_dir=str(target))
    assert isinstance(cfg.profiles_dir, Path)
    assert cfg.profiles_dir == target


# -------------------------------------------------------- import_cookies_to_profile


@pytest.fixture
def bm(tmp_path: Path) -> BrowserManager:
    cfg = BrowserManagerConfig(profiles_dir=tmp_path / "profiles")
    return BrowserManager(cfg)


@pytest.mark.asyncio
async def test_import_cookies_writes_state_json(bm: BrowserManager):
    cookies = [
        {
            "name": "auth_token",
            "value": "abc123",
            "domain": ".example.com",
            "path": "/",
            "secure": True,
            "httpOnly": True,
            "sameSite": "lax",
            "expirationDate": 9999999999.0,
        }
    ]
    ok = await bm.import_cookies_to_profile("twitter_main", cookies)
    assert ok is True

    state_path = bm.config.profiles_dir / "twitter_main" / "state.json"
    assert state_path.exists()

    state = json.loads(state_path.read_text())
    assert len(state["cookies"]) == 1
    saved = state["cookies"][0]
    assert saved["name"] == "auth_token"
    assert saved["value"] == "abc123"
    assert saved["sameSite"] == "Lax"  # capitalized for Playwright
    assert saved["expires"] == 9999999999.0  # expirationDate → expires


@pytest.mark.asyncio
async def test_import_cookies_accepts_json_string(bm: BrowserManager):
    cookies_json = json.dumps([
        {"name": "k", "value": "v", "domain": ".example.com", "path": "/"}
    ])
    ok = await bm.import_cookies_to_profile("twitter_main", cookies_json)
    assert ok is True


@pytest.mark.asyncio
async def test_import_cookies_normalizes_samesite(bm: BrowserManager):
    cookies = [
        {"name": "a", "value": "1", "domain": "x", "path": "/", "sameSite": "strict"},
        {"name": "b", "value": "2", "domain": "x", "path": "/", "sameSite": "lax"},
        {"name": "c", "value": "3", "domain": "x", "path": "/", "sameSite": "no_restriction"},
        {"name": "d", "value": "4", "domain": "x", "path": "/"},  # missing
    ]
    await bm.import_cookies_to_profile("acct", cookies)
    saved = json.loads(
        (bm.config.profiles_dir / "acct" / "state.json").read_text()
    )["cookies"]
    same_sites = [c["sameSite"] for c in saved]
    assert same_sites == ["Strict", "Lax", "None", "None"]


@pytest.mark.asyncio
async def test_import_cookies_rejects_invalid_json(bm: BrowserManager):
    with pytest.raises(BrowserError) as exc_info:
        await bm.import_cookies_to_profile("acct", "not valid json {")
    assert "Invalid cookies JSON" in str(exc_info.value)


@pytest.mark.asyncio
async def test_import_cookies_rejects_empty_list(bm: BrowserManager):
    with pytest.raises(BrowserError) as exc_info:
        await bm.import_cookies_to_profile("acct", [])
    assert "Empty" in str(exc_info.value)


@pytest.mark.asyncio
async def test_import_cookies_overwrites_existing(bm: BrowserManager):
    """Re-importing must REPLACE the cookies array, not append."""
    first = [{"name": "x", "value": "1", "domain": "d", "path": "/"}]
    await bm.import_cookies_to_profile("acct", first)
    second = [{"name": "y", "value": "2", "domain": "d", "path": "/"}]
    await bm.import_cookies_to_profile("acct", second)
    saved = json.loads(
        (bm.config.profiles_dir / "acct" / "state.json").read_text()
    )
    assert len(saved["cookies"]) == 1
    assert saved["cookies"][0]["name"] == "y"


@pytest.mark.asyncio
async def test_nested_account_key_creates_subdir(bm: BrowserManager):
    """`account_key` of 'project_3/twitter_primary' should nest."""
    await bm.import_cookies_to_profile(
        "project_3/twitter_primary",
        [{"name": "x", "value": "y", "domain": "d", "path": "/"}],
    )
    assert (
        bm.config.profiles_dir / "project_3" / "twitter_primary" / "state.json"
    ).exists()


# --------------------------------------------------- BrowserManager state


def test_initially_not_started(bm: BrowserManager):
    assert bm.is_started is False
    assert bm.active_contexts == 0
