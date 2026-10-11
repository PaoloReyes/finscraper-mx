# finscraper-mx

Unofficial, **read-only** balance scrapers for Mexican financial institutions: banks, sofipos, brokers and crypto exchanges.

> **Disclaimer.** This project is not affiliated with, endorsed by, or supported by any institution it supports.
> Automated access may be against an institution's terms of use; you are responsible for how you use it.
> Use it only with your own accounts, keep request rates low (e.g. once a day), and never share your credentials or session data.

## Supported institutions

| Id | Institution | Status | Auth |
|---|---|---|---|
| `bitso` | Bitso | ✅ balances in MXN | read-only API key (`api_key`, `api_secret`) |
| `gbm` | GBM+ | ✅ account values in MXN | refresh token (one-time MFA login via `Gbm.login`) |
| `skandia` | Skandia México | ✅ Fondo de Ahorro in MXN | CURP + 6-digit PIN (`username`, `password`); requires `tesseract-ocr` |

## Prerequisites

- Python 3.12 or newer
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) (only for the `skandia` provider, which reads
  Skandia's login captcha with it). Check it with `tesseract --version`.
  - macOS: `brew install tesseract`
  - Debian/Ubuntu: `apt-get install tesseract-ocr`

## Install

```bash
pip install "finscraper-mx @ git+https://github.com/PaoloReyes/finscraper-mx"
```

Install once; all providers are included.

## Use

Every provider works the same way: build it with its auth fields (see the table above) and call
`fetch_balances()`.

```python
from finscraper_mx import get_provider

provider = get_provider("bitso")(api_key="…", api_secret="…")
# provider = get_provider("gbm")(refresh_token="…")   # same shape, different auth fields

for balance in provider.fetch_balances():
    print(balance.account, balance.amount, balance.currency)
```

Or from the command line, with the auth fields in a JSON file:

```bash
finscraper-mx list
finscraper-mx balances bitso --auth auth.json
```

Providers raise `AuthRequired` (missing credentials), `SessionExpired` (the session lapsed — renew the
credential) or `LayoutChanged` (the institution changed something and the provider needs an update).

### Obtaining credentials

Each provider's auth fields are credentials you create once and store yourself (the library is stateless
and never persists them):

- **Bitso** — a read-only API key from Bitso's site.
- **GBM** — a `refresh_token` from a one-time MFA login:
  `get_provider("gbm").login(user, password, code)` returns it. Reuse it until it expires (then log in
  again). GBM optionally takes `contract_id` / `accounts` to pin strategies, and `location=(lat, lon)`.
  It is unofficial and reverse-engineered; automated access may be against GBM's terms, and the refresh
  token is trade-capable — keep it off shared hosts.

## Design

- **Read-only.** Providers only read balances; they never move money or change settings.
- **Stateless.** The library never stores credentials or sessions. The caller passes them in and decides where they live.
- **Prefer sessions over passwords.** Where possible, a provider reuses a session the user created in their own
  browser instead of logging in with a password.
- **Few dependencies.** `httpx` for every provider; Skandia also needs `Pillow`, `pytesseract` and the `tesseract-ocr` binary.

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
