"""Example 9 — Import cookies, then post an image to Instagram.

Cookie import is **strongly preferred** over credential login —
Instagram's "Suspicious Login Attempt" challenge fires on a fresh IP
almost every time, and Steadfast can't solve those interactively.

How to get the cookies file:
  1. Open https://www.instagram.com in your normal browser, sign in.
  2. Install the "Cookie-Editor" Chrome/Firefox extension.
  3. Click the extension icon while on instagram.com.
  4. Click "Export" -> "Export as JSON".
  5. Save it as `instagram_cookies.json` next to this script.

Verify your export contains `sessionid` (the only IG auth cookie that
matters):
  python -c 'import json; print({c["name"] for c in json.load(open("instagram_cookies.json"))} & {"sessionid"})'

If `sessionid` is missing, you weren't actually logged in when exporting.

Run:
  python examples/09_instagram_post.py <image.jpg> "caption text"
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
from steadfast.platforms import Instagram

COOKIES_FILE = Path(__file__).parent / "instagram_cookies.json"


async def main() -> None:
    configure_logging("INFO")

    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <image_path> [caption]")
        sys.exit(1)

    image_path = Path(sys.argv[1])
    caption = sys.argv[2] if len(sys.argv) > 2 else ""

    if not image_path.exists():
        print(f"Image file not found: {image_path}")
        sys.exit(1)

    if not COOKIES_FILE.exists():
        print(f"Missing {COOKIES_FILE}. See docstring for export steps.")
        sys.exit(1)

    cookies = json.loads(COOKIES_FILE.read_text())
    if not any(c.get("name") == "sessionid" for c in cookies):
        print("Cookies file has no `sessionid`. You weren't logged in when exporting.")
        sys.exit(1)

    bm = BrowserManager(
        BrowserManagerConfig(profiles_dir=Path("./profiles")),
        AntiDetect(),
    )
    await bm.start()

    try:
        ig = Instagram(bm, account_key="my_instagram")

        await ig.import_cookies(cookies)
        print("Cookies imported.")

        if not await ig.ensure_logged_in():
            print("Session invalid — re-export cookies from a fresh browser login.")
            return
        print("Session is valid.")

        result = await ig.post(image_path, caption=caption)
        if result.success:
            print(f"Posted: id={result.platform_post_id}")
            if result.warning:
                print(f"  Note: {result.warning}")
        else:
            print(f"Failed: {result.error}")
    finally:
        await bm.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
