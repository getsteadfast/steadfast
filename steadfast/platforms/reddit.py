"""Reddit browser automation.

Reddit's UX is split between old.reddit.com (server-rendered, stable
selectors) and www.reddit.com (SPA with custom web components like
``shreddit-composer``).  We prefer **old.reddit.com** as the primary path
since it's more reliable for automation, with new Reddit as a fallback.

Usage::

    from steadfast import AntiDetect, BrowserManager, BrowserManagerConfig
    from steadfast.platforms import Reddit

    bm = BrowserManager(BrowserManagerConfig(profiles_dir="./profiles"), AntiDetect())
    await bm.start()

    reddit = Reddit(bm, account_key="my_reddit")
    await reddit.import_cookies(cookies_list)
    assert await reddit.ensure_logged_in()

    result = await reddit.post("test", title="Hello", body="From Steadfast")
    print(result.platform_post_id, result.url)

    await bm.shutdown()
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from .._log import get_logger
from ..browser_manager import BrowserManager
from ..exceptions import LoginFailed
from ._models import PostResult

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import ElementHandle, Page

log = get_logger("steadfast.reddit")

REDDIT_BASE = "https://www.reddit.com"
OLD_REDDIT_BASE = "https://old.reddit.com"


# JS that force-reveals a hidden element by un-setting display:none on it and
# its ancestors. Reddit hides the comment textarea behind a JS-only "expand"
# trigger on some old-reddit themes.
_JS_FORCE_REVEAL = """(selector) => {
    const el = document.querySelector(selector);
    if (!el) return;
    let node = el;
    while (node && node !== document.body) {
        if (getComputedStyle(node).display === 'none') {
            node.style.display = '';
        }
        node = node.parentElement;
    }
    el.style.display = '';
    el.focus();
}"""


class Reddit:
    """Reddit browser client for a single account."""

    def __init__(
        self,
        browser_manager: BrowserManager,
        account_key: str = "reddit_primary",
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

    async def import_cookies(self, cookies: str | list[dict[str, Any]]) -> bool:
        """Import cookies for this account (e.g. from Cookie-Editor)."""
        ok = await self.browser_manager.import_cookies_to_profile(self.account_key, cookies)
        self._is_logged_in = False
        return ok

    @staticmethod
    def _to_old_reddit_url(url: str) -> str:
        """Rewrite a Reddit URL to its old.reddit.com equivalent.

        NOTE: chained `.replace()` would double-rewrite because old.reddit.com
        contains "reddit.com" as a substring — handle each form explicitly.
        """
        if "old.reddit.com" in url:
            return url
        if "www.reddit.com" in url:
            return url.replace("www.reddit.com", "old.reddit.com")
        return url.replace("reddit.com", "old.reddit.com")

    @staticmethod
    def _to_new_reddit_url(url: str) -> str:
        """Rewrite a Reddit URL to its www.reddit.com equivalent.

        Same substring-collision caveat as :meth:`_to_old_reddit_url`.
        """
        if "www.reddit.com" in url:
            return url
        if "old.reddit.com" in url:
            return url.replace("old.reddit.com", "www.reddit.com")
        return url.replace("reddit.com", "www.reddit.com")

    @staticmethod
    async def _is_ip_blocked(page: Page) -> bool:
        """Return True iff the page is Reddit's network-security block page."""
        try:
            body = await page.inner_text("body")
        except Exception:
            return False
        first_chunk = body.lower()[:200]
        return "blocked by network security" in body.lower() or "blocked" in first_chunk

    # ------------------------------------------------------------------ Auth

    async def get_session_health(self) -> bool:
        """Probe BOTH old.reddit and new.reddit for logged-in indicators.

        Sessions sometimes work on one domain but not the other (different
        cookies per subdomain).  Doesn't raise — returns False on any error.
        """
        page = await self._get_page()
        try:
            # Check old.reddit
            await page.goto(OLD_REDDIT_BASE, wait_until="domcontentloaded")
            await self._anti_detect.random_delay(1.5, 3.0)
            if not await page.query_selector(".login-required, .login-form"):
                for sel in (
                    '#header form[action*="logout"]',
                    '#header .user a[href*="/user/"]',
                    "#header .user .userkarma",
                    "#mail, #modmail",
                ):
                    if await page.query_selector(sel):
                        log.info("Session valid (old reddit)", selector=sel)
                        self._is_logged_in = True
                        await self._save_session()
                        return True

            # Check new.reddit
            await page.goto(REDDIT_BASE, wait_until="domcontentloaded")
            await self._anti_detect.random_delay(2.0, 4.0)
            for sel in (
                "#email-collection-tooltip-id",
                'button[aria-label*="profile"]',
                'header a[href*="/user/"]',
                'faceplate-tracker[source="nav"]',
                'a[href*="logout"]',
                '[data-testid="reddit-user-nav"]',
            ):
                if await page.query_selector(sel):
                    log.info("Session valid (new reddit)", selector=sel)
                    self._is_logged_in = True
                    await self._save_session()
                    return True

            # Reasoning by absence: if there's NO login button anywhere, we're
            # very likely logged in (Reddit just didn't render the indicators
            # we know about).
            if not await page.query_selector(
                'a[href*="/login"], button:has-text("Log In")'
            ):
                log.info("Session likely valid (no login button found)")
                self._is_logged_in = True
                await self._save_session()
                return True

            log.warning("Session check: not logged in", url=page.url)
            return False
        except Exception as exc:
            log.warning("Session check failed", error=str(exc))
            return False
        finally:
            await page.close()

    async def ensure_logged_in(
        self,
        username: str | None = None,
        password: str | None = None,
    ) -> bool:
        """Return True if logged in; otherwise try `login` if creds provided."""
        if self._is_logged_in:
            return True
        if await self.get_session_health():
            return True
        if username and password:
            return await self.login(username=username, password=password)
        return False

    async def login(self, username: str, password: str) -> bool:
        """Log in with credentials. Tries old reddit first, then new reddit.

        Prefer cookie import for production use — Reddit's login flow
        triggers CAPTCHAs frequently from automation.

        Raises :class:`LoginFailed` on any failure.
        """
        if self._is_logged_in or await self.get_session_health():
            return True

        last_error: Exception | None = None
        for strategy in ("old_reddit", "new_reddit"):
            try:
                if strategy == "old_reddit":
                    ok = await self._login_old_reddit(username, password)
                else:
                    ok = await self._login_new_reddit(username, password)
                if ok:
                    self._is_logged_in = True
                    await self._save_session()
                    log.info("Login successful", username=username, strategy=strategy)
                    return True
            except Exception as exc:
                last_error = exc
                log.warning(
                    "Login strategy failed", strategy=strategy, error=str(exc)
                )
                continue

        raise LoginFailed("reddit", f"All login strategies failed. Last error: {last_error}")

    async def _login_old_reddit(self, username: str, password: str) -> bool:
        """Login via old.reddit.com/login (simpler form, more reliable)."""
        page = await self._get_page()
        try:
            await page.goto(f"{OLD_REDDIT_BASE}/login", wait_until="domcontentloaded")
            await self._anti_detect.random_delay(2.0, 4.0)

            user_input = await page.wait_for_selector(
                '#user_login, input[name="user"]', timeout=10000
            )
            pass_input = await page.wait_for_selector(
                '#passwd_login, input[name="passwd"]', timeout=10000
            )

            await user_input.click()
            await self._anti_detect.random_delay(0.3, 0.8)
            await user_input.fill(username)
            await self._anti_detect.random_delay(0.5, 1.2)
            await pass_input.click()
            await self._anti_detect.random_delay(0.3, 0.8)
            await pass_input.fill(password)
            await self._anti_detect.random_delay(0.8, 2.0)

            login_btn = await page.query_selector(
                'button[type="submit"], #login-button, input[type="submit"]'
            )
            if login_btn:
                await login_btn.click()
            else:
                await pass_input.press("Enter")

            await self._anti_detect.random_delay(4.0, 7.0)
            return await self._verify_login(page, username)
        finally:
            await page.close()

    async def _login_new_reddit(self, username: str, password: str) -> bool:
        """Login via www.reddit.com/login (newer flow, more variation)."""
        page = await self._get_page()
        try:
            await page.goto(f"{REDDIT_BASE}/login", wait_until="domcontentloaded")
            await self._anti_detect.random_delay(2.0, 4.0)

            user_input = None
            for sel in (
                'input[name="username"]',
                "#loginUsername",
                'input[id="loginUsername"]',
                'input[autocomplete="username"]',
            ):
                try:
                    user_input = await page.wait_for_selector(sel, timeout=5000)
                    if user_input:
                        break
                except Exception:
                    continue
            if not user_input:
                raise LoginFailed("reddit", "Could not find username field")

            pass_input = None
            for sel in (
                'input[name="password"]',
                "#loginPassword",
                'input[id="loginPassword"]',
                'input[type="password"]',
            ):
                try:
                    pass_input = await page.wait_for_selector(sel, timeout=5000)
                    if pass_input:
                        break
                except Exception:
                    continue
            if not pass_input:
                raise LoginFailed("reddit", "Could not find password field")

            await user_input.click()
            await self._anti_detect.random_delay(0.3, 0.8)
            await user_input.fill(username)
            await self._anti_detect.random_delay(0.5, 1.2)
            await pass_input.click()
            await self._anti_detect.random_delay(0.3, 0.8)
            await pass_input.fill(password)
            await self._anti_detect.random_delay(0.8, 2.0)

            clicked = False
            for sel in (
                'button[type="submit"]',
                'button:has-text("Log In")',
                'button:has-text("Log in")',
            ):
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        await btn.click()
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                await pass_input.press("Enter")

            await self._anti_detect.random_delay(5.0, 8.0)
            return await self._verify_login(page, username)
        finally:
            await page.close()

    async def _verify_login(self, page: Page, username: str) -> bool:
        """Verify login. Raises LoginFailed with the most-specific error found."""
        for sel in (
            ".AnimatedForm__errorMessage",
            ".c-form-error",
            ".status-msg.error",
            'div[class*="ErrorMessage"]',
            '[role="alert"]',
            "faceplate-banner",
            "faceplate-toast",
        ):
            try:
                err = await page.query_selector(sel)
                if err and await err.is_visible():
                    text = (await err.inner_text()).strip()
                    if text:
                        raise LoginFailed("reddit", f"Login error: {text}")
            except LoginFailed:
                raise
            except Exception:
                continue

        try:
            body = await page.inner_text("body")
            for phrase in (
                "incorrect username or password",
                "wrong password",
                "too many attempts",
                "blocked by network security",
            ):
                if phrase in body.lower():
                    raise LoginFailed("reddit", f"Login failed: detected '{phrase}' on page")
        except LoginFailed:
            raise
        except Exception:
            pass

        async def _check_logged_in_here() -> str | None:
            for sel in (
                f'a[href*="/user/{username}"]',
                'a[href*="logout"], form[action*="logout"]',
                'button[aria-label*="profile"]',
                '[data-testid="reddit-user-nav"]',
                "#email-collection-tooltip-id",
                "#USER_DROPDOWN_ID",
                "#header .user",
                'a[href*="/submit"]',
            ):
                try:
                    if await page.query_selector(sel):
                        return sel
                except Exception:
                    continue
            return None

        # Check current page
        matched = await _check_logged_in_here()
        if matched:
            log.info("Login verified", selector=matched)
            return True

        # Check old reddit
        if "/login" not in page.url and "/register" not in page.url:
            await page.goto(OLD_REDDIT_BASE, wait_until="domcontentloaded")
            await self._anti_detect.random_delay(2.0, 3.0)
            if await _check_logged_in_here():
                return True
            if not await page.query_selector(
                ".login-required, .login-form, #login_login-main"
            ):
                body = await page.inner_text("body")
                if "log in" not in body.lower()[:300]:
                    log.info("Login likely succeeded — no login form on old reddit")
                    return True

        # Check new reddit
        await page.goto(REDDIT_BASE, wait_until="domcontentloaded")
        await self._anti_detect.random_delay(2.0, 4.0)
        if await _check_logged_in_here():
            return True
        if not await page.query_selector(
            'a[href*="/login"], button:has-text("Log In")'
        ):
            return True

        raise LoginFailed("reddit", "Login verification failed — could not confirm logged-in state")

    # ------------------------------------------------------------------ Post

    async def post(self, subreddit: str, title: str, body: str) -> PostResult:
        """Submit a text post to a subreddit.

        Tries old.reddit first (most reliable). Returns a
        :class:`PostResult` with the post's permalink on success.
        """
        page = await self._get_page()
        try:
            log.info("Submitting post", subreddit=subreddit, title=title[:60])

            # Pin old.reddit via cookie (best effort)
            with contextlib.suppress(Exception):
                await page.context.add_cookies([{
                    "name": "redesign_optout",
                    "value": "true",
                    "domain": ".reddit.com",
                    "path": "/",
                }])

            submit_url = f"{OLD_REDDIT_BASE}/r/{subreddit}/submit?selftext=true"
            await page.goto(submit_url, wait_until="domcontentloaded")
            await self._anti_detect.random_delay(2.0, 4.0)

            if await self._is_ip_blocked(page):
                return PostResult(
                    success=False,
                    error="Reddit has blocked this IP (network security). Proxy required.",
                )

            on_old_reddit = "old.reddit.com" in page.url and "/submit" in page.url

            if on_old_reddit:
                # Logged-in check
                login_form = await page.query_selector(
                    "form#login_login-main, form.login-form, .login-form-side"
                )
                user_ind = await page.query_selector(
                    ".user a.login-required, span.user a, .logout"
                )
                if login_form and not user_ind:
                    return PostResult(
                        success=False,
                        error="Reddit session expired — not logged in. Re-import cookies.",
                    )

                # Subreddit restriction check
                restriction = await page.query_selector(
                    ".restricted-subreddit, .submit-page .infobar.infobar-error, "
                    ".submit-page .infobar-message"
                )
                if restriction:
                    text = (await restriction.inner_text()).strip()
                    if text and any(
                        kw in text.lower()
                        for kw in (
                            "restrict", "karma", "not allowed", "approved",
                            "banned", "require", "minimum", "cannot",
                        )
                    ):
                        return PostResult(
                            success=False,
                            error=f"Subreddit restriction: {text[:200]}",
                        )

                # Fill the form
                title_input = await page.query_selector(
                    'textarea[name="title"], input[name="title"]'
                )
                if title_input:
                    await title_input.click()
                    await title_input.fill("")
                    await self._anti_detect.human_like_type(
                        page, 'textarea[name="title"], input[name="title"]', title
                    )
                    await self._anti_detect.short_pause()

                    # Click the text tab if present
                    try:
                        tab = await page.query_selector('a.text-button, [value="self"]')
                        if tab:
                            await tab.click()
                            await self._anti_detect.short_pause()
                    except Exception:
                        pass

                    body_input = await page.query_selector(
                        'textarea[name="text"], .usertext-edit textarea'
                    )
                    if body_input:
                        await body_input.click()
                        await body_input.fill("")
                        await body_input.fill(body)
                        await self._anti_detect.random_delay(1.0, 3.0)

                        submit_btn = await page.query_selector(
                            'button[name="submit"], .save-button button[type="submit"], '
                            "#newlink-submit-button"
                        )
                        if submit_btn:
                            await submit_btn.click()
                            await self._anti_detect.random_delay(3.0, 6.0)
                            await page.wait_for_load_state("domcontentloaded")

                            err = await page.query_selector(".error, .status-msg.error")
                            if err:
                                text = (await err.inner_text()).strip()
                                if text:
                                    return PostResult(success=False, error=text)

                            landed = page.url
                            if "/comments/" in landed:
                                permalink = landed.split("?")[0]
                                # Extract post id from /comments/<id>/...
                                post_id = ""
                                parts = permalink.split("/comments/")
                                if len(parts) > 1:
                                    post_id = parts[1].split("/")[0]

                                await self._save_session()
                                log.info("Post submitted", permalink=permalink)
                                return PostResult(
                                    success=True,
                                    platform_post_id=post_id,
                                    url=permalink,
                                    text_preview=title[:100],
                                )
                            # Old reddit didn't redirect to /comments/ — likely a soft failure
                            return PostResult(
                                success=False,
                                error=f"Submit did not redirect to /comments/ — landed on {landed[:120]}",
                            )

            return PostResult(
                success=False,
                error=(
                    f"Could not submit on old.reddit.com (landed on {page.url[:120]}). "
                    f"Subreddit may require approved-user status."
                ),
            )
        except Exception as exc:
            log.error("Failed to submit post", error=str(exc))
            return PostResult(success=False, error=str(exc))
        finally:
            await page.close()

    # --------------------------------------------------------------- Comment

    async def comment(self, post_url: str, text: str) -> PostResult:
        """Submit a comment on a Reddit post.

        Tries old.reddit first; falls back to new.reddit if not logged in there.

        Returns a :class:`PostResult`. On success ``url`` is the post URL
        and ``platform_post_id`` is the post id with a ``comment-`` prefix
        (Reddit comment ids aren't extractable synchronously without
        re-fetching the page).
        """
        page = await self._get_page()
        try:
            old_url = self._to_old_reddit_url(post_url)
            new_url = self._to_new_reddit_url(post_url)

            using_old = True
            await page.goto(old_url, wait_until="domcontentloaded")
            await self._anti_detect.random_delay(2.0, 5.0)

            # Logged-in check on old reddit
            if await page.query_selector(
                '.login-required, .commentarea .infobar a[href*="login"]'
            ):
                log.warning("Not logged in on old.reddit, falling back to new reddit")
                using_old = False
                await page.close()
                page = await self._get_page()
                await page.goto(new_url, wait_until="domcontentloaded")
                # Wait for SPA bundles to hydrate (shreddit-composer is lazy)
                await asyncio.sleep(12)

            # Post deleted? Reddit redirects away from /comments/
            if "/comments/" not in page.url.lower():
                return PostResult(
                    success=False,
                    error="Target post has been deleted — Reddit redirected away.",
                )

            # Locked / archived?
            locked = await self._detect_locked(page, using_old)
            if locked:
                return PostResult(success=False, error=locked)

            await self._anti_detect.short_pause()

            comment_box, find_error = await self._find_comment_box(page)
            if not comment_box:
                err_msg = find_error or "Could not find comment box"
                return PostResult(success=False, error=err_msg)

            # Type
            await comment_box.click()
            await comment_box.fill("")
            await comment_box.type(text, delay=25)
            await self._anti_detect.random_delay(1.5, 3.0)

            # Submit
            submit_btn = None
            for sel in (
                ".usertext-edit .save-button button",
                'button:has-text("save")',
                'button:has-text("comment")',
                'button[type="submit"]',
                "shreddit-composer button[type=submit]",
            ):
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        submit_btn = btn
                        break
                except Exception:
                    continue

            if not submit_btn:
                # Fallback: Ctrl-Enter
                await comment_box.press("Control+Enter")
            else:
                await submit_btn.click()

            await self._anti_detect.random_delay(3.0, 6.0)

            await self._save_session()

            # Extract post id from URL
            post_id = ""
            if "/comments/" in page.url:
                parts = page.url.split("/comments/")
                if len(parts) > 1:
                    post_id = parts[1].split("/")[0]

            log.info("Comment submitted", post_url=post_url[:80])
            return PostResult(
                success=True,
                platform_post_id=f"comment-on-{post_id}" if post_id else "",
                url=page.url.split("?")[0],
                text_preview=text[:100],
            )
        except Exception as exc:
            log.error("Failed to submit comment", post_url=post_url[:80], error=str(exc))
            return PostResult(success=False, error=str(exc))
        finally:
            await page.close()

    async def _detect_locked(self, page: Page, using_old: bool) -> str | None:
        """Return an error message iff the post is locked/archived."""
        if using_old:
            locked = await page.query_selector(
                ".locked-tagline, .archived-infobar, .thing.link.locked, "
                '.link .stamp[title*="archived"], .link .stamp[title*="locked"]'
            )
            if locked:
                text = await locked.inner_text()
                return f"Post is locked/archived: {text[:100]}"
            try:
                comment_area = await page.query_selector(".commentarea")
                if comment_area:
                    ca_text = (await comment_area.inner_text())[:500].lower()
                    if "archived" in ca_text and "no longer" in ca_text:
                        return "Post is archived — commenting disabled"
            except Exception:
                pass
        else:
            try:
                body = (await page.inner_text("body"))[:2000].lower()
                if "this thread has been locked" in body or "comments are locked" in body:
                    return "Post is locked — commenting disabled"
                if "archived" in body and ("no longer" in body or "can't comment" in body):
                    return "Post is archived — commenting disabled"
            except Exception:
                pass
        return None

    async def _find_comment_box(self, page: Page) -> tuple[ElementHandle | None, str | None]:
        """Find a usable comment textarea/contenteditable.

        Strategy chain:
          1. Visible old-reddit textarea (most reliable)
          2. Hidden old-reddit textarea (JS force-reveal)
          3. New-reddit ``shreddit-composer`` (wait for custom element define)
          4. Generic contenteditable as last resort

        Returns (element, error_string). ``element`` is None on failure;
        ``error_string`` explains why.
        """
        if await page.query_selector(
            '.login-required, .commentarea .infobar a[href*="login"]'
        ):
            return None, "Not logged in — session may have expired"

        log.info("Finding comment box", url=page.url[:80])

        old_textarea_selectors = (
            '.commentarea .usertext-edit textarea[name="text"]',
            '.commentarea textarea[name="text"]',
            ".usertext-edit textarea",
            'textarea[name="text"]',
        )

        # Strategy 1: visible textarea
        for sel in old_textarea_selectors:
            try:
                el = await page.wait_for_selector(sel, timeout=3000, state="visible")
                if el:
                    return el, None
            except Exception:
                continue

        # Strategy 2: hidden textarea, force-reveal via JS
        for sel in old_textarea_selectors:
            try:
                el = await page.wait_for_selector(sel, timeout=2000, state="attached")
                if el:
                    await page.evaluate(_JS_FORCE_REVEAL, sel)
                    await asyncio.sleep(0.5)
                    el = await page.query_selector(sel)
                    if el:
                        if await el.is_visible():
                            return el, None
                        await el.click(force=True)
                        await asyncio.sleep(0.3)
                        return el, None
            except Exception:
                continue

        # Strategy 3: new-reddit shreddit-composer
        try:
            status = await page.evaluate(
                """() => Promise.race([
                    customElements.whenDefined('shreddit-composer').then(() => 'defined'),
                    new Promise(r => setTimeout(() => r('timeout'), 20000))
                ])"""
            )
            log.info("shreddit-composer status", status=status)
            if status == "defined":
                # Try a few selectors against the composer
                for sel in (
                    "shreddit-composer textarea",
                    'shreddit-composer div[contenteditable="true"]',
                    'shreddit-composer [role="textbox"]',
                    'div[contenteditable="true"][data-placeholder*="comment" i]',
                ):
                    try:
                        el = await page.query_selector(sel)
                        if el and await el.is_visible():
                            return el, None
                    except Exception:
                        continue
        except Exception as exc:
            log.debug("shreddit-composer wait failed", error=str(exc))

        # Strategy 4: any visible contenteditable
        try:
            el = await page.query_selector(
                'div[contenteditable="true"], textarea'
            )
            if el and await el.is_visible():
                return el, None
        except Exception:
            pass

        return None, "Could not find any comment input on the page"

    # --------------------------------------------------------------- Upvote

    async def upvote(self, post_url: str) -> bool:
        """Upvote a post by URL.

        Operates on old.reddit (the upvote arrow has the most stable selector
        there).  Returns True iff the upvote arrow was found and clicked.
        Returns False if already upvoted or any error occurs.
        """
        page = await self._get_page()
        try:
            old_url = self._to_old_reddit_url(post_url)
            await page.goto(old_url, wait_until="domcontentloaded")
            await self._anti_detect.random_delay(1.5, 3.5)

            btn = await page.query_selector(
                '.arrow.up:not(.upmod), [data-click-id="upvote"]:not([aria-pressed="true"])'
            )
            if btn:
                await self._anti_detect.human_like_click(page, ".arrow.up:not(.upmod)")
                await self._anti_detect.short_pause()
                log.info("Upvoted", url=post_url[:80])
                return True
            log.info("Already upvoted or button not found", url=post_url[:80])
            return False
        except Exception as exc:
            log.error("Failed to upvote", url=post_url[:80], error=str(exc))
            return False
        finally:
            await page.close()
