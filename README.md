# finscraper-mx

Unofficial, **read-only** balance scrapers for Mexican financial institutions: banks, sofipos, brokers and crypto exchanges.

> **Disclaimer.** This project is not affiliated with, endorsed by, or supported by any institution it supports.
> Automated access may be against an institution's terms of use; you are responsible for how you use it.
> Use it only with your own accounts, keep request rates low (e.g. once a day), and never share your credentials or session data.

## Supported institutions

| Id | Institution | Status | Auth |
|---|---|---|---|
| `bbva_mx` | BBVA México | 🚧 planned | browser session (cookie + XSRF token) |

## Install

```bash
pip install "finscraper-mx[bbva-mx] @ git+https://github.com/PaoloReyes/finscraper-mx"
```

Each institution is an optional extra, so you only install the dependencies you use (`[all]` installs every one).

## Use

```python
from finscraper_mx import get_provider

provider = get_provider("bbva_mx")(cookie="…", xsrf_token="…")
for balance in provider.fetch_balances():
    print(balance.account, balance.amount, balance.currency)
```

Or from the command line, with the auth fields in a JSON file:

```bash
finscraper-mx list
finscraper-mx balances bbva_mx --auth auth.json
```

Providers raise `AuthRequired` (missing credentials), `SessionExpired` (log in again) or `LayoutChanged`
(the institution changed something and the provider needs an update).

## Design

- **Read-only.** Providers only read balances; they never move money or change settings.
- **Stateless.** The library never stores credentials or sessions. The caller passes them in and decides where they live.
- **Prefer sessions over passwords.** Where possible, a provider reuses a session the user created in their own
  browser instead of logging in with a password.
- **No dependencies in the core.** Each institution's dependencies are an optional extra.

## Adding an institution

1. Create `src/finscraper_mx/institutions/<id>.py` with a `Provider` subclass (`id`, `name`, `auth_fields`, `fetch_balances`).
2. Register it in `institutions/__init__.py` and add its dependencies as an extra in `pyproject.toml`.
3. Add tests that use **sanitized** recorded responses: replace names, account numbers, CLABEs, balances and tokens with fake values.
4. Add a row to the table above.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all,dev]"
pytest
```
