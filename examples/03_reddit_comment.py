"""Example 3 — Comment on a Reddit post.

Demonstrates the old.reddit / new.reddit auto-fallback.  Steadfast tries
old.reddit.com first (stable selectors) and falls back to www.reddit.com
if old reddit reports session-expired.

Run:
  python examples/03_reddit_comment.py "<post URL>" "<comment text>"
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from steadfast import (
    AntiDetect,
    BrowserManager,
    BrowserManagerConfig,
    configure_logging,
)
from steadfast.platforms import Reddit


async def main() -> None:
    configure_logging("INFO")

    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    post_url, comment_text = sys.argv[1], sys.argv[2]

    bm = BrowserManager(
        BrowserManagerConfig(profiles_dir=Path("./profiles")),
        AntiDetect(),
    )
    await bm.start()

    try:
        reddit = Reddit(bm, account_key="my_reddit")

        if not await reddit.ensure_logged_in():
            print("Not logged in. Import cookies first (see example 02 pattern).")
            print("Note: Reddit needs BOTH `reddit_session` AND `token_v2` to work.")
            return

        # Steadfast will auto-translate the URL between old/new reddit.
        result = await reddit.comment(post_url=post_url, text=comment_text)
        if result.success:
            print(f"Commented on: {result.url}")
            print(f"  id: {result.platform_post_id}")
        else:
            print(f"Failed: {result.error}")
            # Common errors worth handling:
            #   "Reddit has blocked this IP (network security)" → use a proxy
            #   "Reddit session expired"  → re-import cookies
            #   "Post has been deleted"   → skip
            #   "Post is locked/archived" → skip
            #   "Subreddit restriction"   → need karma/age/approved-user
    finally:
        await bm.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
