# finscraper-mx

Unofficial, **read-only** balance scrapers for Mexican financial institutions: banks, sofipos, brokers and
crypto exchanges. Every institution is used the same way and returns the same `Balance` shape, in MXN.

> **Disclaimer.** This project is not affiliated with, endorsed by, or supported by any institution it supports.
> Automated access may be against an institution's terms of use; you are responsible for how you use it.
> Use it only with your own accounts, keep request rates low (e.g. once a day), and never share your credentials or session data.

## Supported institutions

| Id | Institution | Support | Auth fields |
|---|---|---|---|
| `bitso` | Bitso | ✅ | `api_key`, `api_secret` |
| `didi` | DiDi Cuenta | ✅ | `token`, `signed_query`, `wsgsig` |
| `gbm` | GBM+ | ✅ | `refresh_token` |
| `klar` | Klar México | ✅ | `private_key_pem`, `device_id`, `username`, `password` |
| `mercado_pago` | Mercado Pago | ✅ | `token`, `session_id`, `device_id` |
| `mifel` | Banco Mifel | ✅ | `username`, `password` |
| `openbank` | Openbank México | ✅ | `username`, `password`, `device_uuid` |
| `plata` | Plata | ✅ | `auth_request`, `installation_id` |
| `skandia` | Skandia México | ✅ | `username`, `password` |

**How to get each institution's auth fields: [`docs/credentials.md`](docs/credentials.md).** Some are a
plain login; others are captured once from your own phone.

## Install

Requires Python 3.12 or newer. All providers are included.

```bash
pip install "finscraper-mx @ git+https://github.com/PaoloReyes/finscraper-mx"
```

## Use

Build a provider with its auth fields as keyword arguments and call `fetch_balances()`. Every provider
works the same way; only the auth fields change ([how to get them](docs/credentials.md)).

```python
from finscraper_mx import get_provider

provider = get_provider("bitso")(api_key="…", api_secret="…")
# provider = get_provider("gbm")(refresh_token="…")
# provider = get_provider("mifel")(username="…", password="…")

for balance in provider.fetch_balances():
    print(f"{balance.account:<20} {balance.kind:<10} {balance.amount:>12,} {balance.currency}")
```

Each `Balance` has:

| Field | Meaning |
|---|---|
| `institution` | the provider id, e.g. `"bitso"` |
| `account` | the institution's own label, e.g. `"Saldo"` or `"BTC"` |
| `kind` | `checking`, `savings`, `investment`, `crypto` or `other` |
| `amount` | a `Decimal`, valued in MXN |
| `currency` | always `"MXN"` |
| `as_of` | when it was read (UTC) |

Reading several institutions at once, skipping any that fail:

```python
from finscraper_mx import FinscraperError, SessionExpired, get_provider

providers = [
    get_provider("bitso")(api_key="…", api_secret="…"),
    get_provider("klar")(private_key_pem="…", device_id="…", username="…", password="…"),
    get_provider("plata")(auth_request="…", installation_id="…"),
]

for provider in providers:
    try:
        balances = provider.fetch_balances()
    except SessionExpired:
        print(f"{provider.id}: session lapsed, re-capture its credentials (docs/credentials.md)")
        continue
    except FinscraperError as error:
        print(f"{provider.id}: {error}")
        continue
    print(provider.id, sum(b.amount for b in balances))
```

### Command line

The CLI reads the same auth fields from a gitignored JSON file:

```bash
finscraper-mx list                                   # institutions and their auth fields
finscraper-mx balances bitso --auth auth_bitso.json  # balances as JSON
```

```json
[
  {
    "institution": "bitso",
    "account": "BTC",
    "kind": "crypto",
    "amount": "12345.67",
    "currency": "MXN",
    "as_of": "2026-10-24T03:00:00+00:00"
  }
]
```

### Errors

All errors derive from `FinscraperError`:

| Error | Meaning | What to do |
|---|---|---|
| `AuthRequired` | a credential is missing, or the institution rejected it | check the auth file |
| `SessionExpired` | the captured session or token lapsed | renew it ([`docs/credentials.md`](docs/credentials.md)) |
| `LayoutChanged` | the institution changed its API | the provider needs an update; open an issue |

## Design

- **Read-only.** Providers only read balances; they never move money or change settings.
- **Your credentials stay with you.** The library never stores credentials or sessions; you pass them in.
  The only thing written to disk is a small, non-secret cache for Mercado Pago (see its section in the
  credentials guide).
- **Same interface everywhere.** `get_provider(id)(**auth).fetch_balances()` → `list[Balance]`; amounts
  are `Decimal`.
- **Few dependencies.** `httpx` (with HTTP/2, for Plata's gRPC API), `curl-cffi` for Mifel's login, and
  `cryptography` for Klar's device-key challenges.

How each provider talks to its institution: [`docs/flows/`](docs/flows).

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/python .claude/hooks/precommit_gate.py --check   # compliance gate, also run before every commit
```

To add an institution, follow [`.claude/rules/providers.md`](.claude/rules/providers.md) (or run
`/add-provider <institution>` in Claude Code). Tests use mocked HTTP with fake ids, tokens and amounts only.
