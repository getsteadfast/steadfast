"""Example 10 — Import cookies, then upload a video to TikTok.

Cookie import is **strongly preferred** over credential login — TikTok's
CAPTCHA flow fires on a fresh IP almost every time, and Steadfast can't
solve those interactively.

How to get the cookies file:
  1. Open https://www.tiktok.com in your normal browser, sign in.
  2. Install the "Cookie-Editor" Chrome/Firefox extension.
  3. Click the extension icon while on tiktok.com.
  4. Click "Export" -> "Export as JSON".
  5. Save it as `tiktok_cookies.json` next to this script.

Verify your export contains BOTH `sessionid` AND `sessionid_ss`:
  python -c 'import json; print({c["name"] for c in json.load(open("tiktok_cookies.json"))} & {"sessionid","sessionid_ss"})'

If either is missing, you weren't actually logged in when exporting OR
Cookie-Editor's "current domain only" mode dropped one of them.

Run:
  python examples/10_tiktok_upload.py <video.mp4> "caption text" [privacy]

  privacy: public (default) | friends | private
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
from steadfast.platforms import TikTok

COOKIES_FILE = Path(__file__).parent / "tiktok_cookies.json"


async def main() -> None:
    configure_logging("INFO")

    if len(sys.argv) < 3:
        print(f"Usage: python {sys.argv[0]} <video_path> <caption> [privacy]")
        sys.exit(1)

    video_path = Path(sys.argv[1])
    caption = sys.argv[2]
    privacy = sys.argv[3] if len(sys.argv) > 3 else "public"

    if privacy not in ("public", "friends", "private"):
        print(f"Invalid privacy '{privacy}' — must be public, friends, or private")
        sys.exit(1)

    if not video_path.exists():
        print(f"Video file not found: {video_path}")
        sys.exit(1)

    if not COOKIES_FILE.exists():
        print(f"Missing {COOKIES_FILE}. See docstring for export steps.")
        sys.exit(1)

    cookies = json.loads(COOKIES_FILE.read_text())
    names = {c.get("name") for c in cookies}
    missing = {"sessionid", "sessionid_ss"} - names
    if missing:
        print(f"Cookies file missing required auth cookies: {missing}")
        print("You weren't logged in when exporting, or Cookie-Editor dropped one.")
        sys.exit(1)

    bm = BrowserManager(
        BrowserManagerConfig(profiles_dir=Path("./profiles")),
        AntiDetect(),
    )
    await bm.start()

    try:
        tt = TikTok(bm, account_key="my_tiktok")

        await tt.import_cookies(cookies)
        print("Cookies imported.")

        if not await tt.ensure_logged_in():
            print("Session invalid — re-export cookies from a fresh browser login.")
            return
        print("Session is valid.")

        print(f"Uploading {video_path.name} ({video_path.stat().st_size // 1024} KiB) "
              f"as '{caption[:50]}' ({privacy})…")

        result = await tt.upload(
            video_path,
            caption=caption,
            privacy=privacy,  # type: ignore[arg-type]
        )
        if result.success:
            print(f"Uploaded: id={result.platform_post_id}")
            if result.warning:
                print(f"  Note: {result.warning}")
        else:
            print(f"Failed: {result.error}")
    finally:
        await bm.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
