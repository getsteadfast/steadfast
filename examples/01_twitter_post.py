"""Example 1 — Post a single tweet.

The simplest possible Steadfast script.

Prereqs:
  pip install steadfast
  playwright install chromium

Auth: this example assumes you've already imported cookies for the
account.  See example 02 for the cookie-import flow.

Run:
  python examples/01_twitter_post.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from steadfast import (
    AntiDetect,
    BrowserManager,
    BrowserManagerConfig,
    configure_logging,
)
from steadfast.platforms import Twitter


async def main() -> None:
    configure_logging("INFO")

    bm = BrowserManager(
        BrowserManagerConfig(profiles_dir=Path("./profiles")),
        AntiDetect(),
    )
    await bm.start()

    try:
        twitter = Twitter(bm, account_key="my_twitter")

        # Quick health check first — fail fast if cookies expired.
        if not await twitter.ensure_logged_in():
            print("Not logged in. Run example 02 to import cookies first.")
            return

        result = await twitter.post("Hello from Steadfast!")
        if result.success:
            print(f"Posted: {result.url}")
            if result.warning:
                print(f"  Note: {result.warning}")
        else:
            print(f"Failed: {result.error}")
    finally:
        await bm.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
