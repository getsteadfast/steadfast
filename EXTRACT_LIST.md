# EXTRACT_LIST — Files to copy from MarketPilot snapshot to Steadfast

> Source: `/opt/marketpilot_snapshot_2026-05-29/`
> Destination: `/opt/steadfast/`
> Each file gets imports updated + project-specific references removed.

> **Status (2026-05-29 EOD):** Stage 1 ✅, Stage 2 ✅, Stage 3 ✅, Stage 4 ✅
> (quality bar met: 85 tests passing, `mypy --strict` clean, `ruff` clean,
> all docstrings present, no MarketPilot/SelfTrade refs in code).
> See "Reality check" at the bottom for line-count actuals vs estimates.

---

## Stage 1: Core library (Week 1, Day 4-7) — DONE

Copy these. Strip out: any `campaign_id`, `project_id`, `tenant_id` references; any AI-content-generation imports; any MarketPilot-DB ORM references; any structlog → `log = logging.getLogger(__name__)` simplification.

| Source path | Destination path | Notes |
|---|---|---|
| `core/browser_manager.py` | `steadfast/browser_manager.py` | Main file. Remove MarketPilot config dependencies — make `BrowserManagerConfig` a plain dataclass. |
| `core/anti_detect.py` | `steadfast/anti_detect.py` | Anti-detection scripts. Should be near drop-in. |
| `core/exceptions.py` | `steadfast/exceptions.py` | `BrowserError`, `PlatformError`. Clean copy. |
| `core/logger.py` | `steadfast/_log.py` | Replace structlog → stdlib logging. Rename to `_log.py` (private). |
| `core/utils.py` | `steadfast/utils.py` | Audit first — keep only browser-relevant helpers. |
| `core/remote_display.py` | `steadfast/remote_display.py` | VNC remote display for manual login capture. This is a SELLING POINT — keep. |

**Estimated effort**: 8-12 hours.

---

## Stage 2: Platform clients (Week 2, Day 8-14) — DONE

Strip these to MVP. Each becomes a single class: `login()`, `post()`, `reply()`, `like()`, `get_session_health()`. Remove every reference to MarketPilot's manager / orchestrator / scheduler.

| Source path | Destination path | Notes |
|---|---|---|
| `platforms/twitter/browser_client.py` | `steadfast/platforms/twitter.py` | 1036 lines now (we just patched it 2026-05-29). Trim to ~400 lines. |
| `platforms/linkedin/browser_client.py` | `steadfast/platforms/linkedin.py` | 867 lines. Trim to ~400 lines. |
| `platforms/reddit/browser_client.py` | `steadfast/platforms/reddit.py` | 2319 lines — biggest. Strategy: keep new-reddit + old-reddit flows, drop the experimental subroutines. Target ~600 lines. |
| `platforms/base.py` | `steadfast/platforms/base.py` | Base class. Audit + clean. |

**Estimated effort**: 16-20 hours.

---

## Stage 3: Skip entirely (DO NOT EXTRACT)

These are MarketPilot-specific. They're either: (a) the marketing/campaign layer that's NOT the product, (b) crypto-specific, or (c) needs full rewrite.

- `content/*` — AI content generation. Not the wedge. Skip.
- `dashboard/*` — admin UI for MarketPilot. New SaaS will have its own.
- `core/orchestrator.py` — campaign orchestration. Not the library.
- `core/scheduler.py` — APScheduler-based. SaaS-tier feature, not library.
- `core/job_queue.py` — same.
- `core/strategy_agent.py` — AI brain. Not the wedge.
- `core/ai_*` — AI providers. Not the wedge.
- `core/database.py` + `core/models.py` — MarketPilot ORM. New SaaS will have its own.
- `core/auth.py` — MarketPilot user auth. New SaaS will have its own (FastAPI + JWT).
- `core/circuit_breaker.py` — needed but rebuild simpler.
- `core/metrics.py` — needed but rebuild simpler.
- `core/step_tracker.py` — MarketPilot-specific. Skip.
- `prospecting/*` — sales prospecting features. Skip.
- All other platform integrations (Facebook, Instagram, etc.) — Stage 4 / later.

---

## Stage 4: Defer to v0.2.0+

These are good ideas but NOT v0.1.0 launch features.

