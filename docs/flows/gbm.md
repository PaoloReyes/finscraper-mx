# GBM+ — flow

Auth: a `refresh_token`, minted once by `Gbm.login(user, password, code)` (OAuth2 PKCE + TOTP MFA).
Afterwards reads are silent (refresh only) until the token expires, then log in again. Reads one
`Balance` per active strategy across every contract, valued in MXN.

## One-time setup — `Gbm.login(user, password, code)`

```mermaid
sequenceDiagram
  participant C as caller
  participant AU as auth.gbm.com

  C->>AU: POST /session/user  (clientid, user, password)
  AU-->>C: MFA challenge + session
  C->>AU: POST /session/user/challenge  (TOTP code, PKCE codeChallenge)
  AU-->>C: authorizationCode
  C->>AU: POST /session/token  (authorizationCode, PKCE codeVerifier)
  AU-->>C: refresh_token
  Note over C: store the refresh_token - trade-capable, keep it local
```

## Each read — `fetch_balances()`

```mermaid
sequenceDiagram
  participant C as caller
  participant P as GBM provider
  participant AU as auth.gbm.com
  participant AP as api.appgbm.com
  participant HB as homebroker-api.gbm.com

  C->>P: get_provider("gbm")(refresh_token)
  C->>P: fetch_balances()
  P->>AU: POST /session/user/refresh  (refresh_token)
  AU-->>P: access_token  (no MFA)
  P->>AP: GET /v2/onboarding/contracts
  AP-->>P: contract ids
  loop each contract (unless contract_id/accounts given)
    P->>AP: GET /v1/dashboard/contracts/{id}/accounts
    AP-->>P: active strategies (ETFs, Fibras, ...)
  end
  loop each strategy
    P->>HB: POST Operation/GetContractBuyingPower  (legacy_contract_id)
    HB-->>P: marketValueTotal
  end
  P-->>C: list of Balance, one per strategy, in MXN
```
