"""Example 8 — Upload a video to YouTube via the Studio web wizard.

This is the second YouTube example (07_youtube_comment.py covers
engagement).  Both share the same browser profile + cookies — the
upload here will use whatever session 07 imported.

Usage:
  python examples/08_youtube_upload.py <video_file.mp4> "Video title" [privacy]

  privacy: public (default) | unlisted | private

Requirements:
  - youtube_cookies.json already set up (see 07_youtube_comment.py docstring).
  - The account must have completed YouTube channel creation at least once.
    First-time uploads from a channel-less account get blocked at the
    "create channel" wizard; Steadfast can't auto-fill that step.
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

    if len(sys.argv) < 3:
        print(f"Usage: python {sys.argv[0]} <video_file> <title> [privacy]")
        sys.exit(1)

    video_path = Path(sys.argv[1])
    title = sys.argv[2]
    privacy = sys.argv[3] if len(sys.argv) > 3 else "public"

    if privacy not in ("public", "unlisted", "private"):
        print(f"Invalid privacy '{privacy}' — must be public, unlisted, or private")
        sys.exit(1)

    if not video_path.exists():
        print(f"Video file not found: {video_path}")
        sys.exit(1)

    if not COOKIES_FILE.exists():
        print(f"Missing {COOKIES_FILE}. See examples/07_youtube_comment.py for setup.")
        sys.exit(1)

    cookies = json.loads(COOKIES_FILE.read_text())

    bm = BrowserManager(
        BrowserManagerConfig(profiles_dir=Path("./profiles")),
        AntiDetect(),
    )
    await bm.start()

    try:
        yt = YouTube(bm, account_key="my_youtube")
        await yt.import_cookies(cookies)

        if not await yt.ensure_logged_in():
            print("Session invalid — re-export cookies from a fresh browser login.")
            return

        print(f"Uploading {video_path.name} ({video_path.stat().st_size // 1024} KiB) "
              f"as '{title[:50]}' ({privacy})…")

        result = await yt.upload(
            video_path,
            title=title,
            description="Uploaded via Steadfast — https://steadfast.dev",
            privacy=privacy,  # type: ignore[arg-type]
        )

        if result.success:
            if result.platform_post_id:
                print(f"Published: video_id={result.platform_post_id}")
                print(f"  URL: {result.url}")
            else:
                print("Published but URL extraction failed — check Studio dashboard.")
                if result.warning:
                    print(f"  Note: {result.warning}")
        else:
            print(f"Failed: {result.error}")
    finally:
        await bm.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
