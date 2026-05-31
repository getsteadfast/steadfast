"""YouTube browser automation.

YouTube auth runs through **Google** (accounts.google.com), which is the
most checkpoint-heavy login flow Steadfast supports.  Credential login
trips "Verify it's you" + 2FA + "Couldn't sign you in" challenges on a
fresh IP almost every time — :meth:`import_cookies` is the only reliable
auth path for production use.

Capabilities:
  * Comment on videos (text engagement)
  * Like videos (idempotent)
  * Upload videos via YouTube Studio's web wizard
  * Session-health probing + cookie import

**Not implemented** (deliberately deferred):
  * Subscribe / unsubscribe.
  * Search (read-side, scope creep).
  * Scheduled publishing, monetization toggles, custom thumbnails — the
    upload flow stops at "Public / Unlisted / Private" visibility.

Usage::

    from steadfast import AntiDetect, BrowserManager, BrowserManagerConfig
    from steadfast.platforms import YouTube

    bm = BrowserManager(BrowserManagerConfig(profiles_dir="./profiles"), AntiDetect())
    await bm.start()

    yt = YouTube(bm, account_key="my_youtube")
    await yt.import_cookies(cookies_list)   # from Cookie-Editor extension
    assert await yt.ensure_logged_in()

    result = await yt.comment("https://youtu.be/dQw4w9WgXcQ", "Great video!")
    print(result.platform_post_id, result.url)

    await bm.shutdown()
"""

from __future__ import annotations

import asyncio
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from .._log import get_logger
from ..browser_manager import BrowserManager
from ..exceptions import LoginFailed, PlatformError
from ._models import PostResult

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import ElementHandle, Page

log = get_logger("steadfast.youtube")

YOUTUBE_BASE = "https://www.youtube.com"
GOOGLE_LOGIN_URL = "https://accounts.google.com/ServiceLogin"
YOUTUBE_UPLOAD_URL = "https://www.youtube.com/upload"

# YouTube enforces these limits on the upload form. The client truncates
# rather than raising — exceeding either is almost always a typo, not an
# intentional choice that should bubble up as an exception.
_TITLE_MAX_CHARS = 100
_DESC_MAX_CHARS = 5000

# Logged-in indicators on www.youtube.com. The avatar button is the most
# reliable; the other selectors are fallbacks for layouts where the avatar
# hasn't rendered yet.
_LOGGED_IN_INDICATORS = (
    "button#avatar-btn",
    "img.yt-spec-avatar-shape__avatar",
    "#avatar-btn",
    'a[href="/account"]',
    'ytd-topbar-menu-button-renderer[aria-label="Account menu"]',
)

