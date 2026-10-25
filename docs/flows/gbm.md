# GBM — flow

Getting the credentials: [`docs/credentials.md`](../credentials.md#gbm).

Reads one `Balance` per active strategy across every contract, valued in MXN.

## Auth

Fully headless: `username` + `password` + `totp_secret` + `device_id`. The provider generates GBM's
6-digit MFA code itself from `totp_secret` (the account's authenticator seed, RFC 6238: base32 secret,
HMAC-SHA1, 30 s) and runs GBM's mobile PKCE login on every read. No refresh token to babysit and no
human step. GBM allows **one authorized device and one session at a time**, so a read logs the phone
app's session out (the phone stays an authorized device; just sign in again).

```mermaid
sequenceDiagram
  participant C as caller
  participant P as GBM provider
  participant AU as auth.gbm.com

  C->>P: fetch_balances()
  P->>AU: POST /session/user (clientId, user, password, PKCE codeChallenge, lat/long, device-mac-address)
  alt GBM issues an MFA challenge
    AU-->>P: MFA challenge + session + server timestamp
    Note over P: code = TOTP(totp_secret, server time)  (RFC 6238, avoids clock skew)
    P->>AU: POST /session/user/challenge (code, PKCE codeChallenge + codeVerifier, session)
    AU-->>P: authorizationCode
  else GBM returns the code directly (no MFA this login)
    AU-->>P: authorizationCode
  end
  P->>AU: POST /session/token (authorizationCode, PKCE codeVerifier)
  AU-->>P: access_token
```

An unauthorized `device_id` (the phone took the single-device slot) makes `/session/user` fail and the
provider raises `SessionExpired`; a rejected code raises `AuthRequired`.

## Read — `fetch_balances()`

```mermaid
sequenceDiagram
  participant C as caller
  participant P as GBM provider
  participant AU as auth.gbm.com
  participant AP as api.appgbm.com
  participant HB as homebroker-api.gbm.com

  C->>P: get_provider("gbm")(username, password, totp_secret, device_id)
  C->>P: fetch_balances()
  P->>AU: headless login (above)
  AU-->>P: access_token
  P->>AP: GET /v2/onboarding/contracts
  AP-->>P: contract ids
  loop each contract (unless contract_id/accounts given)
    P->>AP: GET /v1/dashboard/contracts/{id}/accounts
    AP-->>P: active strategies (ETFs, Fibras, ...)
  end
  loop each strategy
    P->>HB: POST Operation/GetContractBuyingPower (legacy_contract_id)
    HB-->>P: marketValueTotal
  end
  P-->>C: list of Balance, one per strategy, in MXN
```

## Setup — take over the TOTP seed once

One-time, Claude-assisted (the `capture-credentials` skill), not through the provider. GBM's active seed
can't be read (`GET /security-settings/totp/key` mints fresh enrollment secrets), so setup **activates a
seed the scraper controls**: log in → `GET /security-settings/totp/key` (fresh seed) → generate its code
against server time → `POST /security-settings/totp/confirm` to activate → store as `totp_secret`. This
**replaces the GBM app's own authenticator** (re-enrolling it in the app rotates the seed and breaks the
scraper). `device_id` is the `device-mac-address` from a one-time app capture. See
[`docs/credentials.md#gbm`](../credentials.md#gbm).

## Balance mapping

One `Balance` per active strategy, `kind` `investment`, amount = the strategy's `marketValueTotal` in MXN.
Contracts and their strategies are discovered from the dashboard unless the caller pins them with
`contract_id` / `accounts`. An unexpected `marketValueTotal` raises `LayoutChanged`.
