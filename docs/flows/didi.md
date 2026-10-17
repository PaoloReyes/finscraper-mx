# DiDi — flow

Auth: the signed-in mobile app's request, captured by the user (e.g. with mitmproxy). The app signs
every request with a per-request `wsgsig`/`secdd` signature computed natively over the exact query
string and body; it can't be reproduced off-device and only validates the request it was made for, so
the provider never logs in and never rebuilds the request — it replays the captured one verbatim. The
required pieces are the session `token` (from the `sofi-header-hint-content` header), the exact captured
query string (`signed_query`) and the `wsgsig`; pass the `secdd_authentication`, `secdd_challenge`,
`oid` and `rid` the app sent with it, and override `sofi_hint`, `didi_hint` or `body` if the capture
differs from the defaults. Reads one `Balance` per balance card of the SoFiPo home — `sellingPoint`
(Saldo, savings) and `termDeposit` (Mis Ahorros, fixed-term) — in MXN.

## One-time setup (the user captures their own app session)

```mermaid
sequenceDiagram
  participant U as user
  participant A as DiDi app
  participant M as mitmproxy
  participant S as wallet.didipay.com

  U->>A: open DiDi Cuenta (SoFiPo) and sign in normally
  A->>S: POST /sofipo/uchome/v0/main (sofi-header-hint-content token + wsgsig/secdd over this exact query+body)
  S-->>A: SoFiPo home JSON
  U->>M: read the request from the capture
  Note over U: store token, signed_query, wsgsig and secdd/oid/rid in a local auth.json - keep it private
```

## Each read — `fetch_balances()`

```mermaid
sequenceDiagram
  participant C as caller
  participant P as DiDi provider
  participant S as wallet.didipay.com

  C->>P: get_provider("didi")(token, signed_query, wsgsig, ...)
  C->>P: fetch_balances()
  P->>S: POST /sofipo/uchome/v0/main?signed_query (captured headers + wsgsig, replayed verbatim)
  alt rejected or stale signature
    S-->>P: HTTP 401/403, errno != 0, or errno 0 with empty data
    P-->>C: SessionExpired
  else signed in
    S-->>P: home JSON with data.content.cardList
    P->>P: read balance from sellingPoint and amount from termDeposit, clean to Decimal MXN
    P-->>C: list of Balance (Saldo savings, Mis Ahorros fixed-term)
  end
```

Notes:

- The balance arrives as a display string (e.g. `10,058.37`); the provider strips separators and the
  `+` of `totalEarning` and parses `Decimal`.
- `wsgsig`/`secdd` are request-bound and short-lived: a captured signature only validates its own
  request, and a rebuilt request returns `errno 0` with empty `data`. The provider therefore replays the
  captured `signed_query` and signature verbatim; recapture when a read raises `SessionExpired`.
