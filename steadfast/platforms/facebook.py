"""Facebook browser automation.

Facebook is the **most volatile** of the platforms Steadfast targets: its
DOM, button labels, and dialog structure change on a near-weekly basis.
The implementation here uses chained-fallback selectors aggressively — if
one variant breaks, the rest still find their target until you can ship
an update.

The post flow has one critical invariant: **never look for a textbox
before clicking the composer trigger**.  Random ``contenteditable`` divs
on the feed are COMMENT boxes; the post composer only exists inside the
dialog that pops open after you click ``"What's on your mind"``.

Usage::

    from steadfast import AntiDetect, BrowserManager, BrowserManagerConfig
    from steadfast.platforms import Facebook

    bm = BrowserManager(BrowserManagerConfig(profiles_dir="./profiles"), AntiDetect())
    await bm.start()

    fb = Facebook(bm, account_key="my_facebook")
    await fb.import_cookies(cookies_list)   # from Cookie-Editor extension
    assert await fb.ensure_logged_in()

    result = await fb.post("Steadfast just hit v0.1.0 — try it out!")
    print(result.platform_post_id, result.url)

    await bm.shutdown()
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .._log import get_logger
from ..browser_manager import BrowserManager
from ..exceptions import LoginFailed, PlatformError
from ._models import PostResult

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import ElementHandle, Page

log = get_logger("steadfast.facebook")

FACEBOOK_BASE = "https://www.facebook.com"

# Strings that indicate FB redirected us to a security checkpoint instead
# of completing login.  Almost always means 2FA or unusual-login challenge.
_CHECKPOINT_URL_FRAGMENTS = (
    "checkpoint",
    "two_step_verification",
    "approve",
    "code_generator",
    "login_attempt",
)

# Cookie/consent banners that block clicks until dismissed.
_BANNER_SELECTORS = (
    'button[data-testid="cookie-policy-manage-dialog-accept-button"]',
    'button[title="Allow all cookies"]',
    'div[aria-label="Decline optional cookies"]',
)

# Logged-in indicators.  We use OR semantics: if any one matches, the
# session is considered live.  Order is shortest-to-longest by typical
# render time so the cheap checks run first.
_LOGGED_IN_INDICATORS = (
    '[aria-label="Your profile"]',
    '[aria-label="Account"]',
    '[aria-label="Messenger"]',
    '[aria-label="Notifications"]',
    '[aria-label="Menu"]',
    '[data-testid="blue_bar_profile_link"]',
    '[aria-label*="on your mind" i]',
    '[aria-label*="Create a post" i]',
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


async def _dismiss_banners(page: Page) -> None:
    """Best-effort dismiss of cookie/consent banners. Failures are silent."""
    for sel in _BANNER_SELECTORS:
        try:
            btn = await page.query_selector(sel)
            if btn:
                await btn.click(force=True)
                await asyncio.sleep(0.5)
        except Exception:
            continue


class Facebook:
    """Facebook browser client for a single account.

    All operations go through the canonical www.facebook.com flow — we don't
    use mobile or m.facebook.com (older selectors, less-tested in
    production).  Page-mode (posting as a Page rather than a profile) is
    explicitly out of scope for v0.1.0; use the profile context.
    """

    def __init__(
        self,
        browser_manager: BrowserManager,
        account_key: str = "facebook_primary",
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

    async def _fb_delay(self, min_sec: float = 2.0, max_sec: float = 5.0) -> None:
        """Wait between actions — FB's bot detection is impatience-sensitive."""
        await asyncio.sleep(random.uniform(min_sec, max_sec))

    # ----------------------------------------------------------------- Auth

    async def import_cookies(self, cookies: str | list[dict[str, Any]]) -> bool:
        """Import cookies for this account (e.g. from Cookie-Editor extension).

        Strongly preferred over :meth:`login` — Facebook's credential login
        trips the 2FA / "unusual login" checkpoint on a fresh IP almost
        every time, while imported cookies from an established session
        usually survive for weeks.
        """
        ok = await self.browser_manager.import_cookies_to_profile(self.account_key, cookies)
        self._is_logged_in = False
        return ok

    async def get_session_health(self) -> bool:
        """Probe facebook.com and look for logged-in indicators.

        Doesn't raise — returns False on any error. Sets ``_is_logged_in``
        and saves session state on success.
        """
        page = await self._get_page()
        try:
            await page.goto(FACEBOOK_BASE, wait_until="domcontentloaded")
            await self._fb_delay(2.0, 4.0)

            current_url = page.url
            if "/login" in current_url or "checkpoint" in current_url:
                log.info("Facebook session invalid — bounced to login/checkpoint",
                         account_key=self.account_key, landed=current_url)
                return False

            login_form = await page.query_selector(
                'input[name="email"], input[name="pass"], '
                'button[data-testid="royal_login_button"]'
            )
            if login_form:
                log.info("Facebook session invalid — login form detected",
                         account_key=self.account_key)
                return False

            indicator = await _first_visible(page, _LOGGED_IN_INDICATORS)
            if indicator:
                self._is_logged_in = True
                await self._save_session()
                return True

            # Soft fallback: if we're on a non-login facebook.com URL and
            # the body doesn't say "log in" in the first 300 chars, treat
            # as logged in.  Some logged-in pages (Marketplace, Groups)
            # don't render the standard indicators.
            try:
                body_lower = (await page.inner_text("body")).lower()[:300]
                if "log in" not in body_lower and "create new account" not in body_lower:
                    self._is_logged_in = True
                    await self._save_session()
                    return True
            except Exception:
                pass

            return False
        except Exception as exc:
            log.debug("Facebook session check failed", error=str(exc))
            return False
        finally:
            await page.close()

    async def ensure_logged_in(
        self,
        email: str | None = None,
        password: str | None = None,
    ) -> bool:
        """Return True if logged in; otherwise try credential login if given."""
        if self._is_logged_in:
            return True
        if await self.get_session_health():
            return True
        if email and password:
            return await self.login(email=email, password=password)
        return False

    async def login(self, email: str, password: str) -> bool:
        """Log in with credentials.

        Prefer :meth:`import_cookies` + :meth:`ensure_logged_in` whenever
        possible.  Facebook's checkpoint flow (2FA, unusual-login) is the
        #1 source of broken automation; cookie import sidesteps it.

        Raises :class:`LoginFailed` on any failure, including 2FA challenge.
        """
        if self._is_logged_in:
            return True
        if await self.get_session_health():
            return True

        page = await self._get_page()
        try:
            await page.goto(f"{FACEBOOK_BASE}/login", wait_until="domcontentloaded")
            await self._fb_delay(2.0, 5.0)

            email_input = await page.wait_for_selector(
                'input[name="email"], input#email, input[id="email"]',
                timeout=15000,
            )
            if not email_input:
                raise LoginFailed("facebook", "Could not find email field on login page")

            pass_input = await page.wait_for_selector(
                'input[name="pass"], input#pass, input[type="password"]',
                timeout=10000,
            )
            if not pass_input:
                raise LoginFailed("facebook", "Could not find password field on login page")

            await email_input.click()
            await self._fb_delay(0.3, 0.8)
            await email_input.fill(email)
            await self._fb_delay(0.5, 1.5)
            await pass_input.click()
            await self._fb_delay(0.3, 0.8)
            await pass_input.fill(password)
            await self._fb_delay(1.0, 2.5)

            login_selectors = (
                'button[name="login"]',
                'button[data-testid="royal_login_button"]',
                'button[type="submit"]',
                "#loginbutton",
            )
            if not await _click_first_visible(page, login_selectors):
                await pass_input.press("Enter")

            await self._fb_delay(5.0, 10.0)

            current_url = page.url
            if any(frag in current_url.lower() for frag in _CHECKPOINT_URL_FRAGMENTS):
                raise LoginFailed(
                    "facebook",
                    f"2FA / checkpoint detected for {email} (landed on {current_url}). "
                    "Either disable 2FA or use import_cookies() instead.",
                )

            # Verify session — get_session_health does the right checks
            # but operates on a fresh page; do an inline equivalent here
            # so we don't double-close the current page.
            indicator = await _first_visible(page, _LOGGED_IN_INDICATORS)
            if not indicator and "/login" in page.url:
                raise LoginFailed(
                    "facebook",
                    f"Login verification failed for {email}. Landed on: {page.url}",
                )

            self._is_logged_in = True
            await self._save_session()
            log.info("Facebook login successful", email=email)
            return True
        except LoginFailed:
            raise
        except Exception as exc:
            raise LoginFailed("facebook", f"Login error for {email}: {exc}") from exc
        finally:
            await page.close()

    # --------------------------------------------------------------- Posting

    # Composer-trigger selectors. Order matters — most stable first.
    _COMPOSER_TRIGGERS = (
        '[aria-label="Create a post"]',
        '[aria-label="What\'s on your mind"]',
        '[aria-label*="Create a post" i]',
        '[aria-label*="What\'s on your mind" i]',
        'div[role="button"]:has-text("Create a post")',
        'div[role="button"]:has-text("What\'s on your mind")',
        'div[role="button"]:has-text("Write something")',
        'span:has-text("What\'s on your mind")',
        'div[data-pagelet*="Composer"] div[role="button"]',
    )

    # Dialog-scoped textbox selectors. The dialog gate is critical — random
    # contenteditable divs on the feed are COMMENT boxes, not the composer.
    _DIALOG_TEXTBOX = (
        'div[role="dialog"] div[role="textbox"][contenteditable="true"]',
        'div[role="dialog"] div[role="textbox"]',
        'div[role="dialog"] div[contenteditable="true"][data-lexical-editor]',
        'div[role="dialog"] div[contenteditable="true"]',
    )

    _POST_SUBMIT = (
        'div[role="dialog"] div[aria-label="Post"][role="button"]',
        'div[role="dialog"] div[role="button"]:has-text("Post")',
        'div[role="dialog"] button:has-text("Post")',
        'div[role="dialog"] [aria-label="Post"]',
    )

    async def post(self, text: str) -> PostResult:
        """Submit a status post.

        Returns a :class:`PostResult`.  ``platform_post_id`` is synthetic
        (``fb_post_<timestamp>``) because Facebook does not surface the
        permanent permalink synchronously after submission — a separate
        page-fetch is required to find it, which v0.1.0 skips.
        """
        page = await self._get_page()
        try:
            await page.goto(FACEBOOK_BASE, wait_until="domcontentloaded", timeout=30000)
            await self._fb_delay(4.0, 6.0)

            if "/login" in page.url or "checkpoint" in page.url:
                return PostResult(
                    success=False,
                    error=f"Facebook session expired (landed on {page.url[:100]})",
                )

            await _dismiss_banners(page)

            # Click the composer trigger to open the Create Post dialog.
            if not await _click_first_visible(page, self._COMPOSER_TRIGGERS):
                raise PlatformError(
                    "facebook",
                    f"Could not find composer trigger on {page.url[:100]}",
                )
            await self._fb_delay(2.0, 4.0)

            # Find the textbox INSIDE the dialog.
            textbox = None
            for sel in self._DIALOG_TEXTBOX:
                try:
                    textbox = await page.wait_for_selector(sel, timeout=5000)
                    if textbox:
                        break
                except Exception:
                    continue
            if not textbox:
                raise PlatformError(
                    "facebook",
                    "Composer dialog opened but no textbox found inside it",
                )

            # Type the body. Use page.keyboard so React's state listener
            # actually picks up the input (.fill() bypasses React events).
            await textbox.click()
            await self._fb_delay(0.5, 1.0)
            await page.keyboard.type(text, delay=20)
            await self._fb_delay(1.5, 3.0)

            if not await _click_first_visible(page, self._POST_SUBMIT):
                raise PlatformError("facebook", "Could not find Post submit button in dialog")

            # Wait for the dialog to close, indicating the post landed.
            try:
                await page.wait_for_selector(
                    "div[role='dialog']", state="detached", timeout=15000
                )
            except Exception:
                # Some FB variants leave a confirmation toast in place — accept
                # the timeout as soft-success if no error is visible.
                err = await page.query_selector(
                    'div[role="dialog"] [aria-label*="Error" i], '
                    'div[role="dialog"] :text("try again")'
                )
                if err:
                    return PostResult(
                        success=False,
                        error=f"Facebook rejected the post: {(await err.inner_text())[:200]}",
                    )

            await self._save_session()
            synthetic = f"fb_post_{int(datetime.now(timezone.utc).timestamp())}"
            log.info("Facebook post created", account_key=self.account_key, length=len(text),
                     synthetic_id=synthetic)
            return PostResult(
                success=True,
                platform_post_id=synthetic,
                url="",
                text_preview=text[:100],
                warning="Facebook does not return a permanent post URL synchronously",
            )
        except PlatformError as exc:
            return PostResult(success=False, error=str(exc))
        except Exception as exc:
            log.error("Failed to create Facebook post", error=str(exc))
            return PostResult(success=False, error=str(exc))
        finally:
            await page.close()

    # --------------------------------------------------------------- Comment

    _COMMENT_TRIGGERS = (
        '[aria-label="Write a comment"]',
        '[aria-label*="Write a comment" i]',
        'div[role="textbox"][aria-label*="comment" i]',
        'div[contenteditable="true"][aria-placeholder*="comment" i]',
        'div[contenteditable="true"][data-lexical-editor]',
    )

    _COMMENT_SUBMIT = (
        'div[aria-label="Comment"][role="button"]',
        'button[aria-label="Comment"]',
        'div[role="button"]:has-text("Comment")',
    )

    async def comment(self, post_url: str, text: str) -> PostResult:
        """Submit a comment on a Facebook post.

        Returns a :class:`PostResult`.  ``platform_post_id`` is synthetic
        (``fb_comment_<timestamp>``) — FB does not surface the comment ID
        synchronously.
        """
        page = await self._get_page()
        try:
            await page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
            await self._fb_delay(4.0, 7.0)

            if "/login" in page.url or "checkpoint" in page.url:
                return PostResult(
                    success=False,
                    error=f"Facebook session expired (landed on {page.url[:100]})",
                )

            await _dismiss_banners(page)

            # Some posts have nested "View more comments" sequences before the
            # comment box appears. Scroll once to load the comments section.
            await self._anti_detect.human_like_scroll(page)
            await self._fb_delay(1.5, 3.0)

            comment_box = await _first_visible(page, self._COMMENT_TRIGGERS)
            if not comment_box:
                raise PlatformError(
                    "facebook", f"Could not find comment box on {post_url[:100]}"
                )

            # Activate + type via page.keyboard for React.
            await comment_box.click()
            await self._fb_delay(0.5, 1.0)
            await page.keyboard.type(text, delay=20)
            await self._fb_delay(1.0, 2.5)

            # Submit. Some variants only accept Cmd/Ctrl+Enter.
            if not await _click_first_visible(page, self._COMMENT_SUBMIT):
                await comment_box.press("Control+Enter")

            await self._fb_delay(2.0, 5.0)
            await self._save_session()

            synthetic = f"fb_comment_{int(datetime.now(timezone.utc).timestamp())}"
            log.info("Facebook comment posted", account_key=self.account_key,
                     post_url=post_url[:80], length=len(text))
            return PostResult(
                success=True,
                platform_post_id=synthetic,
                url=post_url,
                text_preview=text[:100],
            )
        except PlatformError as exc:
            return PostResult(success=False, error=str(exc))
        except Exception as exc:
            log.error("Failed to comment on Facebook post",
                      post_url=post_url[:80], error=str(exc))
            return PostResult(success=False, error=str(exc))
        finally:
            await page.close()

    # ------------------------------------------------------------------ Like

    _LIKE_BUTTONS = (
        '[aria-label="Like"]',
        '[aria-label*="Like" i]:not([aria-label*="liked" i])',
        'div[role="button"]:has-text("Like"):not(:has-text("Liked"))',
    )

    async def like(self, post_url: str) -> bool:
        """Like a Facebook post by URL.

        Returns True iff a Like button was found and clicked. Returns False
        on session expiry or if the button isn't visible.  Doesn't raise.
        """
        page = await self._get_page()
        try:
            await page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
            await self._fb_delay(3.0, 6.0)

            if "/login" in page.url or "checkpoint" in page.url:
                log.warning("Facebook session expired during like", url=page.url[:100])
                return False

            await _dismiss_banners(page)
            if not await _click_first_visible(page, self._LIKE_BUTTONS):
                log.info("Facebook like button not found", url=post_url[:80])
                return False

            await self._fb_delay(1.0, 2.5)
            await self._save_session()
            log.info("Facebook post liked", post_url=post_url[:80])
            return True
        except Exception as exc:
            log.warning("Facebook like failed", post_url=post_url[:80], error=str(exc))
            return False
        finally:
            await page.close()
