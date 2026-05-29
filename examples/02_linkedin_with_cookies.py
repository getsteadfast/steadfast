"""Example 2 — Import cookies, then post to LinkedIn.

This is the **recommended** auth pattern for any platform.  Credential login
on LinkedIn trips security checkpoints almost every time; real cookies
exported from a logged-in browser session work for weeks to months.

How to get the cookies file:
  1. Open https://www.linkedin.com in your normal browser, sign in.
  2. Install the "Cookie-Editor" Chrome/Firefox extension.
  3. Click the extension icon while on linkedin.com.
  4. Click "Export" -> "Export as JSON".
  5. Save it as `linkedin_cookies.json` next to this script.

Verify your export contains `li_at` (the LinkedIn session cookie):
  python -c 'import json; print([c["name"] for c in json.load(open("linkedin_cookies.json"))])'

If `li_at` is missing, you weren't actually logged in when exporting.

Run:
  python examples/02_linkedin_with_cookies.py
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
from steadfast.platforms import LinkedIn

COOKIES_FILE = Path(__file__).parent / "linkedin_cookies.json"


async def main() -> None:
    configure_logging("INFO")

    if not COOKIES_FILE.exists():
        print(f"Missing {COOKIES_FILE}. See docstring for export steps.")
        sys.exit(1)

    cookies = json.loads(COOKIES_FILE.read_text())
    if not any(c.get("name") == "li_at" for c in cookies):
        print("Cookies file has no `li_at`. You weren't logged in when exporting.")
        sys.exit(1)

    bm = BrowserManager(
        BrowserManagerConfig(profiles_dir=Path("./profiles")),
        AntiDetect(),
    )
    await bm.start()

    try:
        li = LinkedIn(bm, account_key="my_linkedin")

        # One-time setup: import cookies into the profile.
        await li.import_cookies(cookies)
        print("Cookies imported.")

        # Sanity check that they actually work.
        if not await li.ensure_logged_in():
            print("Cookies imported but session not valid. Re-export?")
            return
        print("Session is valid.")

        result = await li.post("Testing Steadfast on LinkedIn 👋")
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
