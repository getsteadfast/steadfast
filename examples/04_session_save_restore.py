"""Example 4 — Session save / restore across BrowserManager lifecycle.

Demonstrates the core value prop: **sessions survive**.

Sequence:
  1. Open Twitter, verify logged in via existing cookies.
  2. Shut down the BrowserManager entirely.
  3. Open a fresh BrowserManager pointed at the same profiles dir.
  4. Verify we're still logged in — no re-auth needed.

This is the test that distinguishes Steadfast from vanilla Playwright,
where you'd have to re-import cookies (or re-login) every single run.

Run:
  python examples/04_session_save_restore.py
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

PROFILES = Path("./profiles")
ACCOUNT_KEY = "my_twitter"


async def check_session() -> bool:
    """Open a fresh BM, check Twitter session health, close it cleanly."""
    bm = BrowserManager(
        BrowserManagerConfig(profiles_dir=PROFILES),
        AntiDetect(),
    )
    await bm.start()
    try:
        twitter = Twitter(bm, account_key=ACCOUNT_KEY)
        return await twitter.get_session_health()
    finally:
        await bm.shutdown()


async def main() -> None:
    configure_logging("INFO")

    print("Run 1 — checking session...")
    ok1 = await check_session()
    print(f"  Session valid: {ok1}")
    if not ok1:
        print("Need to import cookies first. See example 02.")
        return

    print()
    print("BrowserManager has fully shut down. Spinning up a brand new one...")
    print()

    print("Run 2 — checking session in a fresh BM instance...")
    ok2 = await check_session()
    print(f"  Session valid: {ok2}")

    if ok1 and ok2:
        print()
        print("✓ Session survives BrowserManager restart.")
        print("  This is the wedge — vanilla Playwright would have lost it.")


if __name__ == "__main__":
    asyncio.run(main())
