# Changelog

All notable changes to Steadfast will be documented in this file.  Format:
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).  Versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial pre-alpha extraction from the operator's internal MarketPilot codebase.
- `BrowserManager` + `BrowserManagerConfig` — Playwright pool with per-account contexts, sticky fingerprints, cookie-import, save/restore.
- `AntiDetect` + `ProxyInfo` — proxies, user agents, viewports, human-like helpers, optional proxy health checks via aiohttp.
- `RemoteDisplay` — Xvfb + x11vnc launcher for headed-browser flows on headless servers.
- Exception hierarchy: `SteadfastError`, `BrowserError`, `ProxyError`, `PlatformError`, `LoginFailed`, `RateLimited`, `AccountSuspended`.
- Platforms: `Twitter`, `LinkedIn`, `Reddit` — each with `import_cookies`, `ensure_logged_in`, `login`, `get_session_health`, `post`, `comment`/`reply`, `like`/`upvote`.
- `PostResult` dataclass returned by every post/reply/comment operation.
- 85 tests, all run without launching a real browser.
- Logging adapter that renders `log.info("msg", k=v)` as `msg k='v'` over stdlib `logging`.

### Notes
- v0.1.0 is the planned first public release.  Until then, APIs may change without warning.
- Twitter `post` returns a `PostResult` with a synthetic `unverified-<ts>` `platform_post_id` when the post lands but URL extraction fails.  This is intentional — preferring it over `success=False` so callers don't repost duplicates.
- Reddit's URL helpers (`_to_old_reddit_url`, `_to_new_reddit_url`) handle the `old.reddit.com` ⊂ `reddit.com` substring case explicitly.  Chained `.replace()` would double-rewrite.
- LinkedIn's `post()` returns an empty `url` and a synthetic `li_post_<timestamp>` `platform_post_id` — LinkedIn does not surface the permalink synchronously.

[Unreleased]: https://github.com/getsteadfast/steadfast/commits/main
