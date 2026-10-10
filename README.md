# finscraper-mx

Unofficial, **read-only** balance scrapers for Mexican financial institutions: banks, sofipos, brokers and crypto exchanges.

> **Disclaimer.** This project is not affiliated with, endorsed by, or supported by any institution it supports.
> Automated access may be against an institution's terms of use; you are responsible for how you use it.
> Use it only with your own accounts, keep request rates low (e.g. once a day), and never share your credentials or session data.

## Supported institutions

| Id | Institution | Status | Auth |
|---|---|---|---|
| `bitso` | Bitso | ✅ balances in MXN | read-only API key (`api_key`, `api_secret`) |
| `gbm` | GBM+ | ✅ account values in MXN | refresh token (one-time MFA login via `Gbm.renew`) |

## Install

```bash
pip install "finscraper-mx @ git+https://github.com/PaoloReyes/finscraper-mx"
```

Install once; all providers are included.

## Use

```python
from finscraper_mx import get_provider

provider = get_provider("bitso")(api_key="…", api_secret="…")
for balance in provider.fetch_balances():
    print(balance.account, balance.amount, balance.currency)  # one row per asset, valued in MXN
```

Or from the command line, with the auth fields in a JSON file:

```bash
finscraper-mx list
finscraper-mx balances bitso --auth auth.json
```

Providers raise `AuthRequired` (missing credentials), `SessionExpired` (log in again) or `LayoutChanged`
(the institution changed something and the provider needs an update).

### GBM+ (refresh-token flow)

GBM uses an MFA login once, then a long-lived refresh token for silent reads:

```python
from finscraper_mx.institutions.gbm import Gbm

gbm = Gbm(token_file="gbm_tokens.json", user="you@example.com")  # reads/writes the refresh token

try:
    balances = gbm.fetch_balances()        # silent refresh; discovers contracts + all active strategies
except SessionExpired:                     # refresh token lapsed
    gbm.renew(password, "123456")          # one-time MFA login; stores a new refresh token
    balances = gbm.fetch_balances()

for b in balances:
    ...   # Balance(account="ETFs"/"Fibras"/"Smart Cash", amount=..., currency="MXN")
```

Everything routes through the one `Gbm`:
- `token_file=` → it loads the refresh token and rewrites it (owner-only) on `renew()`; or pass
  `refresh_token=` to keep it stateless and store it yourself.
- Accounts are discovered from the contract(s) by default — pass `contract_id="<uuid>"` or
  `accounts={"<name>": "<legacy_id>"}` to pin them. `Gbm(...).list_accounts()` just enumerates them.

Your ids, refresh token and password live only in local gitignored files — never in the repo.

> GBM is unofficial and reverse-engineered; automated access may be against GBM's terms of use, and the
> refresh token is trade-capable. Keep this repo private and the token files off any shared host.

## Design

- **Read-only.** Providers only read balances; they never move money or change settings.
- **Stateless.** The library never stores credentials or sessions. The caller passes them in and decides where they live.
- **Prefer sessions over passwords.** Where possible, a provider reuses a session the user created in their own
  browser instead of logging in with a password.
- **One dependency.** `httpx` is the only runtime requirement.

## Adding an institution

1. Create `src/finscraper_mx/institutions/<id>.py` with a `Provider` subclass (`id`, `name`, `auth_fields`, `fetch_balances`).
2. Register it in `institutions/__init__.py`; add any new runtime dependency to `dependencies` in `pyproject.toml`.
3. Add tests that use **sanitized** recorded responses: replace names, account numbers, CLABEs, balances and tokens with fake values.
4. Add a row to the table above.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```
