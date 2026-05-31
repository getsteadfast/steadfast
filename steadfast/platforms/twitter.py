"""Twitter / X browser automation.

One :class:`Twitter` instance per account. All methods operate on that
account; multi-account work creates multiple `Twitter` instances over the
same :class:`BrowserManager`.

Typical usage::

    from steadfast import AntiDetect, BrowserManager, BrowserManagerConfig
    from steadfast.platforms import Twitter

    bm = BrowserManager(BrowserManagerConfig(profiles_dir="./profiles"), AntiDetect())
    await bm.start()

    twitter = Twitter(bm, account_key="my_twitter")

    # If you already have cookies from a browser extension:
    await twitter.import_cookies(cookies_list)
    await twitter.ensure_logged_in()

    result = await twitter.post("Hello from Steadfast")
    print(result.url, result.platform_post_id)

    await bm.shutdown()
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .._log import get_logger
from ..browser_manager import BrowserManager
from ..exceptions import LoginFailed, PlatformError
from ._models import PostResult

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import ElementHandle, Page

log = get_logger("steadfast.twitter")

TWITTER_BASE = "https://x.com"


def _tweet_id_from_href(href: str) -> str:
    """Pull a tweet id out of any URL containing ``/status/<id>``.

    Returns ``""`` if the URL doesn't have a status segment. Strips the
    query string and any trailing path component.
    """
    if "/status/" not in href:
        return ""
    return href.split("/status/")[-1].split("?")[0].split("/")[0]


def _require_handle(handle: ElementHandle | None, what: str) -> ElementHandle:
    """Narrow ``Optional[ElementHandle]`` → ``ElementHandle`` or raise.

    Playwright's ``wait_for_selector`` raises ``TimeoutError`` when the
    target doesn't appear within the timeout (default state is
    ``visible``), so the ``None`` branch is unreachable in practice — but
    the type stubs still surface it.  This helper turns "supposedly
    unreachable" into a concrete ``PlatformError`` with the call-site name.
    """
    if handle is None:
        raise PlatformError("twitter", f"Required element returned null: {what}")
    return handle


class Twitter:
    """Twitter/X browser client for a single account."""

    def __init__(self, browser_manager: BrowserManager, account_key: str = "twitter_primary") -> None:
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
        """Import cookies for this account (from Cookie-Editor extension etc.).

        After import the next browser context picks up the new state. The
        in-memory logged-in flag is invalidated so we re-check on next use.
        """
        ok = await self.browser_manager.import_cookies_to_profile(self.account_key, cookies)
        self._is_logged_in = False
        return ok

    # ----------------------------------------------------------------- Auth

    async def get_session_health(self) -> bool:
        """Cheap probe: navigate to /home and check for logged-in markers.

        Doesn't raise. Returns True iff the saved session resolves to a
        logged-in /home view.
        """
        page = await self._get_page()
        try:
            await page.goto(f"{TWITTER_BASE}/home", wait_until="domcontentloaded")
            await self._anti_detect.random_delay(2.0, 4.0)

            if "/login" in page.url or "/i/flow/login" in page.url:
                log.info("Session invalid — redirected to login", account_key=self.account_key)
                return False

            logged_in = await page.query_selector(
                '[data-testid="primaryColumn"], '
                '[data-testid="AppTabBar_Home_Link"], '
                '[data-testid="SideNav_AccountSwitcher_Button"]'
            )
            if logged_in:
                await self._save_session()
                self._is_logged_in = True
                return True

            log.info("Session invalid — no logged-in indicators", account_key=self.account_key)
            return False
        except Exception as exc:
            log.debug("Session check failed", error=str(exc))
            return False
        finally:
            await page.close()

    async def ensure_logged_in(
        self,
        username: str | None = None,
        password: str | None = None,
        email: str = "",
    ) -> bool:
        """Return True if logged in; if not and credentials provided, try login.

        Cookie-based usage:
            await twitter.import_cookies(...)
            assert await twitter.ensure_logged_in()

        Credential usage:
            assert await twitter.ensure_logged_in(username="...", password="...")
        """
        if self._is_logged_in:
            return True
        if await self.get_session_health():
            return True
        if username and password:
            return await self.login(username=username, password=password, email=email)
        return False

    async def login(self, username: str, password: str, email: str = "") -> bool:
        """Log in via the web flow. Raises :class:`LoginFailed` on failure.

        Prefer cookie import (`import_cookies`) when possible — credential
        login is more likely to trigger Twitter's anti-bot checks.
        """
        if self._is_logged_in:
            return True
        if await self.get_session_health():
            return True

        page = await self._get_page()
        try:
            log.info("Logging in with credentials", username=username)

            await page.goto(f"{TWITTER_BASE}/i/flow/login", wait_until="domcontentloaded")
            await self._anti_detect.random_delay(3.0, 6.0)

            # Step 1: username
            await page.wait_for_selector(
                'input[autocomplete="username"], input[name="text"], input[type="text"]',
                timeout=15000,
            )
            await self._anti_detect.human_like_type(
                page,
                'input[autocomplete="username"], input[name="text"], input[type="text"]',
                username,
            )
            await self._anti_detect.short_pause()

            next_btn = _require_handle(
                await page.wait_for_selector(
                    'button:has-text("Next"), div[role="button"]:has-text("Next")',
                    timeout=10000,
                ),
                "Next button (login step 1)",
            )
            await next_btn.click()
            await self._anti_detect.random_delay(2.0, 4.0)

            # Step 1.5 (optional): email / phone verification
            try:
                verify = await page.wait_for_selector(
                    'input[data-testid="ocfEnterTextTextInput"], '
                    'input[name="text"][autocomplete="on"]',
                    timeout=5000,
                )
                if verify:
                    await verify.fill(email or username)
                    await self._anti_detect.short_pause()
                    vbtn = _require_handle(
                        await page.wait_for_selector(
                            'button:has-text("Next"), div[role="button"]:has-text("Next")',
                            timeout=5000,
                        ),
                        "Next button (login step 1.5 verify)",
                    )
                    await vbtn.click()
                    await self._anti_detect.random_delay(2.0, 4.0)
            except Exception:
                pass  # no verify step

            # Step 2: password
            await page.wait_for_selector(
                'input[name="password"], input[type="password"]', timeout=10000
            )
            await self._anti_detect.human_like_type(
                page,
                'input[name="password"], input[type="password"]',
                password,
            )
            await self._anti_detect.short_pause()

            login_btn = _require_handle(
                await page.wait_for_selector(
                    'button[data-testid="LoginForm_Login_Button"], '
                    'div[role="button"]:has-text("Log in"), '
                    'button:has-text("Log in")',
                    timeout=10000,
                ),
                "Log in button (login step 2)",
            )
            await login_btn.click()
            await self._anti_detect.random_delay(3.0, 7.0)

            # Verify
            try:
                await page.wait_for_selector(
                    '[data-testid="primaryColumn"], '
                    '[data-testid="AppTabBar_Home_Link"], '
                    'a[aria-label="Profile"], '
                    '[data-testid="SideNav_AccountSwitcher_Button"]',
                    timeout=15000,
                )
            except Exception as exc:
                err_el = await page.query_selector(
                    '[data-testid="error-detail"], '
                    'span:has-text("Wrong password"), '
                    'span:has-text("unusual login")'
                )
                err_text = await err_el.inner_text() if err_el else ""
                raise LoginFailed("twitter", f"Login verification failed: {err_text}") from exc

            self._is_logged_in = True
            await self._save_session()
            log.info("Login successful", username=username)
            return True
        except LoginFailed:
            raise
        except Exception as exc:
            log.error("Login error", username=username, error=str(exc))
            raise LoginFailed("twitter", f"Login failed: {exc}") from exc
        finally:
            await page.close()

    # ------------------------------------------------------------- Composer

    async def _find_post_button(self, page: Page) -> ElementHandle | None:
        """Locate the post/tweet/reply button.  Returns None if missing."""
        for selector in ("[data-testid='tweetButton']", "[data-testid='tweetButtonInline']"):
            btn = await page.query_selector(selector)
            if btn:
                return btn
        return None

    async def _type_into_composer(self, page: Page, textbox: ElementHandle, text: str) -> bool:
        """Type into Twitter's Draft.js composer using the best of 3 strategies.

        Returns True iff at least 80% of `text` landed in the composer and a
        post button is present.
        """
        strategies = (
            ("CDP", self._strategy_cdp_paste),
            ("execCommand", self._strategy_exec_command),
            ("keyboard", self._strategy_keyboard_type),
        )

        expected = len(text.strip())
        best_content = ""
        best_ratio = 0.0

        for name, strategy in strategies:
            try:
                await strategy(page, textbox, text)
                await asyncio.sleep(1.0)
                content = await textbox.inner_text()
                btn = await self._find_post_button(page)
                btn_disabled = (
                    await btn.get_attribute("aria-disabled") if btn else "no_button"
                )

                content_len = len(content.strip())
                ratio = content_len / expected if expected else 0
                log.info(
                    "Composer strategy result",
                    strategy=name,
                    content_len=content_len,
                    expected=expected,
                    ratio=f"{ratio:.0%}",
                    button_found=btn is not None,
                    button_aria_disabled=btn_disabled,
                )
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_content = content

                if content.strip() and ratio >= 0.95 and btn and btn_disabled != "true":
                    return True

                if content.strip() and ratio >= 0.80 and btn:
                    # Nudge Draft.js — sometimes the button enables late.
                    try:
                        await textbox.evaluate(
                            "el => {el.dispatchEvent(new Event('input', {bubbles: true}));"
                            "el.dispatchEvent(new Event('change', {bubbles: true}));}"
                        )
                        await asyncio.sleep(0.3)
                        await page.keyboard.press("End")
                        await page.keyboard.press("Space")
                        await asyncio.sleep(0.2)
                        await page.keyboard.press("Backspace")
                        await asyncio.sleep(0.5)
                    except Exception:
                        pass
                    for _ in range(8):
                        await asyncio.sleep(0.5)
                        btn = await self._find_post_button(page)
                        btn_disabled = (
                            await btn.get_attribute("aria-disabled") if btn else "true"
                        )
                        if btn_disabled != "true":
                            return True

                # Clear and try next strategy
                await textbox.click()
                await page.keyboard.press("Control+a")
                await page.keyboard.press("Backspace")
                await asyncio.sleep(0.5)
            except Exception as exc:
                log.warning("Composer strategy failed", strategy=name, error=str(exc))

        if best_ratio >= 0.80:
            log.warning("Using best attempt below 95%", best_ratio=f"{best_ratio:.0%}")
            return True
        if best_ratio >= 0.50:
            log.warning("Using partial content (50-80%)", best_ratio=f"{best_ratio:.0%}")
            return True

        log.error(
            "All composer strategies failed",
            best_ratio=f"{best_ratio:.0%}",
            best_content_preview=best_content[:80],
        )
        return False

    async def _strategy_keyboard_type(self, page: Page, textbox: ElementHandle, text: str) -> None:
        await textbox.click()
        await page.keyboard.type(text, delay=25)

    async def _strategy_exec_command(self, page: Page, textbox: ElementHandle, text: str) -> None:
        await textbox.click()
        await textbox.evaluate(
            "(el, text) => { el.focus(); document.execCommand('insertText', false, text); }",
            text,
        )

    async def _strategy_cdp_paste(self, page: Page, textbox: ElementHandle, text: str) -> None:
        await textbox.click()
        cdp = await page.context.new_cdp_session(page)
        try:
            await cdp.send("Input.insertText", {"text": text})
        finally:
            await cdp.detach()

    # ------------------------------------------------------------------ Post

    async def post(self, text: str, media_paths: list[str] | None = None) -> PostResult:
        """Post a new tweet.

        Returns a :class:`PostResult`.  On success-with-no-URL the result is
        still ``success=True`` but ``platform_post_id`` is a synthetic
        ``unverified-<ts>`` and ``warning`` is set — preferring this over
        ``success=False`` so we never lose a real post or repost a duplicate.
        """
        page = await self._get_page()
        try:
            log.info("Posting tweet", account_key=self.account_key, text_len=len(text), text_preview=text[:60])

            await page.goto(f"{TWITTER_BASE}/compose/tweet", wait_until="domcontentloaded")
            await self._anti_detect.random_delay(2.0, 4.0)

            tweet_box = _require_handle(
                await page.wait_for_selector(
                    '[data-testid="tweetTextarea_0"], '
                    'div[role="textbox"][data-testid="tweetTextarea_0"], '
                    'div[role="textbox"]',
                    timeout=10000,
                ),
                "tweet composer textbox",
            )
            await tweet_box.click()
            await self._anti_detect.short_pause()
            text_ok = await self._type_into_composer(page, tweet_box, text)

            if not text_ok:
                actual = await tweet_box.inner_text()
                if not actual.strip():
                    return PostResult(success=False, error="Failed to enter tweet text into composer")
                ratio = len(actual.strip()) / len(text.strip()) if text.strip() else 0
                if ratio < 0.5:
                    return PostResult(
                        success=False,
                        error=f"Tweet text truncated to {ratio:.0%} — aborting to avoid partial post",
                    )

            if media_paths:
                try:
                    file_input = _require_handle(
                        await page.wait_for_selector(
                            'input[data-testid="fileInput"], input[type="file"]', timeout=5000
                        ),
                        "media file input",
                    )
                    for path in media_paths[:4]:
                        if Path(path).exists():
                            await file_input.set_input_files(path)
                            await self._anti_detect.random_delay(1.0, 3.0)
                except Exception as exc:
                    log.warning("Could not attach media", error=str(exc))

            post_btn = await self._find_post_button(page)
            if not post_btn:
                post_btn = _require_handle(
                    await page.wait_for_selector(
                        '[data-testid="tweetButton"], '
                        '[data-testid="tweetButtonInline"], '
                        'button:has-text("Post")',
                        timeout=10000,
                    ),
                    "post button (fallback selector)",
                )

            for _ in range(20):
                if await post_btn.get_attribute("aria-disabled") != "true":
                    break
                await asyncio.sleep(0.5)
            await post_btn.click(force=True)
            await self._anti_detect.random_delay(3.0, 6.0)
            await page.wait_for_load_state("domcontentloaded")

            tweet_id, tweet_url, post_confirmed = await self._confirm_post(page, text)

            if not post_confirmed:
                return PostResult(success=False, error="Could not confirm tweet was posted")

            if not tweet_id:
                tweet_id, tweet_url = await self._fetch_url_from_profile(page, text)

            if not tweet_id:
                synthetic = f"unverified-{int(time.time())}"
                log.warning(
                    "Tweet posted but URL extraction failed — synthetic id",
                    account_key=self.account_key,
                    synthetic_id=synthetic,
                )
                await self._save_session()
                return PostResult(
                    success=True,
                    platform_post_id=synthetic,
                    url=f"{TWITTER_BASE}/{self.account_key}",
                    text_preview=text[:100],
                    warning="URL extraction failed — verify manually on profile",
                )

            await self._save_session()
            log.info("Tweet posted", account_key=self.account_key, tweet_id=tweet_id)
            return PostResult(
                success=True,
                platform_post_id=tweet_id,
                url=tweet_url,
                text_preview=text[:100],
            )
        except Exception as exc:
            log.error("Failed to post tweet", account_key=self.account_key, error=str(exc))
            return PostResult(success=False, error=str(exc))
        finally:
            await page.close()

    async def _confirm_post(self, page: Page, text: str) -> tuple[str, str, bool]:
        """Wait for a toast or composer-closed redirect. Returns (id, url, confirmed)."""
        tweet_id = ""
        tweet_url = ""
        confirmed = False
        try:
            toast = await page.wait_for_selector(
                '[data-testid="toast"], span:has-text("Your post was sent"), '
                'span:has-text("Your Tweet was sent"), span:has-text("post was sent")',
                timeout=10000,
            )
            if toast:
                confirmed = True
                link_el = await page.query_selector('[data-testid="toast"] a')
                if link_el:
                    href = await link_el.get_attribute("href") or ""
                    if href and not href.startswith("http"):
                        href = f"{TWITTER_BASE}{href}"
                    tweet_id = _tweet_id_from_href(href)
                    if tweet_id:
                        tweet_url = href
        except Exception:
            pass

        if not confirmed:
            await asyncio.sleep(2.0)
            if "/compose/tweet" not in page.url:
                confirmed = True
                log.info("Post likely succeeded — compose dialog closed")
            else:
                composer = await page.query_selector(
                    '[data-testid="tweetTextarea_0"], div[role="textbox"]'
                )
                if composer:
                    remaining = await composer.inner_text()
                    if remaining.strip() and remaining.strip() == text.strip():
                        log.error("Tweet NOT posted — composer still has text")

        return tweet_id, tweet_url, confirmed

    async def _fetch_url_from_profile(self, page: Page, text: str) -> tuple[str, str]:
        """After a successful post with no toast-link, look up the most-recent
        tweet on the user's profile.  Returns (tweet_id, tweet_url).

        Strategy: wait for CDN, try profile + with_replies, skip pinned, scan
        up to 8 articles.
        """
        await asyncio.sleep(5.0)
        for profile_path in (f"/{self.account_key}", f"/{self.account_key}/with_replies"):
            try:
                await page.goto(f"{TWITTER_BASE}{profile_path}", wait_until="domcontentloaded")
                await asyncio.sleep(3.0)
                articles = await page.query_selector_all('article[data-testid="tweet"]')
                for art in articles[:8]:
                    try:
                        pin = await art.query_selector('[data-testid="socialContext"]')
                        if pin and "pinned" in (await pin.inner_text() or "").lower():
                            continue
                        link_el = await art.query_selector('a[href*="/status/"]')
                        if not link_el:
                            continue
                        href = await link_el.get_attribute("href") or ""
                        if href and not href.startswith("http"):
                            href = f"{TWITTER_BASE}{href}"
                        tid = _tweet_id_from_href(href)
                        if tid:
                            log.info(
                                "Fetched tweet URL from profile fallback",
                                tweet_id=tid,
                                path=profile_path,
                            )
                            return tid, href
                    except Exception:
                        continue
            except Exception as exc:
                log.warning(
                    "Profile fallback for tweet URL failed",
                    path=profile_path,
                    error=str(exc),
                )
        return "", ""

    # ----------------------------------------------------------------- Reply

    async def reply(self, tweet_url: str, text: str) -> PostResult:
        """Reply to a tweet by URL.

        Returns a :class:`PostResult`.  ``platform_post_id`` will be
        ``"reply-to-<parent_id>"`` on success (the actual reply id requires
        a follow-up page load which we skip in v0.1.0).
        """
        page = await self._get_page()
        try:
            log.info("Replying to tweet", tweet_url=tweet_url[:80])
            await page.goto(tweet_url, wait_until="domcontentloaded")
            await self._anti_detect.random_delay(2.0, 5.0)

            reply_icon = await page.query_selector('[data-testid="reply"]')
            if reply_icon:
                await reply_icon.click()
                await self._anti_detect.random_delay(1.5, 3.0)

            reply_box = _require_handle(
                await page.wait_for_selector(
                    '[data-testid="tweetTextarea_0"], div[role="textbox"]', timeout=10000
                ),
                "reply composer textbox",
            )
            await reply_box.click()
            await self._anti_detect.short_pause()
            text_ok = await self._type_into_composer(page, reply_box, text)

            if not text_ok:
                actual = await reply_box.inner_text()
                if not actual.strip():
                    return PostResult(success=False, error="Failed to enter reply text into composer")
                ratio = len(actual.strip()) / len(text.strip()) if text.strip() else 0
                if ratio < 0.5:
                    return PostResult(success=False, error=f"Reply text truncated to {ratio:.0%}")

            reply_btn = await self._find_post_button(page)
            if not reply_btn:
                reply_btn = _require_handle(
                    await page.wait_for_selector(
                        '[data-testid="tweetButtonInline"], button:has-text("Reply")',
                        timeout=10000,
                    ),
                    "reply submit button (fallback selector)",
                )
            for _ in range(20):
                if await reply_btn.get_attribute("aria-disabled") != "true":
                    break
                await asyncio.sleep(0.5)
            await reply_btn.click(force=True)
            await self._anti_detect.random_delay(2.0, 5.0)

            confirmed = False
            try:
                toast = await page.wait_for_selector(
                    '[data-testid="toast"], span:has-text("Your reply was sent"), '
                    'span:has-text("Your post was sent"), span:has-text("reply was sent")',
                    timeout=10000,
                )
                if toast:
                    confirmed = True
            except Exception:
                pass

            if not confirmed:
                await asyncio.sleep(2.0)
                composer = await page.query_selector(
                    '[data-testid="tweetTextarea_0"], div[role="textbox"]'
                )
                if composer:
                    remaining = await composer.inner_text()
                    if remaining.strip() == text.strip():
                        return PostResult(
                            success=False, error="Reply was not posted — composer still has text"
                        )
                    confirmed = True
                else:
                    confirmed = True

            if not confirmed:
                return PostResult(success=False, error="Could not confirm reply was posted")

            await self._save_session()
            parent_id = _tweet_id_from_href(tweet_url)
            log.info("Reply posted", tweet_url=tweet_url[:80])
            return PostResult(
                success=True,
                platform_post_id=f"reply-to-{parent_id}" if parent_id else tweet_url,
                url=tweet_url,
                text_preview=text[:100],
            )
        except Exception as exc:
            log.error("Failed to reply", tweet_url=tweet_url[:80], error=str(exc))
            return PostResult(success=False, error=str(exc))
        finally:
            await page.close()

    # ------------------------------------------------------------------ Like

    async def like(self, tweet_url: str) -> bool:
        """Like a tweet by URL. Returns True iff the like button was clicked.

        Returns False if the tweet was already liked or the button wasn't found.
        """
        page = await self._get_page()
        try:
            await page.goto(tweet_url, wait_until="domcontentloaded")
            await self._anti_detect.random_delay(1.5, 3.5)
            like_btn = await page.query_selector(
                '[data-testid="like"]:not([data-testid="unlike"])'
            )
            if like_btn:
                await like_btn.click()
                await self._anti_detect.short_pause()
                log.info("Tweet liked", tweet_url=tweet_url[:80])
                return True
            log.info("Tweet already liked or button not found", tweet_url=tweet_url[:80])
            return False
        except Exception as exc:
            log.error("Failed to like tweet", tweet_url=tweet_url[:80], error=str(exc))
            return False
        finally:
            await page.close()
