# Why most browser agents lose their sessions — and how to fix it

If you've shipped a browser agent in production, you've felt this:

> *Worked yesterday.  Logged out today.  No idea why.*

It's the most common operational pain point in the category.  Stagehand,
Browser Use, Skyvern, Anchor Browser — every funded platform has the
same #1 customer complaint, and they all answer it the same way:
re-import your cookies.

That's not wrong.  But it misses the layer below.  This post is about
what really binds a session to a browser, and what you have to keep
constant for the session to survive.

## What "logged in" actually means

When a platform considers you logged in, they're not just checking your
auth cookie.  They're checking that the entire shape of your browser
matches the shape they saw when they issued the cookie.

The auth cookie is the *claim*.  The fingerprint is the *evidence*.

If the evidence stops matching the claim, modern platforms invalidate
the session — they call it a "security check" but it's really fingerprint
drift detection.  Twitter does it.  LinkedIn does it.  Reddit does it.

## What's in the fingerprint

A non-exhaustive list of what platforms hash to identify "this browser":

- **User agent.**  The literal `navigator.userAgent` string.
- **Viewport.**  Window dimensions plus device-pixel ratio.
- **Language.**  `navigator.languages` array, in order.
- **Plugins.**  `navigator.plugins` length and member names.
- **WebGL renderer.**  Reported GPU name + driver version.
- **Canvas hash.**  Pixel-by-pixel result of rendering a test image.
- **Audio fingerprint.**  Audio-context output for a deterministic input.
- **Fonts.**  System fonts available to the browser.
- **Time zone.**  `Intl.DateTimeFormat().resolvedOptions().timeZone`.
- **IP geo.**  ASN, country, sometimes city.
- **`navigator.webdriver`.**  Always `true` from vanilla Playwright.

Reset *any* of these between runs and you're rolling the dice on whether
the session survives.  Reset two or three together and you're guaranteed
to fail the check.

## The four-hour logout

This is the pattern almost everyone hits:

1. You bootstrap the bot by importing fresh cookies.  Session works.
2. You run the bot.  It posts something.  Still working.
3. You shut the bot down for the night.
4. Next morning you start it again.  Logged out.

The cookies didn't expire — they're good for weeks.  What changed?

In most cases: the user agent, the viewport, and possibly the proxy.
Vanilla Playwright assigns these *per-context*, regenerated on each
launch.  So even though `storage_state` carried your cookies forward,
your "browser" now looks completely different from yesterday.

The platform says "huh, same auth_token but different fingerprint —
probably stolen cookies, force re-auth," and there's your logout.

## What you actually need to persist

For sessions to survive across runs, you need to persist:

- The cookies (obviously).
- The user agent (sticky per account).
- The viewport (sticky per account).
- The proxy / IP geo (sticky per account, ideally same region as origin).
- The init-script patches (every context, every launch).

Notice three of those five are "sticky per account."  Not per-run, not
per-context — *per account*.  Same input every run is what produces the
same fingerprint.

## How Steadfast handles it

```
profiles/
  my_twitter/
    state.json         # cookies + localStorage
    fingerprint.json   # viewport + user_agent + proxy
```

When you call `BrowserManager.get_context("my_twitter")`, Steadfast
reads `fingerprint.json` and uses *exactly* the viewport, UA, and
proxy that were used last time.  If the file doesn't exist (first
launch), Steadfast picks deterministic values from its pools using
`hash("my_twitter") % len(pool)` and saves them — so even the random
initial selection is reproducible.

Then it loads `state.json` as Playwright's `storage_state`, applies
the anti-detection init script, and returns the context.

The platform sees the same browser on the same IP carrying the same
cookies as last time.  Session survives.

## What "anti-detect" actually does

Steadfast's init script patches the obvious automation tells: it sets
`navigator.webdriver` to `undefined`, fakes `window.chrome`, returns a
realistic `navigator.plugins`, etc.  Full list in
[anti-detect.md](../anti-detect.md).

Worth knowing what we *don't* do, because the temptation is real:

- We don't randomize canvas / WebGL / audio fingerprints.  Those
  fingerprints are noisy and patching them often makes you *more*
  unique than letting Chromium's defaults render.
- We don't randomize fonts.  Same reason.
- We don't auto-solve CAPTCHAs.  If a platform served you a CAPTCHA,
  the right response is "your session is compromised, re-export."

## The operational pattern

When a session does break — and they all eventually do — the
right move is:

1. Notify the operator: "session expired for account X."
2. Operator re-exports cookies from their normal browser.
3. Operator runs `await client.import_cookies(...)` once.
4. The bot resumes.

The *wrong* move is to call `login(username, password)` in your code.
That goes through the platform's full UI login flow, which trips
CAPTCHAs and device-verification at much higher rates than cookie
import.  Each failed `login()` attempt also makes your fingerprint
more suspect — you're effectively burning your account.

We see this pattern enough in the wild that Steadfast's
`ensure_logged_in()` defaults to *not* attempting credential login
unless you explicitly pass username/password.  Cookies or nothing.

## What this enables

The reason any of this matters: sessions that survive let you build
the next layer.  Multi-tenant agent platforms.  Scheduled posting
systems.  Long-running observability for customer-facing accounts.
You can't build any of those if your auth re-breaks every four hours.

If you're shipping a browser agent today, audit your stack against
the five-thing list.  Most projects persist exactly one of them — the
cookies — and discover the other four the hard way.

---

*Steadfast is a Python library for browser agents that don't lose their
sessions.  Apache 2.0, available on GitHub.  We're shipping the hosted
multi-tenant version later this summer.*
