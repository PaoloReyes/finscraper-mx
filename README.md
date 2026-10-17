# finscraper-mx

Unofficial, **read-only** balance scrapers for Mexican financial institutions: banks, sofipos, brokers and crypto exchanges.

> **Disclaimer.** This project is not affiliated with, endorsed by, or supported by any institution it supports.
> Automated access may be against an institution's terms of use; you are responsible for how you use it.
> Use it only with your own accounts, keep request rates low (e.g. once a day), and never share your credentials or session data.

## Supported institutions

| Id | Institution | Status | Auth |
|---|---|---|---|
| `bitso` | Bitso | ✅ balances in MXN | read-only API key (`api_key`, `api_secret`) |
| `didi` | DiDi Cuenta | ✅ Saldo + Mis Ahorros in MXN | captured signed app request (`token`, `signed_query`, `wsgsig` + `secdd` headers; replayed verbatim because the signature is request-bound) |
| `gbm` | GBM+ | ✅ account values in MXN | refresh token (one-time MFA login via `Gbm.login`) |
| `mercado_pago` | Mercado Pago | ✅ Saldo, Apartados, Cripto in MXN | mobile-app session (`token`, `session_id`, `device_id`) |
| `mifel` | Banco Mifel | ✅ account balances in MXN | online-banking `username` + `password` |
| `plata` | Plata | ✅ accounts + investment in MXN (incl. Plata Acciones) | `auth_request` + `installation_id` (capture the app's Auth call from a proxy; replayed to mint a session token) |
| `skandia` | Skandia México | ✅ Fondo de Ahorro in MXN | CURP + 6-digit PIN (`username`, `password`) |

## Prerequisites

- Python 3.12 or newer

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
- **Mercado Pago** — captured from your own signed-in mobile app (its developer API only covers
  payments, so this is reverse-engineered and unofficial). Sign in on the app with a capture running
  (e.g. mitmproxy), find the `GET mobile.mercadopago.com/mp-home-hub/home` request, and copy its
  `authorization` (the `APP_USR-…` after `Bearer `), `x-meli-session-id` and `x-device-id` into
  `auth.json` as `{"token": "…", "session_id": "…", "device_id": "…"}`. If a read is rejected, add
  `x-client-info`, `x-d2id`, `x-device-fsid` and `x-frontend-session-id` as optional `client_info`,
  `d2id`, `device_fsid` and `frontend_session_id`. App login needs device attestation and a
  face-validation challenge, so it can't be automated; the token is per-login — re-capture when it
  lapses. It gives full account access, so keep it private.
- **DiDi** — captured from your own signed-in mobile app (DiDi has no public wallet API, so this is
  reverse-engineered and unofficial). The app signs each request with a native `wsgsig`/`secdd`
  signature computed over that exact request; it can't be rebuilt off-device and only validates the
  request it was made for, so the provider replays the captured request verbatim. Open DiDi Cuenta (its
  SoFiPo wallet) with a capture running (e.g. mitmproxy), find the
  `POST wallet.didipay.com/sofipo/uchome/v0/main` request, and copy into `auth.json`: the `token` from
  its `sofi-header-hint-content` header, the request's exact query string (`signed_query`), and the
  `wsgsig`, `secdd_authentication`, `secdd_challenge`, `oid` and `rid` headers. Override `sofi_hint`,
  `didi_hint` or `body` if your capture differs from the defaults. The signature is request-bound and
  short-lived, so recapture when a read raises `SessionExpired`. It gives wallet access, so keep it
  private.
- **Plata** — `auth_request` + `installation_id`, captured from a single mitmproxy session. Plata's login
  blob is signed on-device and can't be rebuilt, so the provider replays the captured Auth request to mint
  a fresh session token each run. From the capture: base64-encode the request body of the
  `AuthFlowService/Auth` POST (→ `auth_request`) and copy the `x-plata-installation-id` header
  (→ `installation_id`). It's long-lived (observed still valid ~10 h after capture) and is invalidated when
  Plata rotates the device key; recapture it and regenerate the file when a read raises `SessionExpired`.

## Design

- **Read-only.** Providers only read balances; they never move money or change settings.
- **Stateless.** The library never stores credentials or sessions. The caller passes them in and decides where they live.
- **Prefer sessions over passwords.** Where possible, a provider reuses a session the user created in their own
  browser instead of logging in with a password.
- **Few dependencies.** `httpx` for every provider (with the `http2` extra, which Plata's gRPC API needs);
  Mifel's login also needs `curl-cffi` (it impersonates an iOS TLS fingerprint to pass Mifel's WAF).

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
