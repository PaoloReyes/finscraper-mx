# CLAUDE.md — finscraper-mx

Auto-loaded orientation for agents (and humans). Read this first; you shouldn't need to read the whole repo.

## What this is

A small Python library of **read-only balance scrapers** for Mexican financial institutions. Each
institution is a **provider**; all providers are used the **same way** and return the same `Balance` shape.

## The one interface (do not diverge)

```python
from finscraper_mx import get_provider

provider = get_provider("bitso")(api_key="…", api_secret="…")   # build with the institution's auth fields
balances = provider.fetch_balances()                            # -> list[Balance]
```

A provider is a `core.Provider` subclass with `id` (matches the registry key), `name`, `auth_fields`
(tuple), and `fetch_balances() -> list[Balance]`. Reads always go through `fetch_balances()`. A one-off
credential helper (e.g. GBM's `login` classmethod) is allowed but is **not** the documented "call" — never
add provider-specific public read methods or direct-import usage.

## Architecture

```mermaid
flowchart TD
  caller["caller / finscraper-mx CLI"] -->|"get_provider(id)(**auth)"| reg["institutions registry<br/>PROVIDERS"]
  reg --> bitso["Bitso<br/>auth: api_key + api_secret"]
  reg --> gbm["GBM+<br/>auth: refresh_token<br/>one-time Gbm.login mints it"]
  reg --> mp["Mercado Pago<br/>auth: access_token"]
  bitso -->|"httpx"| bapi[("api.bitso.com")]
  gbm -->|"httpx"| gapi[("auth.gbm.com · appgbm · homebroker")]
  mp -->|"httpx"| mapi[("api.mercadopago.com")]
  bitso -->|"fetch_balances()"| bal["list of Balance<br/>valued in MXN"]
  gbm -->|"fetch_balances()"| bal
  mp -->|"fetch_balances()"| bal
```

| id | auth fields | reads | source |
|----|-------------|-------|--------|
| `bitso` | `api_key`, `api_secret` | per-coin, valued in MXN | official REST API |
| `gbm` | `refresh_token` (one-time `Gbm.login`) | per-strategy, in MXN | auth + appgbm + homebroker |
| `mercado_pago` | `access_token` | account money, in MXN | official API |

Detailed per-provider call flows: [`docs/flows/bitso.md`](docs/flows/bitso.md) ·
[`docs/flows/gbm.md`](docs/flows/gbm.md) ·
[`docs/flows/mercado_pago.md`](docs/flows/mercado_pago.md) (one `docs/flows/<id>.md` per provider).

## Rules

Full list in [`.claude/rules/providers.md`](.claude/rules/providers.md). In short:

- **Standard interface only** — `get_provider(id)(**auth).fetch_balances()`; no bespoke public read methods.
- **Stateless** — the library never persists credentials or sessions; the caller passes auth in.
- **No secrets in the repo** — account ids, tokens and passwords live only in local gitignored files
  (`*_config.json`, `*_tokens.json`, `*_values.json`, `auth*.json`, `.env`, `.har`). A fixed public app id
  (e.g. GBM's `CLIENT_ID`) is fine; nothing personal is hardcoded.
- **One runtime dependency:** `httpx`. **Money is `Decimal`**, never float.

## Layout

```
src/finscraper_mx/
  core.py                   Provider, Balance, AccountKind, errors
  institutions/__init__.py  PROVIDERS registry {id: (module, class)} + get_provider()
  institutions/<id>.py      one provider per institution (bitso, gbm, …)
  cli.py                    finscraper-mx list / balances <id> --auth auth.json
tests/test_<id>.py          one test file per provider (mocked httpx, fake data)
docs/flows/<id>.py          detailed mermaid call-flow per provider
.claude/                    rules · skills · commands · agents · hooks · settings.json
```

## .claude/ structure

- **`rules/`** — repo conventions (`providers.md`).
- **`skills/`** — `add-provider` (scaffold a provider, with templates) · `reverse-engineer-provider`
  (map an institution's private web API from a browser capture when there is no official one).
- **`commands/`** — `/add-provider <institution>` (runs the provider-builder agent, which follows the skill).
- **`hooks/precommit_gate.py`** — one file: the hook that blocks commit/push, and `--check` for a manual gate run.
- **`agents/`** — `provider-builder` (subagent that adds/maintains providers end to end).
- **`settings.json`** — permissions for the dev commands + the pre-commit hook.

## Workflows

- **Add a provider** → the `add-provider` skill, or hand it to the `provider-builder` agent.
- **No official API?** → the `reverse-engineer-provider` skill first (capture, find endpoints, pick the
  credential), then `add-provider`.
- **Before commit/push** → the hook runs the gate automatically; run it by hand with:
  ```bash
  .venv/bin/python .claude/hooks/precommit_gate.py --check
  ```
  It checks provider compliance, per-provider tests, README + architecture diagram + flow docs in sync,
  no tracked secrets, and a green suite.
- **Dev setup:** `python -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/pytest`
