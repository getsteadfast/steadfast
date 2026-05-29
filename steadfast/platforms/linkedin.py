"""LinkedIn browser automation.

LinkedIn detects automation aggressively.  All actions use extended delays
(3-10s between actions) and the helpers below also nudge mouse movement and
occasional scrolls between steps.

Usage::

    from steadfast import AntiDetect, BrowserManager, BrowserManagerConfig
    from steadfast.platforms import LinkedIn

    bm = BrowserManager(BrowserManagerConfig(profiles_dir="./profiles"), AntiDetect())
    await bm.start()

    li = LinkedIn(bm, account_key="my_linkedin")

    await li.import_cookies(cookies_list)  # from Cookie-Editor extension
    assert await li.ensure_logged_in()

    result = await li.post("Excited to share…")
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

log = get_logger("steadfast.linkedin")

LINKEDIN_BASE = "https://www.linkedin.com"

# LinkedIn UI selectors. These change periodically; the implementation uses
# fallback chains around them, so updating just one entry is usually enough
# to fix a breakage.
SELECTORS = {
    "login_email": "#username",
    "login_password": "#password",
    "login_button": "button[type='submit']",
    "post_start_button": "button.share-box-feed-entry__trigger",
    "post_text_editor": "div.ql-editor[data-placeholder]",
    "post_submit_button": "button.share-actions__primary-action",
    "post_image_input": "input[type='file'][accept='image/*']",
    "like_button": "button[aria-label*='Like']",
    "comment_button": "button[aria-label*='Comment']",
    "comment_text_input": "div.comments-comment-box__form div.ql-editor",
    "comment_submit_button": "button.comments-comment-box__submit-button",
}


class LinkedIn:
    """LinkedIn browser client for a single account."""

    def __init__(
        self,
        browser_manager: BrowserManager,
        account_key: str = "linkedin_primary",
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

    async def _linkedin_delay(self, min_sec: float = 3.0, max_sec: float = 10.0) -> None:
        """Extended delay between actions — LinkedIn's bot detection threshold."""
        await asyncio.sleep(random.uniform(min_sec, max_sec))

    async def _simulate_human_behavior(self, page: Page) -> None:
        """Random mouse movement + occasional scroll. Cheap, fast, plausible."""
        await self._anti_detect.random_mouse_movement(page)
        await asyncio.sleep(random.uniform(0.5, 1.5))
        if random.random() < 0.4:
            await self._anti_detect.human_like_scroll(page)

    async def _wait_for_feed_stable(self, page: Page, timeout_sec: int = 20) -> bool:
        """Poll page.url until it settles on /feed.

        LinkedIn often redirects through ``/uas/login-submit`` (Google One Tap)
        AFTER the initial ``domcontentloaded`` fires.  Returns True iff the
        page ends up on a /feed URL.
        """
        for _ in range(timeout_sec):
            await asyncio.sleep(1)
            url = page.url
            path = url.split("?")[0]
            if "/feed" in url and "/uas/" not in url and "/login" not in path:
                return True
        return "/feed" in page.url

    async def import_cookies(self, cookies: str | list[dict[str, Any]]) -> bool:
        """Import cookies for this account (e.g. from Cookie-Editor extension).

        Critical for LinkedIn — the auth flow is brittle and credential
        login often hits security checkpoints.  Real session cookies almost
        always survive longer than fresh logins.
        """
        ok = await self.browser_manager.import_cookies_to_profile(self.account_key, cookies)
        self._is_logged_in = False
        return ok

    # ----------------------------------------------------------------- Auth

    async def get_session_health(self) -> bool:
        """Cheap probe: load /feed, return True iff we land on it without
        being bounced to /login.

        Doesn't raise.  Saves state on success.
        """
        page = await self._get_page()
        try:
            await page.goto(f"{LINKEDIN_BASE}/feed/", wait_until="domcontentloaded")
            feed_ready = await self._wait_for_feed_stable(page, timeout_sec=15)
            if not feed_ready:
                log.info("LinkedIn session invalid — did not reach /feed",
                         account_key=self.account_key, landed=page.url)
                return False
            path = page.url.split("?")[0]
            if "/login" in path:
                log.info("LinkedIn session invalid — bounced to login",
                         account_key=self.account_key)
                return False
            await self._save_session()
            self._is_logged_in = True
            return True
        except Exception as exc:
            log.debug("LinkedIn session check failed", error=str(exc))
            return False
        finally:
            await page.close()

    async def ensure_logged_in(
        self,
        email: str | None = None,
        password: str | None = None,
    ) -> bool:
        """Return True if logged in; otherwise try login if credentials given."""
        if self._is_logged_in:
            return True
        if await self.get_session_health():
            return True
        if email and password:
            return await self.login(email=email, password=password)
        return False

    async def login(self, email: str, password: str) -> bool:
        """Log in with credentials.

        Prefer :meth:`import_cookies` + :meth:`ensure_logged_in` when possible.
        LinkedIn's checkpoint flow is the #1 source of broken automation.

        Raises :class:`LoginFailed` if a security checkpoint or unexpected
        landing page is encountered.
        """
        if self._is_logged_in:
            return True
        if await self.get_session_health():
            return True

        page = await self._get_page()
        try:
            await page.goto(f"{LINKEDIN_BASE}/login", wait_until="domcontentloaded")
            await self._linkedin_delay(2.0, 5.0)
            await self._simulate_human_behavior(page)

            await self._anti_detect.human_like_type(page, SELECTORS["login_email"], email)
            await self._linkedin_delay(1.0, 2.5)

            await self._anti_detect.human_like_type(page, SELECTORS["login_password"], password)
            await self._linkedin_delay(1.0, 3.0)

            await self._anti_detect.human_like_click(page, SELECTORS["login_button"])
            await self._linkedin_delay(3.0, 7.0)

            if "checkpoint" in page.url or "challenge" in page.url:
                log.error("LinkedIn security checkpoint encountered", email=email)
                raise LoginFailed(
                    "linkedin",
                    f"Security checkpoint for {email}. Manual verification required.",
                )

            if "/feed" in page.url or "/in/" in page.url:
                self._is_logged_in = True
                await self._save_session()
                log.info("Login successful", email=email)
                return True

            raise LoginFailed(
                "linkedin",
                f"Login failed for {email}. Landed on: {page.url}",
            )
        except LoginFailed:
            raise
        except Exception as exc:
            log.error("Login error", email=email, error=str(exc))
            raise LoginFailed("linkedin", f"Login error for {email}: {exc}") from exc
        finally:
            await page.close()

    # --------------------------------------------------------------- Posting

    async def _find_post_editor(self, page: Page) -> ElementHandle | None:
        """Locate the post-creation editor.

        LinkedIn has cycled through Quill, ProseMirror and a custom rich
        editor — try the broadest fallback chain.
        """
        for selector in (
            SELECTORS["post_text_editor"],                     # div.ql-editor[data-placeholder]
            "div.ql-editor",                                   # Quill (legacy)
            "div[role='textbox'][contenteditable='true']",     # ARIA textbox
            "div[contenteditable='true'][aria-label]",         # Labelled CE
            "div[data-placeholder][contenteditable='true']",   # ProseMirror style
            "div[contenteditable='true']",                     # broadest catch-all
        ):
            try:
                el = await page.wait_for_selector(selector, timeout=5000, state="visible")
                if el:
                    log.info("Found post editor", selector=selector)
                    return el
            except Exception:
                continue
        return None

    async def _find_start_post_button(self, page: Page) -> ElementHandle | None:
        """Locate the 'Start a post' trigger on the feed."""
        for selector in (
            SELECTORS["post_start_button"],
            "div.share-box-feed-entry__top-bar button",
            "button:has-text('Start a post')",
            "[role='button']:has-text('Start a post')",
            "div.share-box button",
        ):
            try:
                btn = await page.wait_for_selector(selector, timeout=5000, state="visible")
                if btn:
                    log.info("Found 'Start a post' button", selector=selector)
                    return btn
            except Exception:
                continue
        return None

    async def _click_natural(self, page: Page, element: ElementHandle) -> None:
        """Click with natural mouse movement towards the element's bbox."""
        box = await element.bounding_box()
        if not box:
            await element.click()
            return
        x = box["x"] + random.uniform(box["width"] * 0.2, box["width"] * 0.8)
        y = box["y"] + random.uniform(box["height"] * 0.2, box["height"] * 0.8)
        await page.mouse.move(x, y, steps=random.randint(5, 15))
        await asyncio.sleep(random.uniform(0.05, 0.2))
        await page.mouse.click(x, y)

    async def _type_into_editor(self, editor: ElementHandle, text: str) -> None:
        """Per-char typing with occasional thinking pauses (LinkedIn-tuned)."""
        await editor.click()
        await asyncio.sleep(random.uniform(0.3, 0.8))
        for char in text:
            await editor.type(char, delay=random.randint(20, 80))
            if random.random() < 0.03:
                await asyncio.sleep(random.uniform(0.2, 0.6))

    async def _click_post_submit(self, page: Page) -> bool:
        """Try each submit-button selector. Returns True iff clicked."""
        for selector in (
            SELECTORS["post_submit_button"],
            "button.share-actions__primary-action",
            "button:has-text('Post')",
            "button[aria-label='Post']",
            "button:has-text('Submit')",
        ):
            try:
                btn = await page.wait_for_selector(selector, timeout=5000, state="visible")
                if btn:
                    await btn.click()
                    return True
            except Exception:
                continue
        return False

    async def post(self, text: str, image_path: str | None = None) -> PostResult:
        """Create a LinkedIn feed post.

        Returns a :class:`PostResult`.  Note that LinkedIn does not surface
        a permanent post URL synchronously after submission — we return a
        synthetic ``li_post_<timestamp>`` id and an empty url.  Callers
        that need the real URL must fetch it from the profile later.
        """
        page = await self._get_page()
        try:
            await page.goto(f"{LINKEDIN_BASE}/feed/", wait_until="domcontentloaded")
            feed_ready = await self._wait_for_feed_stable(page, timeout_sec=20)
            if not feed_ready:
                raise PlatformError(
                    "linkedin", f"Feed page did not load — stuck at: {page.url}"
                )

            await self._linkedin_delay(2.0, 4.0)
            await self._simulate_human_behavior(page)

            start_btn = await self._find_start_post_button(page)
            if not start_btn:
                raise PlatformError(
                    "linkedin",
                    f"Could not find 'Start a post' button on feed ({page.url})",
                )
            await self._click_natural(page, start_btn)
            await self._linkedin_delay(2.0, 4.0)

            editor = await self._find_post_editor(page)
            if not editor:
                raise PlatformError(
                    "linkedin",
                    "Could not find post editor after clicking 'Start a post'",
                )

            await self._type_into_editor(editor, text)
            await self._linkedin_delay(1.0, 3.0)

            if image_path:
                try:
                    image_input = await page.wait_for_selector(
                        SELECTORS["post_image_input"], timeout=5000
                    )
                    if image_input:
                        await image_input.set_input_files(image_path)
                        await self._linkedin_delay(3.0, 6.0)
                except Exception as exc:
                    log.warning("Failed to attach image", error=str(exc))

            submitted = await self._click_post_submit(page)
            if not submitted:
                raise PlatformError("linkedin", "Could not find Post submit button")

            await self._linkedin_delay(3.0, 7.0)
            await self._save_session()

            synthetic = f"li_post_{int(datetime.now(timezone.utc).timestamp())}"
            log.info(
                "LinkedIn post created",
                account_key=self.account_key,
                text_length=len(text),
                synthetic_id=synthetic,
            )
            return PostResult(
                success=True,
                platform_post_id=synthetic,
                url="",  # LinkedIn doesn't expose the URL synchronously
                text_preview=text[:100],
                warning="LinkedIn does not return a permanent post URL synchronously",
            )
        except PlatformError as exc:
            return PostResult(success=False, error=str(exc))
        except Exception as exc:
            log.error("Failed to create LinkedIn post", error=str(exc))
            return PostResult(success=False, error=str(exc))
        finally:
            await page.close()

    # ----------------------------------------------------------------- Like

    async def like(self, post_url: str) -> bool:
        """Like a LinkedIn post by URL.

        Returns True iff a Like button was found and clicked. Returns False
        if the post is already liked, the button isn't found, or anything
        else goes wrong.
        """
        page = await self._get_page()
        try:
            await page.goto(post_url, wait_until="domcontentloaded")
            await self._linkedin_delay(3.0, 6.0)
            await self._simulate_human_behavior(page)

            like_btn = await page.query_selector(SELECTORS["like_button"])
            if not like_btn:
                log.info("Like button not found", post_url=post_url[:80])
                return False

            await self._anti_detect.human_like_click(page, SELECTORS["like_button"])
            await self._linkedin_delay(1.0, 3.0)
            await self._save_session()
            log.info("LinkedIn post liked", post_url=post_url[:80])
            return True
        except Exception as exc:
            log.error("Failed to like LinkedIn post",
                      post_url=post_url[:80], error=str(exc))
            return False
        finally:
            await page.close()

    # --------------------------------------------------------------- Comment

    async def comment(self, post_url: str, text: str) -> PostResult:
        """Comment on a LinkedIn post.

        Returns a :class:`PostResult`.  ``platform_post_id`` is a synthetic
        ``li_comment_<timestamp>`` since LinkedIn does not surface the
        comment id synchronously.
        """
        page = await self._get_page()
        try:
            await page.goto(post_url, wait_until="domcontentloaded")
            await self._linkedin_delay(3.0, 7.0)
            await self._simulate_human_behavior(page)

            comment_btn = await page.query_selector(SELECTORS["comment_button"])
            if comment_btn:
                await comment_btn.click()
                await self._linkedin_delay(1.5, 3.0)

            comment_input = await page.wait_for_selector(
                SELECTORS["comment_text_input"], timeout=10000
            )
            if not comment_input:
                raise PlatformError("linkedin", "Comment input not found")

            await self._type_into_editor(comment_input, text)
            await self._linkedin_delay(1.0, 3.0)

            submit_btn = await page.query_selector(SELECTORS["comment_submit_button"])
            if submit_btn:
                await submit_btn.click()
            else:
                # Fallback: Enter key
                await comment_input.press("Enter")

            await self._linkedin_delay(2.0, 5.0)
            await self._save_session()

            synthetic = f"li_comment_{int(datetime.now(timezone.utc).timestamp())}"
            log.info(
                "LinkedIn comment posted",
                account_key=self.account_key,
                post_url=post_url[:80],
                comment_length=len(text),
            )
            return PostResult(
                success=True,
                platform_post_id=synthetic,
                url=post_url,
                text_preview=text[:100],
            )
        except PlatformError as exc:
            return PostResult(success=False, error=str(exc))
        except Exception as exc:
            log.error("Failed to comment on LinkedIn post",
                      post_url=post_url[:80], error=str(exc))
            return PostResult(success=False, error=str(exc))
        finally:
            await page.close()