# URL fragments indicating Google's anti-automation challenge flow.
_CHECKPOINT_URL_FRAGMENTS = (
    "challenge",
    "speedbump",
    "rejected",
    "deniedsigninrejected",
    "/signin/v2/challenge",
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


def _clean_video_url(video_url: str) -> str:
    """Strip tracking params like ``&pp=`` that YouTube appends to share URLs.

    The video id is everything before the first tracking-param separator;
    keeping it clean avoids the "this video may not be available" redirect
    that hits when YouTube re-resolves a stale tracking blob.
    """
    return video_url.split("&pp=")[0].split("&t=")[0].split("&feature=")[0]


class YouTube:
    """YouTube browser client for a single account.

    Focused on text engagement (comment + like) for v0.1.0.  Video upload
    will be added in a separate ``YouTubeUploader`` class once the upload
    surface (privacy + scheduled-publish + thumbnail) is designed.
    """

    def __init__(
        self,
        browser_manager: BrowserManager,
        account_key: str = "youtube_primary",
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

    async def _yt_delay(self, min_sec: float = 2.0, max_sec: float = 5.0) -> None:
        """Wait between actions — YouTube's bot detection is impatience-sensitive."""
        await asyncio.sleep(random.uniform(min_sec, max_sec))

    # ----------------------------------------------------------------- Auth

    async def import_cookies(self, cookies: str | list[dict[str, Any]]) -> bool:
        """Import cookies for this account (e.g. from Cookie-Editor extension).

        STRONGLY preferred over :meth:`login` — Google's credential auth
        flow is the most checkpoint-heavy login in the ecosystem.  Imported
        cookies from a real browser session almost always survive longer
        than fresh logins.

        IMPORTANT: export cookies from BOTH ``google.com`` and
        ``youtube.com`` — YouTube reads auth state from both domains.
        Cookie-Editor "Export for current domain" misses one of them.
        """
        ok = await self.browser_manager.import_cookies_to_profile(self.account_key, cookies)
        self._is_logged_in = False
        return ok

    async def get_session_health(self) -> bool:
        """Probe youtube.com and look for the avatar button (= logged-in state).

        Doesn't raise — returns False on any error.  Sets ``_is_logged_in``
        and saves session state on success.
        """
        page = await self._get_page()
        try:
            await page.goto(YOUTUBE_BASE, wait_until="domcontentloaded")
            await self._yt_delay(2.0, 4.0)

            current_url = page.url
            if any(frag in current_url.lower() for frag in _CHECKPOINT_URL_FRAGMENTS):
                log.info("YouTube session invalid — Google challenge URL",
                         account_key=self.account_key, landed=current_url)
                return False

            indicator = await _first_visible(page, _LOGGED_IN_INDICATORS)
            if indicator:
                self._is_logged_in = True
                await self._save_session()
                return True

            return False
        except Exception as exc:
            log.debug("YouTube session check failed", error=str(exc))
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
        """Log in via Google's web flow.

        Prefer :meth:`import_cookies` + :meth:`ensure_logged_in` — Google's
        2FA / "Verify it's you" challenge fires on a fresh IP almost every
        time, and Steadfast can't solve those interactively.

        Raises :class:`LoginFailed` on any failure, including challenges.
        """
        if self._is_logged_in:
            return True
        if await self.get_session_health():
            return True

        page = await self._get_page()
        try:
            await page.goto(GOOGLE_LOGIN_URL, wait_until="domcontentloaded")
            await self._yt_delay(2.0, 4.0)

            # Step 1: email
            await self._anti_detect.human_like_type(page, 'input[type="email"]', email)
            await self._anti_detect.short_pause()
            await page.click("#identifierNext")
            await self._yt_delay(3.0, 6.0)

            # Step 1.5: Google often shows "verify it's you" between email
            # and password; bail with a clear error rather than looping.
            if any(frag in page.url.lower() for frag in _CHECKPOINT_URL_FRAGMENTS):
                raise LoginFailed(
                    "youtube",
                    f"Google challenge after email step for {email} (landed on "
                    f"{page.url}). Use import_cookies() instead.",
                )

            # Step 2: password
            await self._anti_detect.human_like_type(
                page, 'input[type="password"]', password
            )
            await self._anti_detect.short_pause()
            await page.click("#passwordNext")
            await self._yt_delay(5.0, 10.0)

            # Step 3: post-login challenge check
            if any(frag in page.url.lower() for frag in _CHECKPOINT_URL_FRAGMENTS):
                raise LoginFailed(
                    "youtube",
                    f"Google challenge after password step for {email} (landed on "
                    f"{page.url}). Use import_cookies() instead.",
                )

            # Step 4: verify on youtube.com
            await page.goto(YOUTUBE_BASE, wait_until="domcontentloaded")
            await self._yt_delay(3.0, 5.0)

            indicator = await _first_visible(page, _LOGGED_IN_INDICATORS)
            if not indicator:
                raise LoginFailed(
                    "youtube",
                    f"Login verification failed for {email} — no avatar visible",
                )

            self._is_logged_in = True
            await self._save_session()
            log.info("YouTube login successful", email=email)
            return True
        except LoginFailed:
            raise
        except Exception as exc:
            raise LoginFailed("youtube", f"Login error for {email}: {exc}") from exc
        finally:
            await page.close()

    # --------------------------------------------------------------- Comment

    # Placeholder is the collapsed "Add a comment..." UI element that expands
    # into the editable input.  Multiple variants exist as YouTube migrates
    # away from polymer-paper to web components.
    _COMMENT_PLACEHOLDERS = (
        "#simplebox-placeholder",
        "ytd-comment-simplebox-renderer #placeholder-area",
        "tp-yt-paper-input-container #placeholder-area",
        "ytd-comments-header-renderer #placeholder-area",
    )

    # The actual editable input that appears after the placeholder is clicked.
    _COMMENT_EDITABLE = (
        "div#contenteditable-root[contenteditable='true']",
        "#contenteditable-root",
    )

    # Submit button — only enables after text is typed.
    _COMMENT_SUBMIT = (
        "#submit-button ytd-button-renderer#submit-button",
        "ytd-comment-simplebox-renderer #submit-button",
        "#submit-button button",
        "#submit-button",
    )

    async def comment(self, video_url: str, text: str) -> PostResult:
        """Post a comment on a YouTube video.

        Returns a :class:`PostResult`.  ``platform_post_id`` is synthetic
        (``yt_comment_<timestamp>``) — YouTube does not surface the
        permanent comment ID synchronously after submission.

        Returns ``success=False`` with a clear error if comments are
        disabled on the target video (a common case worth handling
        explicitly).
        """
        page = await self._get_page()
        try:
            clean_url = _clean_video_url(video_url)
            await page.goto(clean_url, wait_until="domcontentloaded")
            await self._yt_delay(3.0, 6.0)

            # YouTube lazy-loads comments when the section enters the
            # viewport.  Scroll until they render (or give up).
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, 400)")
                await self._yt_delay(1.0, 2.0)

            try:
                await page.wait_for_selector(
                    "ytd-comments#comments, ytd-comment-simplebox-renderer",
                    timeout=15000,
                )
            except Exception:
                return PostResult(
                    success=False,
                    error="Comments section did not load — comments may be disabled",
                )

            await self._yt_delay(1.0, 3.0)

            # Click the placeholder to expand the comment box. If no
            # placeholder is visible, try the editable area directly (some
            # variants render the editable input straight away).
            placeholder_clicked = await _click_first_visible(page, self._COMMENT_PLACEHOLDERS)
            if not placeholder_clicked and not await _click_first_visible(
                page, self._COMMENT_EDITABLE
            ):
                return PostResult(
                    success=False,
                    error="Comment input area not found — comments may be disabled",
                )
            await self._yt_delay(1.0, 3.0)

            # Wait for the editable to be live.
            editable = None
            for sel in self._COMMENT_EDITABLE:
                try:
                    editable = await page.wait_for_selector(sel, timeout=10000)
                    if editable:
                        break
                except Exception:
                    continue
            if not editable:
                raise PlatformError(
                    "youtube", "Editable comment input did not appear after click"
                )

            # Type via page.keyboard so React state updates fire.
            await editable.click()
            await self._yt_delay(0.5, 1.0)
            await page.keyboard.type(text, delay=random.randint(30, 80))
            await self._yt_delay(1.0, 3.0)

            # Verify the text actually landed (shadow-DOM / iframe issues
            # would leave the editable empty).
            typed = await editable.inner_text()
            if not typed or len(typed.strip()) < min(5, len(text.strip())):
                return PostResult(
                    success=False,
                    error="Comment text did not enter the editor — possible shadow-DOM block",
                )

            if not await _click_first_visible(page, self._COMMENT_SUBMIT):
                raise PlatformError("youtube", "Comment submit button not found / not enabled")

            await self._yt_delay(3.0, 6.0)
            await self._save_session()

            synthetic = f"yt_comment_{int(datetime.now(timezone.utc).timestamp())}"
            log.info("YouTube comment posted", account_key=self.account_key,
                     video_url=clean_url[:80], length=len(text))
            return PostResult(
                success=True,
                platform_post_id=synthetic,
                url=clean_url,
                text_preview=text[:100],
            )
        except PlatformError as exc:
            return PostResult(success=False, error=str(exc))
        except Exception as exc:
            log.error("Failed to comment on YouTube video",
                      video_url=video_url[:80], error=str(exc))
            return PostResult(success=False, error=str(exc))
        finally:
            await page.close()

    # ------------------------------------------------------------------ Like

    _LIKE_BUTTONS = (
        "like-button-view-model button",
        "#top-level-buttons-computed ytd-toggle-button-renderer:first-child button",
        "ytd-menu-renderer button[aria-label*='like' i]",
    )

    async def like(self, video_url: str) -> bool:
        """Like a YouTube video.

        Idempotent — if the video is already liked (``aria-pressed=true``),
        returns True without clicking.  Returns False if the button isn't
        found or on session expiry.  Doesn't raise.
        """
        page = await self._get_page()
        try:
            await page.goto(_clean_video_url(video_url), wait_until="domcontentloaded")
            await self._yt_delay(3.0, 6.0)

            # Simulate watching for a beat — YouTube logs immediate-click
            # patterns as bot signal.
            await self._anti_detect.page_read_delay(300)

            like_btn = None
            for sel in self._LIKE_BUTTONS:
                try:
                    like_btn = await page.wait_for_selector(sel, timeout=5000)
                    if like_btn:
                        break
                except Exception:
                    continue
            if not like_btn:
                log.info("YouTube like button not found", video_url=video_url[:80])
                return False

            pressed = await like_btn.get_attribute("aria-pressed")
            if pressed == "true":
                log.info("Video already liked", video_url=video_url[:80])
                return True

            await like_btn.click()
            await self._yt_delay(1.0, 3.0)
            await self._save_session()
            log.info("YouTube video liked", video_url=video_url[:80])
            return True
        except Exception as exc:
            log.warning("YouTube like failed",
                        video_url=video_url[:80], error=str(exc))
            return False
        finally:
            await page.close()

    # ---------------------------------------------------------------- Upload

    # YouTube Studio's upload wizard runs the user through:
    # Details → Video elements → Checks → Visibility → publish.
    # The selectors below target each step's primary control.
    _UPLOAD_TITLE = (
        'ytcp-mention-textbox#title-textarea div#textbox[contenteditable="true"]',
    )
    _UPLOAD_DESCRIPTION = (
        'ytcp-mention-textbox#description-textarea div#textbox[contenteditable="true"]',
    )
    _UPLOAD_NEXT = ("#next-button", "ytcp-button#next-button")
    _UPLOAD_DONE = ("#done-button", "ytcp-button#done-button")
    _UPLOAD_PRIVACY: dict[str, str] = {
        "public": 'tp-yt-paper-radio-button[name="PUBLIC"]',
        "unlisted": 'tp-yt-paper-radio-button[name="UNLISTED"]',
        "private": 'tp-yt-paper-radio-button[name="PRIVATE"]',
    }
    _UPLOAD_VIDEO_LINK = (
        'a.style-scope.ytcp-video-info[href*="youtu"]',
        'a[href*="youtube.com/video"]',
        'a[href*="youtu.be"]',
    )
    _VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|/video/)([a-zA-Z0-9_-]{11})")

    async def upload(
        self,
        video_path: str | Path,
        title: str,
        description: str = "",
        privacy: Literal["public", "unlisted", "private"] = "public",
    ) -> PostResult:
        """Upload a video to YouTube via the Studio web wizard.

        Title is truncated to 100 chars, description to 5000 — YouTube
        enforces both server-side, so the truncation here just avoids a
        spurious error from the form-validation step.

        ``privacy`` controls the Visibility step: ``"public"`` (default),
        ``"unlisted"``, or ``"private"``.  Scheduled publishing,
        monetization, and custom thumbnails are out of scope for v0.1.0.

        On success the returned :class:`PostResult` carries the 11-character
        video id in ``platform_post_id`` and the full ``youtube.com/watch?v=…``
        URL in ``url``.  If the post-publish dialog doesn't surface a link
        within the wait window, ``platform_post_id`` is empty and a
        ``warning`` explains.
        """
        path_obj = Path(video_path)
        if not path_obj.exists():
            return PostResult(success=False, error=f"Video file not found: {path_obj}")

        page = await self._get_page()
        try:
            await page.goto(YOUTUBE_UPLOAD_URL, wait_until="domcontentloaded")
            await self._yt_delay(3.0, 6.0)

            # Step 1 — file input. Studio's upload picker accepts files via
            # the standard `<input type="file">`; we set it directly rather
            # than clicking through the OS picker.
            file_input = await page.wait_for_selector('input[type="file"]', timeout=20000)
            if not file_input:
                raise PlatformError("youtube", "Upload file input not found")
            await file_input.set_input_files(str(path_obj))
            await self._yt_delay(3.0, 6.0)

            # Step 2 — title (replace the auto-filled filename).
            await self._fill_field(page, self._UPLOAD_TITLE, title[:_TITLE_MAX_CHARS],
                                   required="title")

            # Step 3 — description (optional).
            if description:
                try:
                    await self._fill_field(page, self._UPLOAD_DESCRIPTION,
                                           description[:_DESC_MAX_CHARS],
                                           required="description")
                except PlatformError:
                    # Description field is sometimes lazy-rendered; treat as
                    # non-fatal.
                    log.warning("Description input not found — skipping",
                                account_key=self.account_key)

            # Step 4 — declare not-for-kids (YouTube blocks publish without it).
            try:
                radio = await page.wait_for_selector(
                    'tp-yt-paper-radio-button[name="NOT_MADE_FOR_KIDS"]', timeout=5000,
                )
                if radio:
                    await radio.click()
                    await self._yt_delay(0.5, 1.5)
            except Exception:
                pass  # not-for-kids picker can be in a collapsed section

            # Step 5 — three "Next" clicks to traverse the wizard.
            for _ in range(3):
                next_btn = await page.wait_for_selector(
                    ", ".join(self._UPLOAD_NEXT), timeout=10000,
                )
                if not next_btn:
                    raise PlatformError("youtube", "Could not advance upload wizard")
                await next_btn.click()
                await self._yt_delay(2.0, 4.0)

            # Step 6 — Visibility (privacy choice).
            await self._select_privacy(page, privacy)
            await self._yt_delay(1.0, 3.0)

            # Step 7 — wait for processing to be far enough along to publish.
            await self._wait_for_upload_ready(page)

            # Step 8 — Publish / Done.
            done_btn = await page.wait_for_selector(
                ", ".join(self._UPLOAD_DONE), timeout=15000,
            )
            if not done_btn:
                raise PlatformError("youtube", "Publish button not found")
            await done_btn.click()
            await self._yt_delay(3.0, 6.0)

            # Step 9 — extract the published video URL from the success dialog.
            published_url = await self._extract_published_url(page)
            video_id = ""
            if published_url:
                m = self._VIDEO_ID_RE.search(published_url)
                if m:
                    video_id = m.group(1)

            await self._save_session()

            if video_id:
                log.info("YouTube video uploaded", account_key=self.account_key,
                         video_id=video_id, title=title[:60])
                return PostResult(
                    success=True,
                    platform_post_id=video_id,
                    url=published_url,
                    text_preview=title[:100],
                )

            log.warning("Upload published but video URL not surfaced",
                        account_key=self.account_key, title=title[:60])
            return PostResult(
                success=True,
                platform_post_id="",
                url="https://studio.youtube.com",
                text_preview=title[:100],
                warning="Video published but URL not extractable — check Studio dashboard",
            )
        except PlatformError as exc:
            return PostResult(success=False, error=str(exc))
        except Exception as exc:
            log.error("YouTube upload failed",
                      title=title[:60], error=str(exc))
            return PostResult(success=False, error=str(exc))
        finally:
            await page.close()

    # ── Upload helpers ────────────────────────────────────────────────

    async def _fill_field(
        self, page: Page, selectors: tuple[str, ...], value: str, required: str
    ) -> None:
        """Click into a Studio mention-textbox, clear it, and type ``value``.

        Studio's textboxes are ``contenteditable`` divs (not real inputs) —
        ``.fill()`` would bypass React's input listener, so we use
        ``page.keyboard`` instead.  ``required`` is used in the error
        message when no selector in ``selectors`` matches.
        """
        field: ElementHandle | None = None
        for sel in selectors:
            try:
                field = await page.wait_for_selector(sel, timeout=15000)
                if field:
                    break
            except Exception:
                continue
        if not field:
            raise PlatformError("youtube", f"{required.capitalize()} input not found")

        await field.click()
        await self._yt_delay(0.3, 0.8)
        await page.keyboard.press("Control+a")
        await self._yt_delay(0.2, 0.5)
        await page.keyboard.type(value, delay=random.randint(30, 70))
        await self._yt_delay(1.0, 3.0)

    async def _select_privacy(self, page: Page, privacy: str) -> None:
        """Click the radio button for the requested privacy level.

        Unknown values fall back to ``public`` — matches the MarketPilot
        behavior and avoids accidentally privating a video on a typo.
        """
        selector = self._UPLOAD_PRIVACY.get(
            privacy.lower(), self._UPLOAD_PRIVACY["public"]
        )
        try:
            radio = await page.wait_for_selector(selector, timeout=8000)
            if radio:
                await radio.click()
        except Exception:
            log.warning("Could not set privacy — leaving default",
                        privacy=privacy)

    async def _wait_for_upload_ready(self, page: Page, max_wait_sec: int = 300) -> None:
        """Poll Studio's progress UI until the Publish button is enabled.

        YouTube allows publishing before processing fully completes — once
        the upload bar is past the "uploading" phase, the done button
        un-disables.  We give up after ``max_wait_sec`` and try publishing
        anyway (the worst case is the wizard surfaces an explicit error).
        """
        log.info("Waiting for upload processing", account_key=self.account_key)
        for i in range(max_wait_sec // 5):
            done = await page.query_selector("#done-button:not([disabled])")
            if done:
                return
            progress = await page.query_selector(
                'ytcp-video-upload-progress[processing], '
                'span.progress-label:has-text("Uploading"), '
                'span.progress-label:has-text("Processing")'
            )
            if not progress and i > 6:
                # No progress bar AND we've waited 30s+ — assume ready.
                return
            await asyncio.sleep(5)
        log.warning("Upload-processing wait timed out — attempting publish anyway")

    async def _extract_published_url(self, page: Page) -> str:
        """Pull the watch URL from Studio's post-publish dialog. ``""`` on miss."""
        for sel in self._UPLOAD_VIDEO_LINK:
            try:
                link = await page.wait_for_selector(sel, timeout=10000)
                if link:
                    href = await link.get_attribute("href")
                    if href:
                        return href
            except Exception:
                continue
        # Fallback: regex-scrape the dialog body.
        try:
            dialog = await page.query_selector(
                'ytcp-uploads-dialog .dialog-content, #dialog-content'
            )
            if dialog:
                text = await dialog.inner_text()
                m = re.search(
                    r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[a-zA-Z0-9_-]+)',
                    text,
                )
                if m:
                    return m.group(1)
        except Exception:
            pass
        return ""
