# Auth & sessions

This is the wedge.  Everything else in Steadfast is in service of one
property: **a session you established once keeps working across runs.**

## The problem

Vanilla Playwright gives you a `BrowserContext`.  When you close it, the
cookies go with it.  Most projects work around that by:

1. Logging in fresh on every run (slow, trips bot detection).
2. Saving `storage_state` to disk and reloading it manually.

Option 2 is what every "browser agent" library does — including the
funded ones.  It mostly works.  But it doesn't account for the *other*
things that bind a session to a browser fingerprint:

- **User agent.**  Sites cache "this auth_token came from a Chrome 121
  on macOS" and reject it if the next request claims to be Firefox 122
  on Linux.
- **Viewport.**  Less critical but still tracked.  Some sites bind
  CSRF tokens to viewport-dependent layout fingerprints.
- **Proxy / IP.**  Twitter and Reddit re-verify the IP geo of a
  session; sudden moves between regions trigger re-auth challenges.
- **WebGL / canvas / audio fingerprints.**  Reset on every fresh
  context, even if you've reloaded the cookies.

When *any* of these change between runs, the platform thinks "different
browser" and forces you to log in again.  That's the 4-hour-logout your
users complain about.

## What Steadfast does

For every `account_key` you use, Steadfast persists *all* of these as a
single sticky bundle, in two files:

```
profiles/<account_key>/state.json        # cookies + localStorage
profiles/<account_key>/fingerprint.json  # viewport + UA + proxy
```

On every subsequent run with the same `account_key`, Steadfast:

1. Loads `state.json` as Playwright's `storage_state`.
2. Loads `fingerprint.json` and uses the exact same viewport, UA, and
   proxy as last time.
3. Re-applies the anti-detection init script (see
   [anti-detect.md](./anti-detect.md)).

The platform sees the same auth cookies coming from the same browser
fingerprint over the same IP — so the session keeps working.

## Cookie import vs credential login

If you have the choice, **always use cookie import**.

Credential login (calling `await twitter.login(username, password)`)
makes the browser go through the platform's full login UI: fill the
username field, click Next, fill the password field, solve the CAPTCHA
if there is one, deal with the device-verification email if there is one.
The probability that you'll hit one of those checks is very high from a
fresh browser fingerprint.

Cookie import skips the whole flow.  You established the session once,
in a normal browser, where you (a human) handled any verification
challenges that came up.  All Steadfast does is copy the resulting
cookies into its profile.

## The cookies that matter

Each platform's session lives in one or two specific cookies.  If those
are present and valid, you're logged in.  If they're missing, you're
not — no matter how many other cookies are in your export.

| Platform | Critical cookie | What carries it |
|---|---|---|
| Twitter / X | `auth_token` (httpOnly) | the session itself |
| Twitter / X | `ct0` | CSRF token; required for write ops |
| Twitter / X | `twid` | user id |
| LinkedIn | `li_at` | the session itself |
| Reddit | `reddit_session` | old-reddit session |
| Reddit | `token_v2` | new-reddit bff API auth |

The Reddit pairing matters: `reddit_session` alone gets you onto
old.reddit.com but new.reddit.com's API will reject you.  `token_v2`
alone usually works but is shorter-lived.  You want both.

## When a session does break

Cookies expire eventually.  When they do, Steadfast's
`get_session_health()` returns `False`, and `ensure_logged_in()` returns
`False` if you didn't pass credentials.  Your code should treat that as
a clear "re-import cookies" signal:

```python
if not await twitter.ensure_logged_in():
    # Don't call login() — that'll fail too in 9/10 cases.
    # Tell the operator to re-export cookies.
    notify_operator("Twitter session expired; re-export cookies needed")
    return
```

This is the operational pattern.  Don't try to auto-recover broken
sessions with `login()` — it just trips CAPTCHAs and burns your
fingerprint reputation.  Surface the failure, get fresh cookies, move on.

## When you really need to log in via UI

Some scenarios force it: you don't have a normal browser on the same
machine, or you've been forced to rotate credentials, or you're
bootstrapping a brand-new account.

For those, Steadfast supports the **manual-login via VNC** flow:

```python
from steadfast import BrowserManager, BrowserManagerConfig, RemoteDisplay
# ... see the manual-login example in docs (coming) for the full flow
```

The library boots a headed browser on the server inside Xvfb, exposes a
VNC port, you point a VNC client at it from your laptop, log in by hand,
and Steadfast captures the resulting cookies into the profile.  The
result is the same as cookie import, except you didn't have to copy/paste
a JSON blob.

(Requires `Xvfb` and `x11vnc` installed on the host.)
