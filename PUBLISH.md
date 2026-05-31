# PUBLISH.md — release runbook

How to ship a new Steadfast release to PyPI.  Written for v0.1.0 first
publish but applies to every subsequent release after the version bump.

> **Dist name vs import name**
>
> Steadfast publishes as **`steadfast-browser`** on PyPI (the bare
> `steadfast` name was claimed by an unrelated 2023 package).  The
> import name stays `steadfast` — users do `pip install steadfast-browser`,
> then `from steadfast import ...`.

## 0. Prerequisites (one-time setup)

1. **PyPI account.** Already set up.
2. **API token.** Generate at <https://pypi.org/manage/account/token/>.
   After the first upload (which had to use an account-wide token
   because the project didn't exist yet), rotate to a project-scoped
   token for `steadfast-browser` and update the file below.
3. **Token storage.**  v0.1.0 was uploaded using a single-file token at
   `/opt/final_trading_with_client/pypiapi.txt` (already in that repo's
   `.gitignore`).  Keep it there or move it to a cleaner location —
   anywhere outside the steadfast repo that you don't commit.

> **If you ever want TestPyPI:** it's a separate site with separate
> accounts and separate tokens.  A pypi.org token returns 403 against
> test.pypi.org.  Generate a test.pypi.org token at
> <https://test.pypi.org/manage/account/token/> if you want a real
> dry-run pipeline.  v0.1.0 skipped TestPyPI because the local
> fresh-venv install was enough to catch packaging issues.

## 1. Pre-release checks

Run from repo root:

```bash
source .venv/bin/activate
pytest -q                                    # 130 unit tests must pass
pytest -m integration tests/integration/     # 3 real-browser tests must pass
ruff check .
mypy --strict steadfast/
```

If any of these fail, do not release.

## 2. Bump version

Update both:

```bash
# pyproject.toml
sed -i 's/^version = ".*"/version = "0.1.1"/' pyproject.toml

# steadfast/__init__.py
sed -i 's/^__version__ = ".*"/__version__ = "0.1.1"/' steadfast/__init__.py
```

Commit:

```bash
git add pyproject.toml steadfast/__init__.py CHANGELOG.md
git commit -m "release: v0.1.1"
git tag v0.1.1
```

## 3. Build the artifacts

```bash
rm -rf dist build *.egg-info
python3 -m build
```

Should produce two files in `dist/`:

```
dist/steadfast_browser-0.1.0-py3-none-any.whl
dist/steadfast_browser-0.1.0.tar.gz
```

## 4. Pre-flight check

```bash
twine check dist/*
```

Both lines should say `PASSED`.

## 5. Pre-flight: verify the wheel installs locally

Cheaper substitute for a TestPyPI round-trip — catches the same
metadata + import-path issues:

```bash
python3 -m venv /tmp/sf_local
source /tmp/sf_local/bin/activate
pip install /opt/steadfast/dist/steadfast_browser-*-py3-none-any.whl
python3 -c "import steadfast; print(steadfast.__version__)"
python3 -c "from steadfast.platforms import Facebook, Instagram, LinkedIn, Reddit, Twitter, YouTube"
deactivate
rm -rf /tmp/sf_local
```

If those pass, the real upload will too.

## 6. Upload to PyPI

Authenticate via env vars so the token never appears in shell history:

```bash
export TWINE_USERNAME=__token__
export TWINE_PASSWORD="$(tr -d '[:space:]' < /opt/final_trading_with_client/pypiapi.txt)"
twine upload --non-interactive dist/*
```

The `tr -d '[:space:]'` is mandatory — a trailing `\n` in the token
file causes a silent `403 Forbidden`.

Successful output ends with:
```
View at: https://pypi.org/project/steadfast-browser/<version>/
```

## 7. Smoke-test the published package

```bash
python3 -m venv /tmp/sf_real
source /tmp/sf_real/bin/activate
pip install steadfast-browser
playwright install chromium       # downloads ~120 MB
python3 -c "from steadfast.platforms import Twitter; print('OK')"
```

## 8. Push the tag

```bash
git push origin main
git push origin v0.1.1
```

## 9. Create the GitHub release

On <https://github.com/getsteadfast/steadfast/releases/new>:
- Tag: `v0.1.1`
- Title: `Steadfast v0.1.1`
- Body: paste the relevant section of CHANGELOG.md
- Upload `dist/*.whl` and `dist/*.tar.gz` as release assets (lets users
  download without going through PyPI)

## Rollback

If you publish a broken release:

```bash
# Yank a release (hides from default pip resolution, doesn't delete):
twine upload --skip-existing dist/*    # no — this doesn't yank
# Yank via the PyPI web UI:
# https://pypi.org/manage/project/steadfast-browser/release/0.1.1/
# Click "Options" → "Yank release"
```

`pip install steadfast-browser` will then fall back to the previous
unyanked version.  Users who already pinned to the broken version still
get it (yank is advisory, not a delete).

## Common failures

| Error from `twine upload` | Cause | Fix |
|---|---|---|
| `File already exists` | Same version was already uploaded | Bump version; you can't re-upload the same `0.1.1` |
| `400 Bad Request, 'description' is too long` | README > 4 MiB | Trim README or use `long_description_content_type` |
| `403 Forbidden, invalid or non-existent authentication` | Token wrong or scoped to a different project | Generate a fresh project-scoped token |
| Twine asks for username + skips token | `~/.pypirc` malformed | Verify the `[pypi]` section has `username = __token__` literally |

## Reproducing from a fresh checkout

```bash
git clone https://github.com/getsteadfast/steadfast.git
cd steadfast
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium      # for integration tests
pytest -q                        # 130 should pass
pytest -m integration tests/integration/   # 3 should pass
```
