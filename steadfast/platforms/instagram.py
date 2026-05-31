"""Instagram browser automation.

.. warning::

   Instagram is the **most automation-hostile** platform Steadfast targets.
   Even with perfect cookies + anti-detect, expect periodic
   "Suspicious Login Attempt" challenges, "Save Login Info?" interstitials,
   and selector breakage as Instagram's React tree re-renders class names
   on a roughly-monthly cadence.

   The selectors here are best-effort from production web-DOM observation,
   NOT ported from a battle-tested production codebase like the other
   four platforms.  Expect to update selectors more often than for
   Twitter / LinkedIn / Reddit / Facebook.

Scope:
  * Login + cookie import (auth lives in the ``sessionid`` cookie).
  * Like a post.
  * Comment on a post.
  * Post a single image with caption (web-supported since 2024;
    carousel + video / Reels are out of scope for v0.1.0).

Not implemented (deliberately):
  * Stories (mobile-app-only flow on most accounts).
  * Reels (different composer, video-only).
  * Direct messages (different surface).
  * Search / Explore (read-side, scope creep).
  * Follow / unfollow (rate-limited heavily by Instagram).

Usage::

    from steadfast import AntiDetect, BrowserManager, BrowserManagerConfig
    from steadfast.platforms import Instagram

    bm = BrowserManager(BrowserManagerConfig(profiles_dir="./profiles"), AntiDetect())
    await bm.start()

    ig = Instagram(bm, account_key="my_instagram")
    await ig.import_cookies(cookies_list)   # from Cookie-Editor extension
    assert await ig.ensure_logged_in()

    result = await ig.post("photo.jpg", caption="From Steadfast 👋")
    print(result.platform_post_id, result.url)

    await bm.shutdown()
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .._log import get_logger
from ..browser_manager import BrowserManager
from ..exceptions import LoginFailed, PlatformError
from ._models import PostResult

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import ElementHandle, Page

log = get_logger("steadfast.instagram")

INSTAGRAM_BASE = "https://www.instagram.com"

# URL fragments indicating Instagram's anti-automation challenge flow.
_CHECKPOINT_URL_FRAGMENTS = (
    "challenge",
    "suspicious",
    "two_factor",
    "verify",
)

# Logged-in indicators. ``svg[aria-label="Home"]`` is the canonical signal
# — it only renders on the navbar when an authenticated session exists.
_LOGGED_IN_INDICATORS = (
    'svg[aria-label="Home"]',
    'a[href*="/accounts/edit/"]',
    'a[href="/explore/"]',
    'a[href$="/direct/inbox/"]',
    'svg[aria-label="New post"]',
)

# Modals / popups that block clicks after login or page navigation.
# Order: cookie banner → "Save Login Info?" → "Turn on Notifications?" →
# any generic "Not Now" button.
_DISMISS_BUTTONS = (
    'button:has-text("Allow all cookies")',
    'button:has-text("Decline optional cookies")',
    'button:has-text("Only allow essential cookies")',
    'button:has-text("Save Info")',
    'button:has-text("Not Now")',
    'button:has-text("Not now")',
    'div[role="dialog"] button:has-text("Cancel")',
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


async def _dismiss_popups(page: Page, attempts: int = 3) -> None:
    """Best-effort dismiss of cookie banners + "Save Login Info" + notifications.

    Instagram stacks these modals across page navigations; one click often
    surfaces the next one.  ``attempts`` controls how many rounds we run.
    Failures are silent — these are non-fatal UX nags, not blocking errors.
    """
    for _ in range(attempts):
        if not await _click_first_visible(page, _DISMISS_BUTTONS):
            return
        await asyncio.sleep(random.uniform(0.5, 1.2))


class Instagram:
    """Instagram browser client for a single account.

    All operations go through ``www.instagram.com``.  Mobile
    (``m.instagram.com``) selectors are NOT covered — Instagram's mobile
    web is a stripped-down PWA with different React component IDs.
    """

    def __init__(
        self,
        browser_manager: BrowserManager,
        account_key: str = "instagram_primary",
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

    async def _ig_delay(self, min_sec: float = 2.0, max_sec: float = 5.0) -> None:
        """Wait between actions — Instagram's bot detection is very sensitive."""
        await asyncio.sleep(random.uniform(min_sec, max_sec))

    # ----------------------------------------------------------------- Auth

    async def import_cookies(self, cookies: str | list[dict[str, Any]]) -> bool:
        """Import cookies for this account (e.g. from Cookie-Editor extension).

        STRONGLY preferred over :meth:`login` — Instagram's credential auth
        trips "Suspicious Login Attempt" on a fresh IP almost every time.
        Imported cookies from a real browser session usually survive 1-4
        weeks before requiring re-export.

        CRITICAL: your export must include the ``sessionid`` cookie — it's
        the only one Instagram actually uses for authentication.  Cookie
        exports that omit it (e.g. "session-only" exports) will appear to
        import successfully but fail every session-health check.
        """
        ok = await self.browser_manager.import_cookies_to_profile(self.account_key, cookies)
        self._is_logged_in = False
        return ok

    async def get_session_health(self) -> bool:
        """Probe instagram.com and look for logged-in navbar indicators.

        Doesn't raise — returns False on any error.  Dismisses cookie /
        notification modals along the way.
        """
        page = await self._get_page()
        try:
            await page.goto(INSTAGRAM_BASE, wait_until="domcontentloaded")
            await self._ig_delay(2.0, 4.0)
            await _dismiss_popups(page)

            current_url = page.url
            if any(frag in current_url.lower() for frag in _CHECKPOINT_URL_FRAGMENTS):
                log.info("Instagram session invalid — challenge URL",
                         account_key=self.account_key, landed=current_url)
                return False
            if "/accounts/login" in current_url:
                log.info("Instagram session invalid — bounced to login",
                         account_key=self.account_key)
                return False

            indicator = await _first_visible(page, _LOGGED_IN_INDICATORS)
            if indicator:
                self._is_logged_in = True
                await self._save_session()
                return True

            return False
        except Exception as exc:
            log.debug("Instagram session check failed", error=str(exc))
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
        """Log in with credentials.

        Prefer :meth:`import_cookies` + :meth:`ensure_logged_in` whenever
        possible.  Instagram's challenge flow ("Suspicious Login Attempt",
        SMS / email verification) fires on a fresh IP almost every time
        and Steadfast can't solve those interactively.

        Raises :class:`LoginFailed` on any failure, including challenges.
        """
        if self._is_logged_in:
            return True
        if await self.get_session_health():
            return True

        page = await self._get_page()
        try:
            await page.goto(f"{INSTAGRAM_BASE}/accounts/login/",
                            wait_until="domcontentloaded")
            await self._ig_delay(2.0, 5.0)
            await _dismiss_popups(page)

            user_input = await page.wait_for_selector(
                'input[name="username"]', timeout=15000,
            )
            if not user_input:
                raise LoginFailed("instagram", "Username field not found on login page")

            pass_input = await page.wait_for_selector(
                'input[name="password"]', timeout=10000,
            )
            if not pass_input:
                raise LoginFailed("instagram", "Password field not found on login page")

            await user_input.click()
            await self._ig_delay(0.3, 0.8)
            await user_input.fill(username)
            await self._ig_delay(0.5, 1.5)
            await pass_input.click()
            await self._ig_delay(0.3, 0.8)
            await pass_input.fill(password)
            await self._ig_delay(1.0, 2.5)

            # Submit. Several variants — fall back to Enter if no button.
            submit_selectors = (
                'button[type="submit"]',
                'button:has-text("Log in")',
                'button:has-text("Log In")',
            )
            if not await _click_first_visible(page, submit_selectors):
                await pass_input.press("Enter")

            await self._ig_delay(5.0, 10.0)

            current_url = page.url
            if any(frag in current_url.lower() for frag in _CHECKPOINT_URL_FRAGMENTS):
                raise LoginFailed(
                    "instagram",
                    f"Instagram challenge for {username} (landed on {current_url}). "
                    "Use import_cookies() instead.",
                )

            # Dismiss "Save Login Info?" / "Turn on Notifications?" popups.
            await _dismiss_popups(page)

            indicator = await _first_visible(page, _LOGGED_IN_INDICATORS)
            if not indicator and "/accounts/login" in page.url:
                raise LoginFailed(
                    "instagram",
                    f"Login verification failed for {username}. Landed on: {page.url}",
                )

            self._is_logged_in = True
            await self._save_session()
            log.info("Instagram login successful", username=username)
            return True
        except LoginFailed:
            raise
        except Exception as exc:
            raise LoginFailed("instagram", f"Login error for {username}: {exc}") from exc
        finally:
            await page.close()

    # --------------------------------------------------------------- Posting

    # The "+" / "New post" button in the navbar. Multiple selector variants
    # because Instagram A/B-tests the icon label.
    _NEW_POST_TRIGGERS = (
        'svg[aria-label="New post"]',
        'a[href="#"]:has(svg[aria-label="New post"])',
        'div[role="button"]:has(svg[aria-label="New post"])',
        'span:has-text("Create")',
    )

    # The "Select from computer" CTA in the upload modal.
    _UPLOAD_CTA = (
        'button:has-text("Select from computer")',
        'button:has-text("Select From Computer")',
        'div[role="dialog"] button:has-text("Select")',
    )

    # "Next" wizard button (appears 2x in the flow: crop → caption).
    _NEXT_BUTTONS = (
        'div[role="dialog"] div[role="button"]:has-text("Next")',
        'div[role="dialog"] button:has-text("Next")',
    )

    # Caption textbox in the share dialog.
    _CAPTION_INPUT = (
        'div[role="dialog"] [aria-label="Write a caption..."]',
        'div[role="dialog"] [aria-label*="caption" i]',
        'div[role="dialog"] div[contenteditable="true"]',
        'div[role="dialog"] textarea',
    )

    # Final "Share" button (publishes the post).
    _SHARE_BUTTON = (
        'div[role="dialog"] div[role="button"]:has-text("Share")',
        'div[role="dialog"] button:has-text("Share")',
    )

    async def post(self, image_path: str | Path, caption: str = "") -> PostResult:
        """Post a single image with caption.

        Carousel posts (multiple images), videos, and Reels are NOT
        supported in v0.1.0 — the wizard flow diverges significantly for
        each and would dilute the test surface.

        Returns a :class:`PostResult`.  ``platform_post_id`` is synthetic
        (``ig_post_<timestamp>``) because Instagram doesn't surface the
        permanent shortcode synchronously after publish.
        """
        path_obj = Path(image_path)
        if not path_obj.exists():
            return PostResult(success=False, error=f"Image file not found: {path_obj}")

        page = await self._get_page()
        try:
            await page.goto(INSTAGRAM_BASE, wait_until="domcontentloaded")
            await self._ig_delay(3.0, 5.0)
            await _dismiss_popups(page)

            if "/accounts/login" in page.url:
                return PostResult(
                    success=False,
                    error="Instagram session expired — bounced to login",
                )

            # Step 1: click the "+" / "New post" trigger.
            if not await _click_first_visible(page, self._NEW_POST_TRIGGERS):
                raise PlatformError("instagram", "New-post trigger not found on navbar")
            await self._ig_delay(2.0, 4.0)

            # Step 2: open the file picker. IG's "Select from computer" button
            # triggers a hidden <input type=file>; setting it directly is more
            # reliable than driving the OS file dialog.
            file_input = await page.wait_for_selector(
                'input[type="file"][accept*="image" i], input[type="file"]',
                timeout=10000,
            )
            if not file_input:
                # Sometimes the input only exists after clicking the CTA.
                await _click_first_visible(page, self._UPLOAD_CTA)
                await self._ig_delay(1.0, 2.0)
                file_input = await page.wait_for_selector(
                    'input[type="file"]', timeout=10000,
                )
            if not file_input:
                raise PlatformError("instagram", "File input not found in upload modal")

            await file_input.set_input_files(str(path_obj))
            log.info("Instagram image attached", path=str(path_obj))
            await self._ig_delay(3.0, 5.0)

            # Step 3: two "Next" clicks (crop step → filter step → caption step).
            for _ in range(2):
                if not await _click_first_visible(page, self._NEXT_BUTTONS):
                    raise PlatformError(
                        "instagram", "Could not advance upload wizard (Next not found)"
                    )
                await self._ig_delay(1.5, 3.0)

            # Step 4: caption (optional).
            if caption:
                caption_box = await _first_visible(page, self._CAPTION_INPUT)
                if caption_box:
                    await caption_box.click()
                    await self._ig_delay(0.3, 0.8)
                    await page.keyboard.type(caption, delay=random.randint(25, 70))
                    await self._ig_delay(1.0, 2.5)
                else:
                    log.warning("Caption input not found — posting without caption",
                                account_key=self.account_key)

            # Step 5: Share.
            if not await _click_first_visible(page, self._SHARE_BUTTON):
                raise PlatformError("instagram", "Share button not found in dialog")

            # Step 6: wait for the dialog to close (= post submitted).
            # Some IG variants leave a "Your post has been shared" toast in
            # place of the dialog teardown — accept the timeout as soft pass.
            with contextlib.suppress(Exception):
                await page.wait_for_selector(
                    'div[role="dialog"]', state="detached", timeout=60000,
                )

            await self._save_session()
            synthetic = f"ig_post_{int(datetime.now(timezone.utc).timestamp())}"
            log.info("Instagram post published",
                     account_key=self.account_key, caption_len=len(caption))
            return PostResult(
                success=True,
                platform_post_id=synthetic,
                url="",
                text_preview=caption[:100],
                warning="Instagram does not return a permanent post URL synchronously",
            )
        except PlatformError as exc:
            return PostResult(success=False, error=str(exc))
        except Exception as exc:
            log.error("Instagram post failed", error=str(exc))
            return PostResult(success=False, error=str(exc))
        finally:
            await page.close()

    # --------------------------------------------------------------- Comment

    _COMMENT_INPUTS = (
        'textarea[aria-label*="comment" i]',
        'form textarea[placeholder*="comment" i]',
        'form textarea',
        '[role="textbox"][aria-label*="comment" i]',
    )

    _COMMENT_SUBMIT = (
        'div[role="button"]:has-text("Post")',
        'button:has-text("Post")',
        'button[type="submit"]',
    )

    async def comment(self, post_url: str, text: str) -> PostResult:
        """Submit a comment on an Instagram post.

        Returns a :class:`PostResult`.  ``platform_post_id`` is synthetic
        — Instagram doesn't surface the comment id synchronously.
        """
        page = await self._get_page()
        try:
            await page.goto(post_url, wait_until="domcontentloaded")
            await self._ig_delay(3.0, 6.0)
            await _dismiss_popups(page)

            if "/accounts/login" in page.url:
                return PostResult(
                    success=False,
                    error="Instagram session expired — bounced to login",
                )

            comment_box = await _first_visible(page, self._COMMENT_INPUTS)
            if not comment_box:
                # Some posts have comments disabled; others lazy-render the
                # input after a click on the heart/comment icon. Try once.
                await _click_first_visible(page, ('svg[aria-label="Comment"]',))
                await self._ig_delay(1.0, 2.0)
                comment_box = await _first_visible(page, self._COMMENT_INPUTS)
            if not comment_box:
                return PostResult(
                    success=False,
                    error="Comment input not found — comments may be disabled on this post",
                )

            await comment_box.click()
            await self._ig_delay(0.5, 1.0)
            await page.keyboard.type(text, delay=random.randint(25, 70))
            await self._ig_delay(1.0, 2.5)

            # Submit: button OR Enter as fallback.
            if not await _click_first_visible(page, self._COMMENT_SUBMIT):
                await comment_box.press("Enter")

            await self._ig_delay(2.0, 4.0)
            await self._save_session()

            synthetic = f"ig_comment_{int(datetime.now(timezone.utc).timestamp())}"
            log.info("Instagram comment posted",
                     account_key=self.account_key, post_url=post_url[:80])
            return PostResult(
                success=True,
                platform_post_id=synthetic,
                url=post_url,
                text_preview=text[:100],
            )
        except PlatformError as exc:
            return PostResult(success=False, error=str(exc))
        except Exception as exc:
            log.error("Instagram comment failed",
                      post_url=post_url[:80], error=str(exc))
            return PostResult(success=False, error=str(exc))
        finally:
            await page.close()

    # ------------------------------------------------------------------ Like

    _LIKE_BUTTONS = (
        'svg[aria-label="Like"]',
        'button:has(svg[aria-label="Like"])',
        'span[role="button"]:has(svg[aria-label="Like"])',
    )

    async def like(self, post_url: str) -> bool:
        """Like an Instagram post by URL.

        Idempotent — if the post is already liked (the heart icon's
        aria-label flips to ``"Unlike"``), returns True without clicking.
        Returns False on session expiry or if the button isn't found.
        Doesn't raise.
        """
        page = await self._get_page()
        try:
            await page.goto(post_url, wait_until="domcontentloaded")
            await self._ig_delay(3.0, 6.0)
            await _dismiss_popups(page)

            if "/accounts/login" in page.url:
                log.warning("Instagram session expired during like",
                            url=page.url[:100])
                return False

            # If "Unlike" is visible, the post is already liked.
            already_liked = await page.query_selector('svg[aria-label="Unlike"]')
            if already_liked:
                log.info("Post already liked", post_url=post_url[:80])
                return True

            if not await _click_first_visible(page, self._LIKE_BUTTONS):
                log.info("Instagram like button not found", post_url=post_url[:80])
                return False

            await self._ig_delay(1.0, 2.5)
            await self._save_session()
            log.info("Instagram post liked", post_url=post_url[:80])
            return True
        except Exception as exc:
            log.warning("Instagram like failed",
                        post_url=post_url[:80], error=str(exc))
            return False
        finally:
            await page.close()
