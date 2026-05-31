# Getting started

This guide walks you from `pip install` to a posted tweet in about ten
minutes, then covers the most common failure modes.

## 1. Install

Steadfast needs Python 3.10+ and Playwright's Chromium build:

```bash
pip install steadfast-browser    # dist name on PyPI (imports as `steadfast`)
playwright install chromium
```

Optional extras:

```bash
pip install "steadfast-browser[health]"   # adds aiohttp for proxy health checks
pip install "steadfast-browser[dev]"      # adds pytest, ruff, mypy for contributors
```

> The PyPI distribution name is `steadfast-browser` because the bare
> `steadfast` name was already taken by an unrelated 2023 package.  The
> import name remains `steadfast` — `from steadfast import ...` works the
> same.

## 2. Decide how you'll authenticate

Three options, in order of how-reliable-they-are-in-production:

1. **Cookie import (recommended).** Log in once in your normal browser,
   export cookies with the [Cookie-Editor](https://cookie-editor.com/)
   extension, import them into Steadfast.  Sessions last weeks to months.
2. **Manual login via VNC.** Run a headed browser on a headless server,
   connect via VNC, log in by hand, Steadfast captures the resulting
   cookies.  Requires `RemoteDisplay` + `xvfb` + `x11vnc` installed.
3. **Credential login.** `await twitter.login(username, password)`.
   Works but Twitter / LinkedIn / Reddit all trip CAPTCHA or device
   verification frequently when you do this from automation.

For the rest of this guide we use option 1.

## 3. Get your cookies

Open the platform in your normal browser, sign in, install
[Cookie-Editor](https://cookie-editor.com/), click the extension icon,
choose **Export → Export as JSON**.  Save as `cookies.json` somewhere
your script can find.

**Sanity check** — your file should contain the platform's session cookie:

| Platform | Critical cookie names |
|---|---|
| Twitter / X | `auth_token`, `ct0`, `twid`, `kdt` |
| LinkedIn | `li_at` |
| Reddit | `reddit_session` AND `token_v2` (you need both) |

If the critical cookie is missing, you weren't actually signed in when
you exported.  Try again in a normal (non-incognito) window.

## 4. Your first script

```python
import asyncio
import json
from pathlib import Path
from steadfast import AntiDetect, BrowserManager, BrowserManagerConfig
from steadfast.platforms import Twitter

async def main():
    cookies = json.loads(Path("twitter_cookies.json").read_text())

    bm = BrowserManager(
        BrowserManagerConfig(profiles_dir=Path("./profiles")),
        AntiDetect(),
    )
    await bm.start()
    try:
        twitter = Twitter(bm, account_key="my_twitter")
        await twitter.import_cookies(cookies)
        assert await twitter.ensure_logged_in(), "cookies didn't work"
        result = await twitter.post("Hello from Steadfast!")
        print(result)
    finally:
        await bm.shutdown()

asyncio.run(main())
```

You only have to call `import_cookies` once per machine.  After that the
cookies live in `./profiles/my_twitter/state.json` and Steadfast picks
them up on every subsequent run.

## 5. What you just got

- `./profiles/my_twitter/state.json` — your session cookies + localStorage.
  Treat this file as a secret.  Add `profiles/` to your `.gitignore`.
- `./profiles/my_twitter/fingerprint.json` — your sticky viewport, user
  agent, and proxy.  Same fingerprint on every run = same look to the
  platform = no session invalidation.

If you remove either file, Steadfast generates a fresh one on next launch
— but Twitter will see this as a "new device" and may force you to re-auth.

## 6. Common failure modes

### `cookies didn't work` immediately after import

Means `import_cookies` succeeded (you saved valid JSON) but the platform
didn't recognize the session when you tried `ensure_logged_in`.  Causes,
in order of likelihood:

1. **Critical cookie missing.**  See the table in step 3.
2. **You exported from incognito mode.**  The session cookie never made
   it into your browser's cookie jar.  Re-export from a normal window.
3. **The cookies expired between export and import.**  Re-export.

### `Reddit has blocked this IP (network security)`

Reddit blocks many cloud-server IPs by default.  Use a proxy:

```python
from steadfast import AntiDetect, ProxyInfo

ad = AntiDetect(proxies=[
    ProxyInfo("http", "proxy.example.com", 8080, "user", "pass"),
])
```

### LinkedIn keeps redirecting to /login

Either `li_at` is missing from your cookies (most common), or LinkedIn's
device-verification flow flagged your fingerprint.  Re-export from the
SAME browser you originally signed in from, on the SAME machine.

### Twitter post succeeds but `platform_post_id` starts with `unverified-`

The post landed — that's confirmed.  Twitter didn't return a tweet URL
in the toast, and the profile-fallback couldn't find the tweet (maybe
shadowbanned, maybe a CDN lag).  Verify manually on your profile page.
The synthetic id prevents your queue from reposting it as a duplicate.

## 7. Next steps

- [Auth & sessions](./auth-and-sessions.md) — the *why* behind the design
- [Anti-detection](./anti-detect.md) — what's in the init script
- [Examples](../examples/) — five runnable scripts to copy from
