# Contributing to Steadfast

Thanks for considering a contribution.  Pre-alpha caveats apply — APIs
may shift before v0.1.0 — but bug reports, PRs, and platform-selector
fixes are very welcome.

## Quick setup

```bash
git clone https://github.com/getsteadfast/steadfast.git
cd steadfast
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,health]"
playwright install chromium
```

## Run the tests

```bash
pytest -q                 # 85 tests, runs in < 1s
ruff check steadfast/ tests/ examples/
mypy --strict steadfast/  # type checking (recommended but not gating)
```

The whole suite runs without launching a real browser.  We deliberately
don't add end-to-end tests against live platforms — they're flaky, they
risk burning your accounts, and they'd require a credentials secret in
CI.  Real-browser validation happens in the [examples](./examples/) which
you can run manually.

## What we welcome

- **Selector updates** when a platform changes its UI.  The
  multi-fallback chains in each platform module are designed for this.
- **New behavioural helpers** on `AntiDetect`.
- **New `PostResult` fields** if they're useful across all platforms.
- **Bug reports** with a minimal reproducer.

## What we'd rather defer

- **New platforms** in core.  We're focused on Twitter, LinkedIn, Reddit
  through v0.1.0.  Other platforms (Facebook, Instagram, TikTok, etc.)
  will land via a plugin interface in v0.2+.
- **API renames or signature changes.**  Even pre-alpha, we want to
  minimize churn for early users.
- **Captcha solvers / token recovery / banned-account rescue.**  Out
  of scope.

## Commit + PR style

- Keep PRs scoped.  Selector fix → one PR.  New helper → another PR.
- Run `ruff check --fix` before pushing.
- One commit per logical change is preferred but we'll squash on merge.
- Reference the issue number in the PR title if one exists.

## Security

If you find a vulnerability — e.g. an anti-detect bypass that exposes
operators, a path traversal in the profiles dir, anything that could
leak credentials — please email security@steadfast.dev rather than
opening a public issue.

## License of contributions

By submitting a PR you agree to license your contribution under
[Apache 2.0 with Commons Clause](./LICENSE), the same as the rest of
the project.