| Source | Why deferred |
|---|---|
| Facebook, Instagram, TikTok, YouTube clients | Launch with Twitter / LinkedIn / Reddit only. Add others after first 10 paying customers. |
| Email outreach, WhatsApp, Telegram, Discord clients | Different protocol class (some are API not browser). v0.2.0+. |
| `core/anti_detect.py` advanced fingerprinting | Current MarketPilot version is OK. Improve after first OSS user complaint. |
| `core/connection_manager.py` | LinkedIn-specific. Defer. |
| Observability dashboard | SaaS tier, not library. Build after Stage 1+2 done. |

---

## Quality bar before publishing v0.1.0

A file is "done" when:

- [ ] All imports resolve from inside `steadfast/` (no `core.*` or `platforms.*` leftovers)
- [ ] No references to `campaign_id`, `project_id`, `tenant_id`, `MarketPilot`, `SelfTrade`
- [ ] No DB / ORM imports (the library is stateless w.r.t. DB)
- [ ] No `from core.config import settings` — config is per-call or per-construct
- [ ] All `log.info(...)` calls don't leak any tenant identifiers
- [ ] Passes `ruff check`, `mypy --strict steadfast/`, `pytest tests/`
- [ ] Has a docstring on every public class + method
- [ ] Has at least one example in `examples/` that uses it
- [ ] No `TODO`, `FIXME`, `XXX` comments left

---

## Estimated full Stage 1+2 effort

| Stage | Hours |
|---|---|
| Stage 1 (core lib) | 8-12 |
| Stage 2 (3 platforms) | 16-20 |
| Stage 3 (examples + tests) | 6-8 |
| Stage 4 (docs + polish) | 4-6 |
| **Total to v0.1.0 OSS release** | **34-46 hours** |

At 30 hours/week = 1.5 weeks of focused work. Matches the Week 1-3 portion of the 90-day plan.

---

## Order of operations (recommended)

1. **Day 4** (1h): copy `exceptions.py`, `_log.py`, basic skeleton. Get `import steadfast` to work.
2. **Day 4-5** (4h): port `anti_detect.py`. Write 2 tests (fingerprint persistence, viewport stickiness).
3. **Day 5-7** (6h): port `browser_manager.py`. Write 3 tests (context creation, state.json roundtrip, profile isolation).
4. **Day 8-9** (4h): port `twitter.py` minimal (just `post()` and `login_with_cookies()`). Write 1 test (mocked).
5. **Day 10-11** (4h): port `linkedin.py` minimal. Same test pattern.
6. **Day 12-13** (4h): port `reddit.py` minimal. Same test pattern.
7. **Day 14** (3h): write 5 example scripts. Each must actually run.

Day 1-3 = name + repo setup + scaffolding (you're doing this now).

---

## Reality check (2026-05-29 EOD — post-port)

Actual files on disk after Stage 2:

| File | Pre-port estimate | Actual |
|---|---:|---:|
| `steadfast/platforms/twitter.py` | ~400 | **672** |
| `steadfast/platforms/linkedin.py` | ~400 | **471** |
| `steadfast/platforms/reddit.py` | ~600 | **802** |

The pre-port estimates assumed the post-strip body would only need login + post +
reply + like + session-health. In practice, **each platform has 3-7 UI variants**
(old.reddit + new.reddit + shreddit-composer, X composer Draft.js + execCommand +
CDP, LinkedIn editor + checkpoint flow). Removing fallbacks reaches the targets
but breaks the working flows; defensive code is what makes the lib worth using.

Decision: **keep the platform clients at their current sizes**, not the pre-port
estimates. Re-run this trim only when concrete dead-code is identified (e.g., a
UI variant that no longer exists on the live site).

What WAS trimmed (structural, not surface):
- `reddit.py` 827 → 802 (–25 lines): consolidated `_login_old_reddit` +
  `_login_new_reddit` into a single `_do_login` driven by per-variant selector
  dicts; introduced `_first_selector` + `_click_first_visible` module helpers.
- `twitter.py` 663 → 672 (+9 lines net): added `_tweet_id_from_href` helper at
  three call sites. Net cost is small, but the parsing logic is now a single
  source of truth.
- `linkedin.py` 471 (unchanged): already lean.
