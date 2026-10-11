---
name: add-provider
description: Add a new institution provider to finscraper-mx. Use when adding support for a new Mexican bank, broker, sofipo, wallet or exchange — scaffolds the provider following the standard interface, registers it, adds its test and README row, and keeps the repo compliant. Also use to pick up a half-finished provider in a new chat.
---

# Add a provider to finscraper-mx

Read `CLAUDE.md` first. Every provider is used the same way — `get_provider(id)(**auth).fetch_balances()`
— and returns `list[Balance]`. Keep it that way; do not add provider-specific public read methods.

## Steps

1. **Pick the id and auth.** Choose a short snake_case `id` (e.g. `banorte`, `mercado_pago`). Decide its
   `auth_fields` — the credentials the caller supplies (an API key, a refresh token, a session cookie, …).
   Credentials are created once by the user and stored by them; the library stays stateless.
   If the institution has no official API for balances, map its private one first with the
   `reverse-engineer-provider` skill.

2. **Create `src/finscraper_mx/institutions/<id>.py`** — a `Provider` subclass (template below). Only
   `fetch_balances()` is the public read path. Value everything in MXN; amounts are `Decimal`.

3. **Register it** in `src/finscraper_mx/institutions/__init__.py`:
   ```python
   PROVIDERS = {
       ...,
       "<id>": ("finscraper_mx.institutions.<id>", "<ClassName>"),
   }
   ```

4. **Add `tests/test_<id>.py`** — mock the HTTP with `httpx.MockTransport`; use **fake** ids, tokens and
   amounts (never real data). Cover: reads values correctly, missing auth → `AuthRequired`, and the
   institution's quirks (expired session → `SessionExpired`, pagination, currency conversion, …).

5. **Add a README row** to the supported-institutions table, and add the provider to the **`CLAUDE.md`
   architecture diagram** (and its providers table). Keep usage on the standard pattern.

6. **Add `docs/flows/<id>.md`** — a detailed mermaid flow of the provider's auth + read calls (see the
   existing `docs/flows/bitso.md` / `docs/flows/gbm.md`).

7. **Dependencies.** If it needs a new runtime package, add it to `dependencies` in `pyproject.toml`
   (`httpx` is already there).

8. **Run the gate** and fix anything red before committing:
   ```bash
   .venv/bin/python .claude/hooks/precommit_gate.py --check
   ```

## Provider template

```python
"""<Institution>, through its <official API / web session / …>. Read-only."""
from __future__ import annotations

from decimal import Decimal

import httpx

from ..core import AccountKind, AuthRequired, Balance, Provider, SessionExpired  # noqa: F401

BASE_URL = "https://api.example.com"


class <ClassName>(Provider):
    id = "<id>"
    name = "<Display Name>"
    auth_fields = ("<field1>", "<field2>")  # credentials the caller passes in

    def __init__(self, *, client: httpx.Client | None = None, **auth) -> None:
        super().__init__(**auth)  # enforces auth_fields are present
        self._client = client or httpx.Client(base_url=BASE_URL, timeout=20)

    def fetch_balances(self) -> list[Balance]:
        # use self.auth["<field1>"] … ; call the institution; map to Balance objects
        ...
        return [
            Balance(self.id, account_name, AccountKind.CHECKING, Decimal("0.00"), "MXN"),
        ]
```

## Test template

```python
import httpx
import pytest

from finscraper_mx import AuthRequired, get_provider


def fake_api():
    def handler(request: httpx.Request) -> httpx.Response:
        # return canned JSON per request.url.path (fake ids/tokens/amounts)
        return httpx.Response(200, json={...})
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.example.com")


def test_requires_auth():
    with pytest.raises(AuthRequired):
        get_provider("<id>")()


def test_reads_balances():
    provider = get_provider("<id>")(field1="x", field2="y", client=fake_api())
    balances = provider.fetch_balances()
    assert balances[0].currency == "MXN"
```

## Picking up an unfinished provider in a new chat

1. `.venv/bin/python .claude/hooks/precommit_gate.py --check` — the failures tell you what's missing (unregistered,
   no test, not in README, interface drift, failing suite).
2. Fix each reported item using the steps above until it reads `COMPLIANCE OK`.
