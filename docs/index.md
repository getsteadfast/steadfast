# Steadfast

**Browser agents that don't lose their sessions.**

Steadfast is a Python library for running headless-browser automation
across multiple accounts and platforms — with anti-detection, session
persistence, and per-account isolation built in.

[![Tests](https://img.shields.io/badge/tests-130%20passing-brightgreen)]()
[![License](https://img.shields.io/badge/license-Apache--2.0%20with%20Commons%20Clause-blue)]()
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)]()
[![PyPI](https://img.shields.io/badge/pypi-steadfast--browser-orange)](https://pypi.org/project/steadfast-browser/)

---

## Why Steadfast?

Every browser-agent platform — Stagehand, Browser Use, Skyvern, Anchor
Browser, Hyperbrowser — has the same #1 customer complaint:

> *"My agent gets logged out every 4 hours."*

That's a solved problem.  Steadfast solves it like this:

| Feature | Steadfast | Vanilla Playwright | Stagehand / Browser Use |
|---|:---:|:---:|:---:|
| **Per-account fingerprint isolation** | ✅ | ❌ | partial |
| **Anti-detect init scripts built in** | ✅ | ❌ | partial |
| **Cookie import from browser extension** | ✅ | manual | manual |
| **Session capture via VNC (manual login)** | ✅ | ❌ | ❌ |
| **Session persistence across runs** | ✅ | manual | partial |
| **Sticky viewport + UA + proxy per account** | ✅ | manual | ❌ |
| **Old-reddit + new-reddit auto-fallback** | ✅ | ❌ | ❌ |

---

## Quickstart

```bash
pip install steadfast-browser    # dist name on PyPI
playwright install chromium
```

> **Note on the name:** the package is installed as `steadfast-browser`
> on PyPI (the bare `steadfast` name was taken by an unrelated 2023
> package), but it imports as `steadfast` — so `pip install steadfast-browser`,
> then `from steadfast import ...` in your code.

```python
import asyncio
import json
from pathlib import Path
from steadfast import AntiDetect, BrowserManager, BrowserManagerConfig
from steadfast.platforms import Twitter

async def main():
    bm = BrowserManager(
        BrowserManagerConfig(profiles_dir=Path("./profiles")),
        AntiDetect(),
    )
    await bm.start()
    try:
        twitter = Twitter(bm, account_key="my_twitter")
        await twitter.import_cookies(
            json.loads(Path("twitter_cookies.json").read_text())
        )
        assert await twitter.ensure_logged_in()
        result = await twitter.post("Hello from Steadfast!")
        print(result.url)
    finally:
        await bm.shutdown()

asyncio.run(main())
```

That's it.  No login flow, no captcha solver, no session-expiry handling
in your code.  The cookies you imported keep working for weeks.

---

## What's in v0.1.0

### Platforms

| Platform | Auth | Post | Comment | Like | Upload | Health |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **Twitter / X** | cookies / login | ✅ | ✅ (reply) | ✅ | — | ✅ |
| **LinkedIn** | cookies / login | ✅ | ✅ | ✅ | — | ✅ |
| **Reddit** | cookies / login | ✅ | ✅ | ✅ (upvote) | — | ✅ |
| **Facebook** | cookies / login | ✅ | ✅ | ✅ | — | ✅ |
| **Instagram** | cookies / login | ✅ (image) | ✅ | ✅ | — | ✅ |
| **YouTube** | cookies / login | — | ✅ | ✅ | ✅ (video) | ✅ |

### Core library

- `BrowserManager` — Playwright pool with per-account contexts, concurrency limits, lifecycle helpers.
- `AntiDetect` — sticky proxies, sticky user agents, sticky viewports, human-like delays + clicks + typing, plus anti-automation init scripts injected into every context.
- `RemoteDisplay` — optional virtual-display + VNC server for manual-login flows on headless servers.
- `PostResult` — slotted dataclass returned by every post/reply/comment.

### Exception hierarchy

```python
SteadfastError
├── BrowserError       # Playwright launch / context creation
├── ProxyError         # proxy pool issues
└── PlatformError      # base for platform-specific errors
    ├── LoginFailed
    ├── RateLimited
    └── AccountSuspended
```

---

## Examples

Runnable scripts live in the [examples directory on GitHub](https://github.com/getsteadfast/steadfast/tree/main/examples):

| # | File | Demonstrates |
|---|---|---|
| 1 | [01_twitter_post.py](https://github.com/getsteadfast/steadfast/blob/main/examples/01_twitter_post.py) | Simplest possible post |
| 2 | [02_linkedin_with_cookies.py](https://github.com/getsteadfast/steadfast/blob/main/examples/02_linkedin_with_cookies.py) | Cookie import + post (recommended auth pattern) |
| 3 | [03_reddit_comment.py](https://github.com/getsteadfast/steadfast/blob/main/examples/03_reddit_comment.py) | old.reddit / new.reddit auto-fallback |
| 4 | [04_session_save_restore.py](https://github.com/getsteadfast/steadfast/blob/main/examples/04_session_save_restore.py) | Sessions survive BrowserManager restart |
| 5 | [05_multi_account.py](https://github.com/getsteadfast/steadfast/blob/main/examples/05_multi_account.py) | Two accounts running concurrently on one BM |
| 6 | [06_facebook_post.py](https://github.com/getsteadfast/steadfast/blob/main/examples/06_facebook_post.py) | Facebook composer-dialog flow |
| 7 | [07_youtube_comment.py](https://github.com/getsteadfast/steadfast/blob/main/examples/07_youtube_comment.py) | YouTube comment with Google-domain + youtube-domain cookies |
| 8 | [08_youtube_upload.py](https://github.com/getsteadfast/steadfast/blob/main/examples/08_youtube_upload.py) | Video upload via YouTube Studio wizard |
| 9 | [09_instagram_post.py](https://github.com/getsteadfast/steadfast/blob/main/examples/09_instagram_post.py) | Single-image post via Instagram web composer |

---

## Documentation

- [Getting started](getting-started.md) — install, first run, troubleshooting
- [Auth & sessions](auth-and-sessions.md) — the wedge, explained
- [Anti-detection](anti-detect.md) — what's in the init script and why

---

## Why an open-core SaaS exists

The library above runs anywhere.  But running it *at scale* — many tenants,
each with many accounts, with observability, retries, scheduling, and
session-rescue automation — is operational work most engineers don't want
to write.

The hosted version of Steadfast does that part, billed monthly.  More info
at *(coming soon)*.

---

## Project layout

```
steadfast/
  __init__.py              # public exports
  browser_manager.py       # Playwright pool
  anti_detect.py           # proxies, UAs, viewports, human-like helpers
  exceptions.py            # error hierarchy
  remote_display.py        # Xvfb + x11vnc for manual-login flows
  _log.py                  # KV-rendering logging adapter
  utils.py                 # small helpers (utcnow, async_retry, ...)
  platforms/
    twitter.py
    linkedin.py
    reddit.py
    facebook.py
    instagram.py
    youtube.py
    _models.py             # PostResult dataclass
tests/                     # 130 unit tests + 3 real-browser integration tests
examples/                  # 9 runnable scripts
docs/                      # mkdocs site
```

---

## Contributing

Bug reports + PRs welcome on [GitHub](https://github.com/getsteadfast/steadfast).  Before submitting:

```bash
pip install -e ".[dev]"
ruff check steadfast/ tests/
mypy --strict steadfast/
pytest                                  # 130 unit tests, ~1s
pytest -m integration tests/integration # 3 real-browser tests, ~3s
```

The full unit suite runs in under one second and doesn't require launching
a real browser — most real-browser code paths are exercised by the
integration tests, which spin up a chromium context to verify the
anti-detect init script actually applies in-browser.

---

## License

Apache 2.0 with Commons Clause — see [LICENSE on GitHub](https://github.com/getsteadfast/steadfast/blob/main/LICENSE).

You can use Steadfast for any commercial or non-commercial purpose,
including paid client work.  You can't host it as a competing SaaS
product.  If that's what you want, contact us at
[hello@steadfast.dev](mailto:hello@steadfast.dev) about a commercial
license.
