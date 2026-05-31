"""Example 7 — Import cookies, then comment on a YouTube video.

YouTube auth runs through Google.  The credential flow is the most
checkpoint-heavy login in the ecosystem ("Verify it's you", 2FA, device
prompts).  Steadfast can't solve those interactively — :meth:`import_cookies`
is the only reliable path for headless / server-side automation.

CRITICAL: export cookies from BOTH ``google.com`` AND ``youtube.com``:

  1. Open https://accounts.google.com in your normal browser, sign in.
  2. Install the "Cookie-Editor" Chrome/Firefox extension.
  3. On a `google.com` tab → click Cookie-Editor → Export -> Export as JSON
     → save as `google_cookies.json`.
  4. Open https://www.youtube.com (must redirect through the login flow once).
  5. On a `youtube.com` tab → Export -> Export as JSON
     → save as `youtube_cookies.json`.
  6. Merge them: `cat google_cookies.json youtube_cookies.json > combined.json`
     and edit the result so it's a single JSON list.

Verify your combined file has both `SAPISID` (google) and `LOGIN_INFO`
(youtube):
  python -c 'import json; print({c["name"] for c in json.load(open("combined.json"))} & {"SAPISID","LOGIN_INFO"})'

Run:
  python examples/07_youtube_comment.py <youtube_video_url>
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from steadfast import (
    AntiDetect,
    BrowserManager,
    BrowserManagerConfig,
    configure_logging,
)
from steadfast.platforms import YouTube

COOKIES_FILE = Path(__file__).parent / "youtube_cookies.json"


async def main() -> None:
    configure_logging("INFO")

    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <youtube_video_url>")
        sys.exit(1)
    video_url = sys.argv[1]

    if not COOKIES_FILE.exists():
        print(f"Missing {COOKIES_FILE}. See docstring for export steps.")
        sys.exit(1)

    cookies = json.loads(COOKIES_FILE.read_text())
    domains = {c.get("domain", "") for c in cookies}
    if not any("google.com" in d for d in domains):
        print("No google.com cookies in export — auth state will be incomplete.")
        sys.exit(1)
    if not any("youtube.com" in d for d in domains):
        print("No youtube.com cookies in export — auth state will be incomplete.")
        sys.exit(1)

    bm = BrowserManager(
        BrowserManagerConfig(profiles_dir=Path("./profiles")),
        AntiDetect(),
    )
    await bm.start()

    try:
        yt = YouTube(bm, account_key="my_youtube")

        await yt.import_cookies(cookies)
        print("Cookies imported.")

        if not await yt.ensure_logged_in():
            print("Session invalid — re-export cookies from a fresh browser login.")
            return
        print("Session is valid.")

        result = await yt.comment(video_url, "Great video! 👏")
        if result.success:
            print(f"Comment posted: id={result.platform_post_id}")
            if result.warning:
                print(f"  Note: {result.warning}")
        else:
            print(f"Failed: {result.error}")
    finally:
        await bm.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
