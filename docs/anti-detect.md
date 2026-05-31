# Anti-detection

Steadfast's anti-detect layer has two halves:

1. **Static patches** injected into every browser context via Playwright's
   `add_init_script`.  These hide the well-known automation tells.
2. **Behavioural helpers** on `AntiDetect` — proxies, sticky UAs, sticky
   viewports, human-like delays + clicks + typing.

This page explains both.

## What's in the init script

The script runs in every page before any site JS executes.  Source:
[`steadfast/browser_manager.py`](https://github.com/getsteadfast/steadfast/blob/main/steadfast/browser_manager.py),
constant `_ANTI_DETECT_INIT_JS`.

### `navigator.webdriver` → `undefined`

The most-flagged automation signal.  Vanilla Playwright sets
`navigator.webdriver = true`; we override it back to `undefined` and
remove the prototype binding so it can't be re-detected via `delete`.

### `window.chrome` shim

Headless Chrome has no `window.chrome` object — that's a dead giveaway.
We inject a minimal shim with the methods sites check for: `runtime`,
`loadTimes`, `csi`.

### `Notification.permission` via `navigator.permissions.query`

Headless Chrome returns `denied` regardless of the actual state.  Real
Chrome returns whatever the user has granted.  We patch
`permissions.query` to return `Notification.permission` directly for the
`notifications` permission so the values agree.

### `navigator.plugins` realistic list

Headless Chrome returns an empty `plugins` array.  Real Chrome returns
~3 plugins (PDF viewer, PDF plugin, Native Client).  We inject the
realistic three.

### `navigator.languages`

Headless defaults to `['en-US']`.  Real browsers carry the full list:
`['en-US', 'en']`.  We patch that.

### `navigator.maxTouchPoints` / `hardwareConcurrency` / `deviceMemory`

Hardware-info giveaways.  Headless Chrome reports `hardwareConcurrency=1`
and leaves `deviceMemory` undefined — both suspicious on a desktop site.
We pin them to common production values (`8` and `8`).  `maxTouchPoints=0`
asserts "this is a desktop" — most automation runs on desktop sites and
shouldn't claim to have touch.

### `navigator.connection.rtt`

When `navigator.connection` exists, set `rtt` to a plausible 50ms.
Sites use this to fingerprint network quality.

### WebGL vendor + renderer

Headless Chrome reports `Google Inc.` / `ANGLE (Google, SwiftShader …)`
for the GPU vendor + renderer strings via the `WEBGL_debug_renderer_info`
extension.  This is the single most-flagged automation signal after
`navigator.webdriver` itself.  We replace both with a common Intel
integrated-GPU pair (`Intel Inc.` / `Intel Iris OpenGL Engine`).  Like the
`navigator` overrides, this is a constant value replacement — same
fingerprint across every call, same as a real browser on the same machine.

### iframe shadow-DOM detection bypass

Some sites detect Playwright by trying to attach a shadow DOM to an
element inside an iframe and observing the error.  We monkey-patch
`Element.prototype.attachShadow` to make sure the call goes through
normally.

## What's in the behaviour layer

These live on `AntiDetect`.  They're optional but defaults are
reasonable.

### Sticky proxies

When you construct `AntiDetect(proxies=[...])`, each `account_key`
gets a deterministic assignment via `hash(account_key) % len(proxies)`.
Same account → same proxy on every run, persisted to `fingerprint.json`.

If a proxy goes dead, `replace_dead_proxy()` picks a new one from the
pool and updates the fingerprint file.

### Sticky user agents

Same idea, smaller pool.  `DEFAULT_USER_AGENTS` carries six UAs
representing Chrome / Firefox / Safari on Windows / Mac / Linux.

### Sticky viewport

`COMMON_VIEWPORTS` contains the six most common screen resolutions on
the web.  Each `account_key` gets a deterministic pick.

### Human-like delays

```python
await ad.random_delay(1.0, 5.0)     # Gaussian, clamped
await ad.short_pause()              # 0.3-1.5s
await ad.typing_delay(text_length)  # 40-80 WPM
await ad.page_read_delay(content_length)  # 200-300 WPM reading speed
```

All return immediately if you pass them small numbers — no minimum
floor enforced.  Use them between UI interactions to look human.

### Human-like clicks and typing

```python
await ad.human_like_click(page, "button.foo")
# Moves the mouse with random `steps` count, lands at a random point
# inside the bounding box (20-80% from each edge), pauses briefly, clicks.

await ad.human_like_type(page, "input.foo", "hello")
# Types one character at a time with 30-150ms inter-key delay.
# 5% of characters get an extra 0.3-0.8s "thinking pause".
```

These take longer than `fill()` but look completely different in a
behavioural-analytics flow.

### Random mouse movement

`await ad.random_mouse_movement(page)` moves the cursor to a random
point in the viewport.  Cheap.  Use it between unrelated steps to
avoid the "mouse stays motionless for 20 seconds" pattern.

### Human-like scroll

`await ad.human_like_scroll(page)` scrolls 3-8 times with small
variance.  Use before reading a page, not before clicking the next
button.

## What we explicitly don't do

- **Canvas / Audio fingerprint noise injection.**  Per-call randomness
  (e.g. flipping the LSB of pixel data on every `toDataURL` call) makes
  the browser *more* uniquely identifiable, not less, because real
  browsers are deterministic per session.  We patch the WebGL renderer
  string (constant value, same approach as the `navigator` overrides),
  but we deliberately leave Canvas and AudioContext alone.
- **Random delay everywhere.**  Adding a global "sleep 0.5-2 seconds
  before every action" makes scripts unbearably slow without
  measurably improving detection.  We add delays specifically where
  human behaviour would have a delay (between UI interactions on the
  same page) and skip them otherwise.
- **Captcha solving.**  Not in scope.  If a platform throws a captcha,
  the right move is to re-export your cookies from a session that
  hasn't tripped one.

## What you should do

- Run your scripts under realistic per-account proxies — residential
  if your budget allows, datacenter as a fallback.  Mismatched
  geography (US cookies coming from a German datacenter) is the
  single biggest signal Steadfast can't hide.
- Don't run dozens of accounts concurrently on the same IP.  Reddit
  in particular tracks this aggressively.
- Don't post 100 times in five minutes.  Even with perfect
  anti-detection, rate gives you away.
- Treat session expiry as a signal to re-export cookies, not as a
  reason to call `login()` and burn another login attempt against
  the platform's checkpoint flow.
