"""Example 5 — Run two accounts concurrently from one BrowserManager.

Each account gets its own:
  * Browser context (isolated cookies + localStorage)
  * Sticky fingerprint (viewport + user-agent + proxy)
  * Profile directory (./profiles/<account_key>/)

So even if both accounts are on the same platform (or use the same site),
they remain fully isolated.  This is the multi-tenant building block.

Run:
  python examples/05_multi_account.py
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


async def post_for(twitter: Twitter, text: str) -> None:
    """Wrapper that prints success/failure for one account's post."""
    if not await twitter.ensure_logged_in():
        print(f"[{twitter.account_key}] not logged in — skipping")
        return
    result = await twitter.post(text)
    label = twitter.account_key
    if result.success:
        print(f"[{label}] posted: {result.url or '(URL unverified)'}")
    else:
        print(f"[{label}] failed: {result.error}")


async def main() -> None:
    configure_logging("INFO")

    # Optional: give each context a different proxy. Steadfast's sticky
    # assignment hashes account_key % len(proxies), so each account uses
    # the same proxy on every run — but two accounts likely use different
    # ones, making them appear to come from different network sources.
    anti_detect = AntiDetect(
        proxies=[
            # Replace with your real proxies, or use AntiDetect() with no args.
            # ProxyInfo("http", "proxy-a.example", 8080, "user", "pass"),
            # ProxyInfo("http", "proxy-b.example", 8080, "user", "pass"),
        ],
    )

    bm = BrowserManager(
        BrowserManagerConfig(profiles_dir=Path("./profiles"), max_concurrent=4),
        anti_detect,
    )
    await bm.start()

    try:
        alice = Twitter(bm, account_key="alice_twitter")
        bob = Twitter(bm, account_key="bob_twitter")

        # Run concurrently — each on its own context, fully isolated.
        await asyncio.gather(
            post_for(alice, "Hi from Alice's account"),
            post_for(bob, "Hi from Bob's account"),
        )
    finally:
        await bm.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
