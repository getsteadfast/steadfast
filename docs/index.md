# Steadfast

**Browser agents that don't lose their sessions.**

Steadfast is a Python library for running headless-browser automation
across multiple accounts and platforms — with anti-detection, session
persistence, and per-account isolation built in.

[![Tests](https://img.shields.io/badge/tests-85%20passing-brightgreen)]()
[![License](https://img.shields.io/badge/license-Apache--2.0%20with%20Commons%20Clause-blue)]()
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)]()

> ⚠️ **Status: pre-alpha.** APIs may shift before v0.1.0.  Pin a commit if
> you depend on this in production today.

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
pip install steadfast
playwright install chromium
```

```python
import asyncio
from pathlib import Path
from steadfast import AntiDetect, BrowserManager, BrowserManagerConfig
from steadfast.platforms import Twitter

async def main():
    bm = BrowserManager(
        BrowserManagerConfig(profiles_dir=Path("./profiles")),
        AntiDetect(),
    )
    await bm.start()

    twitter = Twitter(bm, account_key="my_twitter")
    await twitter.import_cookies(open("twitter_cookies.json").read())
    assert await twitter.ensure_logged_in()

    result = await twitter.post("Hello from Steadfast!")
    print(result.url)

    await bm.shutdown()

asyncio.run(main())
```

That's it.  No login flow, no captcha solver, no session-expiry handling
in your code.  The cookies you imported keep working for weeks.

---

## What's in v0.1.0

### Platforms

| Platform | Auth | Post | Reply / Comment | Like / Upvote | Health check |
|---|:---:|:---:|:---:|:---:|:---:|
| **Twitter / X** | cookies / login | ✅ | ✅ | ✅ | ✅ |
| **LinkedIn** | cookies / login | ✅ | ✅ (comment) | ✅ | ✅ |
| **Reddit** | cookies / login | ✅ | ✅ (comment) | ✅ (upvote) | ✅ |

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

See [`examples/`](./examples/) for runnable scripts:

| # | File | Demonstrates |
|---|---|---|
| 1 | [01_twitter_post.py](./examples/01_twitter_post.py) | Simplest possible post |
| 2 | [02_linkedin_with_cookies.py](./examples/02_linkedin_with_cookies.py) | Cookie import + post (recommended auth pattern) |
| 3 | [03_reddit_comment.py](./examples/03_reddit_comment.py) | old.reddit / new.reddit auto-fallback |
| 4 | [04_session_save_restore.py](./examples/04_session_save_restore.py) | Sessions survive BrowserManager restart |
| 5 | [05_multi_account.py](./examples/05_multi_account.py) | Two accounts running concurrently on one BM |

---

## Documentation

- [Getting started](./docs/getting-started.md) — install, first run, troubleshooting
- [Auth & sessions](./docs/auth-and-sessions.md) — the wedge, explained
- [Anti-detection](./docs/anti-detect.md) — what's in the init script and why

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
    _models.py             # PostResult dataclass
tests/                     # 85 tests, < 1s to run
examples/                  # 5 runnable scripts
docs/                      # mkdocs site
```

---

## Contributing

Bug reports + PRs welcome on GitHub.  Before submitting:

```bash
pip install -e ".[dev]"
ruff check steadfast/ tests/
pytest -q
```

The full test suite runs in under one second and doesn't require launching
a real browser — most of the real-browser code paths are tested via the
[examples](./examples/) on real platforms.

---

## License

Apache 2.0 with Commons Clause — see [LICENSE](./LICENSE).

You can use Steadfast for any commercial or non-commercial purpose,
including paid client work.  You can't host it as a competing SaaS
product.  If that's what you want, contact us about licensing.
