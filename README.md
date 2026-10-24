# finscraper-mx

Unofficial, **read-only** balance scrapers for Mexican financial institutions: banks, sofipos, brokers and
crypto exchanges. Every institution is used the same way and returns the same `Balance` shape, in MXN.

> **Disclaimer.** This project is not affiliated with, endorsed by, or supported by any institution it supports.
> Automated access may be against an institution's terms of use; you are responsible for how you use it.
> Use it only with your own accounts, keep request rates low (e.g. once a day), and never share your credentials or session data.

## Supported institutions

| Institution | Support | Auth fields |
|---|---|---|
| Bitso (`bitso`) | ✅ | `api_key`, `api_secret` |
| DiDi Cuenta (`didi`) | ✅ | `token`, `signed_query`, `wsgsig` |
| GBM+ (`gbm`) | ✅ | `refresh_token` |
| Klar México (`klar`) | ✅ | `private_key_pem`, `device_id`, `username`, `password` |
| Mercado Pago (`mercado_pago`) | ✅ | `token`, `session_id`, `device_id` |
| Banco Mifel (`mifel`) | ✅ | `username`, `password` |
| Openbank México (`openbank`) | ✅ | `username`, `password`, `device_uuid` |
| Plata (`plata`) | ✅ | `auth_request`, `installation_id` |
| Skandia México (`skandia`) | ✅ | `username`, `password` |

Credit lines and credit cards are not reported. **Edenred** is in progress and not supported yet.

**How to get each institution's auth fields: [`docs/credentials.md`](docs/credentials.md).** Some are a
plain login; others are captured once from your own phone.

## Install

Requires Python 3.12 or newer. All providers are included.

```bash
pip install "finscraper-mx @ git+https://github.com/PaoloReyes/finscraper-mx"
```

## Use

Build a provider with its auth fields and call `fetch_balances()`:

```python
from finscraper_mx import get_provider

provider = get_provider("bitso")(api_key="…", api_secret="…")
# provider = get_provider("gbm")(refresh_token="…")   # same shape, different auth fields

for balance in provider.fetch_balances():
    print(balance.account, balance.kind, balance.amount, balance.currency)
```

Or from the command line, with the auth fields in a gitignored JSON file:

```bash
finscraper-mx list                                   # institutions and their auth fields
finscraper-mx balances bitso --auth auth_bitso.json  # balances as JSON
```

Errors all derive from `FinscraperError`:

- `AuthRequired`: a credential is missing, or the institution rejected it.
- `SessionExpired`: the captured session or token lapsed; renew it as described in
  [`docs/credentials.md`](docs/credentials.md).
- `LayoutChanged`: the institution changed its API and the provider needs an update.

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
