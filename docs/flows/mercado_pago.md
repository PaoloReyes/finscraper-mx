# Mercado Pago — flow

Auth: the signed-in mobile app's credentials, captured by the user (e.g. with mitmproxy). The required
ones are the `Authorization: Bearer APP_USR-…` `token`, the MeLi `session_id` and the `device_id`;
`client_info`, `d2id`, `device_fsid` and `frontend_session_id` are optional fall-backs. App login needs
device attestation and a face-validation challenge that can't be reproduced off-device, so the provider
never logs in; it replays the captured session. Reads one `Balance` per tab of the home hub's banking
section (Saldo, Apartados, Criptomonedas), in MXN.

The first read of the day can return a stale snapshot, the same one the app paints before it
refreshes. So the day's first `fetch_balances()` re-reads the home hub (1.5 s apart) until two consecutive
reads report the same amounts, at most four reads, returning the latest if they never agree. The Mexico City
date it settled on is cached in `finscraper-mx/mercado_pago.json` under the user cache folder
(`~/Library/Caches` on macOS, `%LOCALAPPDATA%` on Windows, `$XDG_CACHE_HOME` or `~/.cache` elsewhere,
the temp folder with no home; override with `state_path=`), keyed by a hash of the `device_id` (stable
for the install, unlike the session id), so later calls that day read once, even across runs. The file holds nothing secret; if it can't be read or written, every call reconciles.

## One-time setup (the user captures their own app session)

```mermaid
sequenceDiagram
  participant U as user
  participant A as Mercado Pago app
  participant M as mitmproxy
  participant S as mobile.mercadopago.com

  U->>A: sign in normally (device attestation, face_validation)
  A->>S: GET /mp-home-hub/home  (Bearer token + session and device ids)
  S-->>A: home hub JSON
  U->>M: read the request headers from the capture
  Note over U: store token, session_id, device_id in a local auth.json - full account access, keep it private
```

## Each read — `fetch_balances()`

```mermaid
sequenceDiagram
  participant C as caller
  participant P as Mercado Pago provider
  participant S as mobile.mercadopago.com

  C->>P: get_provider("mercado_pago")(token, session_id, device_id)
  C->>P: fetch_balances()
  Note over P: cache file says settled today? then a single read, else loop
  loop until two consecutive reads agree - at most 4 reads, 1.5 s apart
    P->>S: GET /mp-home-hub/home  (Authorization Bearer, x-meli-session-id, x-device-id)
    alt token rejected
      S-->>P: 401 or 403
      P-->>C: SessionExpired
    else signed in
      S-->>P: home hub JSON
      Note over P: sections, type banking_andes, content.tabs
      loop each tab - Saldo, Apartados, Criptomonedas
        Note over P: item type banking_balance_andes, money_amount_items first amount, currency MXN
      end
    end
    Note over P: compare Saldo, Apartados, Criptomonedas with the previous read
  end
  Note over P: write today to the cache file
  P-->>C: list of Balance - Saldo, Apartados, Criptomonedas - in MXN
```
