"""TikTok browser automation.

.. warning::

   TikTok is the **most automation-hostile** platform Steadfast targets —
   even more so than Instagram.  The login flow throws CAPTCHA almost
   every fresh attempt, the upload composer detects headless aggressively,
   and the public DOM uses obfuscated class names that change weekly.

   The selectors here are best-effort from production web-DOM observation,
   NOT ported from a battle-tested production codebase like Twitter / LinkedIn /
   Reddit / Facebook.  Expect to update selectors more often than for the
   other platforms.

Scope:
  * Login + cookie import (auth lives in the ``sessionid`` + ``sessionid_ss``
    cookies — Cookie-Editor exports both).
  * Upload a video file with caption.
  * Like a video.

Not implemented (deliberately deferred):
  * Comment on videos (TikTok lazy-loads the comment panel via JS only after a
    click on the video, then renders the editor in a side-drawer with shifting
    DOM — too brittle for v0.1.x).
  * Stitches / duets (different composer surface).
  * Live streaming (different protocol).
  * Following / unfollowing (rate-limited heavily by TikTok).

Usage::

    from steadfast import AntiDetect, BrowserManager, BrowserManagerConfig
    from steadfast.platforms import TikTok

    bm = BrowserManager(BrowserManagerConfig(profiles_dir="./profiles"), AntiDetect())
    await bm.start()

    tt = TikTok(bm, account_key="my_tiktok")
    await tt.import_cookies(cookies_list)   # from Cookie-Editor extension
    assert await tt.ensure_logged_in()

    result = await tt.upload("video.mp4", caption="Hello from Steadfast 👋")
    print(result.platform_post_id, result.url)

    await bm.shutdown()
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from .._log import get_logger
from ..browser_manager import BrowserManager
from ..exceptions import LoginFailed, PlatformError
from ._models import PostResult

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import ElementHandle, Page

log = get_logger("steadfast.tiktok")

TIKTOK_BASE = "https://www.tiktok.com"
TIKTOK_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"
TIKTOK_LOGIN_URL = "https://www.tiktok.com/login"

# Form limits enforced by TikTok's composer.  We truncate rather than raise.
_CAPTION_MAX_CHARS = 2200

# URL fragments indicating TikTok's anti-automation challenge flow.
_CHECKPOINT_URL_FRAGMENTS = (
    "captcha",
    "verify",
    "challenge",
    "blocked",
    "secsdk",
)

# Logged-in indicators on tiktok.com.  Profile menu + upload button are
# only rendered for authenticated sessions.
_LOGGED_IN_INDICATORS = (
    'a[href*="/upload"]',
    'a[data-e2e="profile-icon"]',
    'div[data-e2e="profile-icon"]',
    'a[href*="/@"]',
    'div[data-e2e="nav-profile"]',
)

# Nag modals that block clicks: cookie banner, "Download the app", etc.
_DISMISS_BUTTONS = (
    'button:has-text("Accept all")',
    'button:has-text("Decline all")',
    'button:has-text("Allow all cookies")',
    'button:has-text("Reject all")',
    'div[role="dialog"] button[aria-label="Close"]',
    'button[aria-label="Close"]',
)


async def _first_visible(page: Page, selectors: tuple[str, ...]) -> ElementHandle | None:
    """Return the first VISIBLE element matching any selector, or None."""
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                return el
        except Exception:
            continue
    return None


async def _click_first_visible(page: Page, selectors: tuple[str, ...]) -> bool:
    """Click the first VISIBLE element matching any selector. True iff clicked."""
    el = await _first_visible(page, selectors)
    if not el:
        return False
    try:
        await el.click()
        return True
    except Exception:
        return False


async def _dismiss_popups(page: Page, attempts: int = 2) -> None:
    """Best-effort dismiss of cookie / app-promo / modal nags.  Silent on failure."""
    for _ in range(attempts):
        if not await _click_first_visible(page, _DISMISS_BUTTONS):
            return
        await asyncio.sleep(random.uniform(0.5, 1.2))


class TikTok:
    """TikTok browser client for a single account.

    All operations go through ``www.tiktok.com`` (NOT ``m.tiktok.com`` —
    mobile web uses a different React tree).  Uploads route through
    ``tiktokstudio/upload`` which is the desktop creator interface; the
    legacy ``/upload`` redirects there.
    """

    def __init__(
        self,
        browser_manager: BrowserManager,
        account_key: str = "tiktok_primary",
    ) -> None:
        self.browser_manager = browser_manager
        self.account_key = account_key
        self._anti_detect = browser_manager.anti_detect
        self._is_logged_in = False

    # -------------------------------------------------------------- Helpers

    async def _get_page(self) -> Page:
        return await self.browser_manager.get_page(self.account_key)

    async def _save_session(self) -> None:
        await self.browser_manager.save_state(self.account_key)

    async def _tt_delay(self, min_sec: float = 2.5, max_sec: float = 6.0) -> None:
        """Wait between actions.  TikTok's bot detection is the most
        sensitive of any platform Steadfast supports — use longer delays
        than Instagram/Facebook.
        """
        await asyncio.sleep(random.uniform(min_sec, max_sec))

    # ----------------------------------------------------------------- Auth

    async def import_cookies(self, cookies: str | list[dict[str, Any]]) -> bool:
        """Import cookies for this account (e.g. from Cookie-Editor extension).

        STRONGLY preferred over :meth:`login` — TikTok's credential auth
        trips CAPTCHA on a fresh IP almost every time, and Steadfast can't
        solve those interactively.

        CRITICAL: your export must include BOTH ``sessionid`` and
        ``sessionid_ss``.  Exports missing either will appear to import
        successfully but fail every session-health check.
        """
        ok = await self.browser_manager.import_cookies_to_profile(self.account_key, cookies)
        self._is_logged_in = False
        return ok

    async def get_session_health(self) -> bool:
        """Probe tiktok.com and look for logged-in indicators.

        Doesn't raise — returns False on any error.  Dismisses cookie /
        notification modals along the way.
        """
        page = await self._get_page()
        try:
            await page.goto(TIKTOK_BASE, wait_until="domcontentloaded")
            await self._tt_delay(2.0, 4.0)
            await _dismiss_popups(page)

            current_url = page.url
            if any(frag in current_url.lower() for frag in _CHECKPOINT_URL_FRAGMENTS):
                log.info("TikTok session invalid — challenge URL",
                         account_key=self.account_key, landed=current_url)
                return False
            if "/login" in current_url:
                log.info("TikTok session invalid — bounced to login",
                         account_key=self.account_key)
                return False

            indicator = await _first_visible(page, _LOGGED_IN_INDICATORS)
            if indicator:
                self._is_logged_in = True
                await self._save_session()
                return True

            return False
        except Exception as exc:
            log.debug("TikTok session check failed", error=str(exc))
            return False
        finally:
            await page.close()

    async def ensure_logged_in(
        self,
        username: str | None = None,
        password: str | None = None,
    ) -> bool:
        """Return True if logged in; otherwise try credential login if given."""
        if self._is_logged_in:
            return True
        if await self.get_session_health():
            return True
        if username and password:
            return await self.login(username=username, password=password)
        return False

    async def login(self, username: str, password: str) -> bool:
        """Log in with credentials via the "Use phone / email / username" path.

        Prefer :meth:`import_cookies` + :meth:`ensure_logged_in` whenever
        possible — TikTok's CAPTCHA fires on credential login on a fresh
        IP almost every time.

        Raises :class:`LoginFailed` on any failure, including CAPTCHA.
        """
        if self._is_logged_in:
            return True
        if await self.get_session_health():
            return True

        page = await self._get_page()
        try:
            await page.goto(TIKTOK_LOGIN_URL, wait_until="domcontentloaded")
            await self._tt_delay(2.0, 5.0)
            await _dismiss_popups(page)

            # Click "Use phone / email / username"
            method_button = await _first_visible(page, (
                'div[data-e2e="channel-item"]:has-text("Use phone / email / username")',
                'a[href*="phone-or-email"]',
                'div:has-text("Use phone / email / username")',
            ))
            if not method_button:
                raise LoginFailed("tiktok", "Login method selector not found")
            await method_button.click()
            await self._tt_delay(1.5, 3.0)

            # Click "Log in with email or username" sub-option
            email_tab = await _first_visible(page, (
                'a[href*="username"]',
                'div:has-text("Log in with email or username")',
            ))
            if email_tab:
                await email_tab.click()
                await self._tt_delay(1.0, 2.0)

            user_input = await page.wait_for_selector(
                'input[name="username"], input[type="text"][placeholder*="username" i]',
                timeout=15000,
            )
            if not user_input:
                raise LoginFailed("tiktok", "Username field not found on login page")

            pass_input = await page.wait_for_selector(
                'input[type="password"]', timeout=10000,
            )
            if not pass_input:
                raise LoginFailed("tiktok", "Password field not found on login page")

            await user_input.click()
            await self._tt_delay(0.3, 0.8)
            await user_input.fill(username)
            await self._tt_delay(0.5, 1.5)
            await pass_input.click()
            await self._tt_delay(0.3, 0.8)
            await pass_input.fill(password)
            await self._tt_delay(1.0, 2.5)

            submit_selectors = (
                'button[type="submit"]',
                'button[data-e2e="login-button"]',
                'button:has-text("Log in")',
            )
            if not await _click_first_visible(page, submit_selectors):
                await pass_input.press("Enter")

            await self._tt_delay(5.0, 10.0)

            current_url = page.url
            if any(frag in current_url.lower() for frag in _CHECKPOINT_URL_FRAGMENTS):
                raise LoginFailed(
                    "tiktok",
                    f"TikTok challenge for {username} (landed on {current_url}). "
                    "Use import_cookies() instead.",
                )

            await _dismiss_popups(page)

            indicator = await _first_visible(page, _LOGGED_IN_INDICATORS)
            if not indicator and "/login" in page.url:
                raise LoginFailed(
                    "tiktok",
                    f"Login verification failed for {username}. Landed on: {page.url}",
                )

            self._is_logged_in = True
            await self._save_session()
            log.info("TikTok login successful", username=username)
            return True
        except LoginFailed:
            raise
        except Exception as exc:
            raise LoginFailed("tiktok", f"Login error for {username}: {exc}") from exc
        finally:
            await page.close()

    # ---------------------------------------------------------------- Upload

    # TikTok Studio's upload picker fires a hidden <input type="file">; setting
    # files on it directly avoids the OS file-picker dialog.  The composer page
    # may have multiple file inputs (video + cover image); the one we want
    # accepts video MIME types.
    _UPLOAD_FILE_INPUT = (
        'input[type="file"][accept*="video" i]',
        'input[type="file"]',
    )

    # Caption editor — TikTok uses a contenteditable div, not a textarea.
    _CAPTION_INPUT = (
        'div[contenteditable="true"][role="combobox"]',
        'div[contenteditable="true"][aria-label*="caption" i]',
        'div[contenteditable="true"][data-text="true"]',
        'div[contenteditable="true"]',
    )

    # Privacy radio buttons.  TikTok labels are "Public", "Friends",
    # "Only you" (Private).  We match on the label text or e2e attribute.
    _PRIVACY_RADIOS: dict[str, tuple[str, ...]] = {
        "public": (
            '[data-e2e="advanced_privacy_settings-Public"]',
            'div[role="radio"]:has-text("Public")',
            'label:has-text("Public")',
        ),
        "friends": (
            '[data-e2e="advanced_privacy_settings-Friends"]',
            'div[role="radio"]:has-text("Friends")',
            'label:has-text("Friends")',
        ),
        "private": (
            '[data-e2e="advanced_privacy_settings-Only you"]',
            'div[role="radio"]:has-text("Only you")',
            'label:has-text("Only you")',
        ),
    }

    _POST_BUTTON = (
        'button[data-e2e="post_video_button"]',
        'button:has-text("Post")',
        'div[role="button"]:has-text("Post")',
    )

    async def upload(
        self,
        video_path: str | Path,
        caption: str = "",
        privacy: Literal["public", "friends", "private"] = "public",
    ) -> PostResult:
        """Upload a video to TikTok via the Studio web composer.

        Caption is truncated to 2200 chars (TikTok's hard limit).
        ``privacy`` selects the Visibility radio: ``"public"`` (default),
        ``"friends"`` (only mutual followers), or ``"private"`` (only the
        author can see).  Scheduled publishing, duets/stitches toggles,
        and AI-generated-content disclosures are out of scope for v0.1.x.

        Returns a :class:`PostResult`.  ``platform_post_id`` is synthetic
        (``tt_post_<timestamp>``) — TikTok doesn't surface the permanent
        video id synchronously after publish; the upload returns + the
        post appears in the user's profile a few seconds later.
        """
        path_obj = Path(video_path)
        if not path_obj.exists():
            return PostResult(success=False, error=f"Video file not found: {path_obj}")

        page = await self._get_page()
        try:
            await page.goto(TIKTOK_UPLOAD_URL, wait_until="domcontentloaded")
            await self._tt_delay(4.0, 7.0)
            await _dismiss_popups(page)

            if "/login" in page.url or any(
                frag in page.url.lower() for frag in _CHECKPOINT_URL_FRAGMENTS
            ):
                return PostResult(
                    success=False,
                    error=f"TikTok session invalid or challenge required (landed on {page.url[:120]})",
                )

            # Step 1 — file input
            file_input = await page.wait_for_selector(
                ", ".join(self._UPLOAD_FILE_INPUT), timeout=20000,
            )
            if not file_input:
                raise PlatformError("tiktok", "Upload file input not found")
            await file_input.set_input_files(str(path_obj))
            log.info("TikTok video attached", path=str(path_obj),
                     account_key=self.account_key)
            await self._tt_delay(5.0, 10.0)  # video processing kicks off

            # Step 2 — caption (replace TikTok's auto-derived suggestion)
            if caption:
                caption_box = await _first_visible(page, self._CAPTION_INPUT)
                if caption_box:
                    await caption_box.click()
                    await self._tt_delay(0.5, 1.0)
                    # Clear any auto-derived text first
                    await page.keyboard.press("Control+a")
                    await self._tt_delay(0.2, 0.5)
                    await page.keyboard.press("Delete")
                    await self._tt_delay(0.3, 0.7)
                    await page.keyboard.type(
                        caption[:_CAPTION_MAX_CHARS], delay=random.randint(25, 70),
                    )
                    await self._tt_delay(1.0, 2.5)
                else:
                    log.warning("Caption input not found — posting without caption",
                                account_key=self.account_key)

            # Step 3 — privacy
            await self._set_privacy(page, privacy)
            await self._tt_delay(1.0, 2.0)

            # Step 4 — wait for upload to finish (Post button enables)
            await self._wait_for_post_ready(page)

            # Step 5 — Post
            if not await _click_first_visible(page, self._POST_BUTTON):
                raise PlatformError("tiktok", "Post button not found / not enabled")

            # Step 6 — wait for the success redirect.  TikTok navigates
            # away from the upload page on a successful publish.
            # Some TikTok variants stay on the upload page with a success
            # toast instead; accept the timeout as a soft pass, the post
            # appears on the profile within a minute regardless.
            with contextlib.suppress(Exception):
                await page.wait_for_url(
                    lambda url: "tiktokstudio/upload" not in url, timeout=120_000,
                )

            await self._save_session()
            synthetic = f"tt_post_{int(datetime.now(timezone.utc).timestamp())}"
            log.info("TikTok video uploaded",
                     account_key=self.account_key, caption_len=len(caption))
            return PostResult(
                success=True,
                platform_post_id=synthetic,
                url="",
                text_preview=caption[:100],
                warning="TikTok does not return a permanent video URL synchronously",
            )
        except PlatformError as exc:
            return PostResult(success=False, error=str(exc))
        except Exception as exc:
            log.error("TikTok upload failed", error=str(exc))
            return PostResult(success=False, error=str(exc))
        finally:
            await page.close()

    # ── Upload helpers ────────────────────────────────────────────────

    async def _set_privacy(self, page: Page, privacy: str) -> None:
        """Click the radio button for the requested privacy level.

        Unknown values fall back to ``public`` — matches the conservative
        default and avoids accidentally privating a video on a typo.
        """
        selectors = self._PRIVACY_RADIOS.get(privacy.lower(), self._PRIVACY_RADIOS["public"])
        if not await _click_first_visible(page, selectors):
            log.warning("Could not set privacy — leaving TikTok default",
                        requested=privacy)

    async def _wait_for_post_ready(self, page: Page, max_wait_sec: int = 600) -> None:
        """Poll until the Post button is enabled (= upload finished processing).

        TikTok shows a progress bar inside the right-pane preview.  The
        Post button stays in a disabled state until the upload reaches
        ~80% server-side.  We give up after ``max_wait_sec`` and try
        clicking anyway; worst case the click silently fails and the
        exception path surfaces it.
        """
        log.info("Waiting for TikTok upload processing",
                 account_key=self.account_key)
        for i in range(max_wait_sec // 5):
            post_btn = await page.query_selector(
                "button[data-e2e='post_video_button']:not([disabled])"
            )
            if post_btn:
                return
            await asyncio.sleep(5)
            if i % 12 == 0:  # log every minute
                log.info("Still waiting on upload",
                         elapsed_sec=(i + 1) * 5, account_key=self.account_key)
        log.warning("Upload-processing wait timed out — clicking Post anyway")

    # ------------------------------------------------------------------ Like

    _LIKE_BUTTONS = (
        'button[aria-label*="like" i][aria-pressed="false"]',
        'span[data-e2e="like-icon"]',
        'button:has(svg[aria-label="Like"])',
    )

    async def like(self, video_url: str) -> bool:
        """Like a TikTok video by URL.

        Idempotent — if the video is already liked
        (``aria-pressed="true"`` or ``"unlike"`` label), returns True
        without clicking.  Returns False on session expiry or if the
        button isn't found.  Doesn't raise.
        """
        page = await self._get_page()
        try:
            await page.goto(video_url, wait_until="domcontentloaded")
            await self._tt_delay(3.0, 6.0)
            await _dismiss_popups(page)

            if "/login" in page.url or any(
                frag in page.url.lower() for frag in _CHECKPOINT_URL_FRAGMENTS
            ):
                log.warning("TikTok session invalid during like", url=page.url[:120])
                return False

            # If aria-pressed="true" is visible, already liked.
            already_liked = await page.query_selector(
                'button[aria-label*="like" i][aria-pressed="true"]'
            )
            if already_liked:
                log.info("TikTok video already liked", video_url=video_url[:80])
                return True

            # Watch a moment first — clicking like 200ms after page load is bot signal.
            await self._anti_detect.page_read_delay(300)

            if not await _click_first_visible(page, self._LIKE_BUTTONS):
                log.info("TikTok like button not found", video_url=video_url[:80])
                return False

            await self._tt_delay(1.0, 2.5)
            # Persist session opportunistically; failures here aren't fatal.
            with contextlib.suppress(Exception):
                await self._save_session()
            log.info("TikTok video liked", video_url=video_url[:80])
            return True
        except Exception as exc:
            log.warning("TikTok like failed",
                        video_url=video_url[:80], error=str(exc))
            return False
        finally:
            await page.close()
