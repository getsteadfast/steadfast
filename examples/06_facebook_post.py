"""Example 6 — Import cookies, then post to Facebook.

Cookie import is **strongly preferred** over credential login for Facebook —
the credential path trips the 2FA / "unusual login" checkpoint on a fresh
IP almost every time, while imported session cookies from an established
browser usually survive for weeks.

How to get the cookies file:
  1. Open https://www.facebook.com in your normal browser, sign in.
  2. Install the "Cookie-Editor" Chrome/Firefox extension.
  3. Click the extension icon while on facebook.com.
  4. Click "Export" -> "Export as JSON".
  5. Save it as `facebook_cookies.json` next to this script.

Verify your export contains BOTH `c_user` and `xs` (the FB auth cookies):
  python -c 'import json; print([c["name"] for c in json.load(open("facebook_cookies.json"))])'

If either is missing, you weren't actually logged in when exporting.

Run:
  python examples/06_facebook_post.py
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
from steadfast.platforms import Facebook

COOKIES_FILE = Path(__file__).parent / "facebook_cookies.json"


async def main() -> None:
    configure_logging("INFO")

    if not COOKIES_FILE.exists():
        print(f"Missing {COOKIES_FILE}. See docstring for export steps.")
        sys.exit(1)

    cookies = json.loads(COOKIES_FILE.read_text())
    names = {c.get("name") for c in cookies}
    missing = {"c_user", "xs"} - names
    if missing:
        print(f"Cookies file is missing {missing}. You weren't logged in when exporting.")
        sys.exit(1)

    bm = BrowserManager(
        BrowserManagerConfig(profiles_dir=Path("./profiles")),
        AntiDetect(),
    )
    await bm.start()

    try:
        fb = Facebook(bm, account_key="my_facebook")

        # One-time setup: import cookies into the profile.
        await fb.import_cookies(cookies)
        print("Cookies imported.")

        # Sanity check that they actually work.
        if not await fb.ensure_logged_in():
            print("Cookies imported but session not valid. Re-export from a fresh login?")
            return
        print("Session is valid.")

        result = await fb.post("Testing Steadfast on Facebook 👋")
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
