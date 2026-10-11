# Mercado Pago — flow

Auth: an access token (`access_token`, `APP_USR-…`) from an application created once in Mercado Pago
Developers. Reads one `Balance`: the account money (dinero en cuenta), in MXN. Cajitas are not included.

```mermaid
sequenceDiagram
  participant C as caller
  participant P as Mercado Pago provider
  participant A as api.mercadopago.com

  C->>P: get_provider("mercado_pago")(access_token)
  C->>P: fetch_balances()
  P->>A: GET /users/me  (Authorization: Bearer token)
  A-->>P: user id
  P->>A: GET /users/{id}/mercadopago_account/balance
  A-->>P: total_amount, available_balance, unavailable_balance, currency_id
  Note over P: 401 or 403 raises SessionExpired, non-MXN account fails
  P-->>C: list with one Balance, "Dinero en cuenta", in MXN
```
