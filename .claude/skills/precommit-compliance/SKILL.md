---
name: precommit-compliance
description: Run before any git commit or push in the finscraper-mx repo. Verifies provider compliance (standard interface only), per-provider tests, README is in sync, no secrets or credential files are tracked, and the whole test suite passes. Use whenever about to commit or push changes here, or when adding/renaming a provider.
---

# finscraper-mx pre-commit compliance

This repo keeps every provider uniform: the only public way to use one is
`get_provider(id)(**auth).fetch_balances()`. Before committing or pushing, confirm nothing has drifted.

## Do this before every commit/push

1. From the repo root, run the checker with the dev venv:
   ```bash
   .venv/bin/python .claude/skills/precommit-compliance/scripts/check_compliance.py
   ```
2. If it ends with `COMPLIANCE OK`, continue with the commit/push.
3. If it ends with `COMPLIANCE FAILED`, fix each listed issue, then re-run until it passes. Do **not**
   commit or push while it fails.
4. Also scan the staged diff for anything sensitive and abort if present:
   ```bash
   git diff --cached | grep -iE "refreshToken|password|api_secret|-----BEGIN" && echo "SECRET STAGED — stop"
   ```

## What the checker enforces

- **Standard interface only.** Every registered provider subclasses `Provider`, its `class.id` matches
  the registry key, it has a `name`, a tuple `auth_fields`, and implements `fetch_balances`. Providers must
  not expose bespoke public call styles — reads go through `fetch_balances()`; one-off credential helpers
  (e.g. a `login` classmethod) are fine but are not the documented "call".
- **Per-provider tests.** Each provider `<id>` has `tests/test_<id>.py`.
- **README in sync.** Every provider id appears in the README's supported-institutions table, the README
  shows the standard `get_provider()` usage, and it contains no `from finscraper_mx.institutions …`
  direct-import examples.
- **No tracked secrets.** No `*_tokens.json`, `*_config.json`, `*_values.json`, `auth*.json`, `.env`, or
  `.har` files are tracked.
- **Green suite.** `pytest` passes.

## When adding or renaming a provider

Keep these in lockstep so the checker stays green:
1. `src/finscraper_mx/institutions/<id>.py` — a `Provider` subclass (standard interface).
2. Register it in `src/finscraper_mx/institutions/__init__.py` (`PROVIDERS`).
3. Add `tests/test_<id>.py` with mocked responses (fake ids/tokens/amounts — never real data).
4. Add a row to the README table and keep usage on the standard pattern.
5. Add any new runtime dependency to `dependencies` in `pyproject.toml`.
