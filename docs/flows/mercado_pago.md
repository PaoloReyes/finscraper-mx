# Mercado Pago — flow

Auth: `cookies`, the `Cookie` request header of the user's own signed-in browser. The login is guarded
by reCAPTCHA and a browser check, so the provider never logs in; it reuses that session until it ends.
Reads one `Balance` per tab of the home page's balance widget (Saldo, Apartados, Criptomonedas), in MXN.

## One-time setup (in the user's browser)

```mermaid
sequenceDiagram
  participant U as user
  participant B as browser
  participant W as www.mercadopago.com.mx

  U->>B: sign in normally (reCAPTCHA, MFA, Google...)
  B->>W: GET /home  (session cookies)
  W-->>B: home page
  U->>B: DevTools, Network, home request, copy the cookie header
  Note over U: store it as cookies in a local auth.json - full account access, keep it private
```

## Each read — `fetch_balances()`

```mermaid
sequenceDiagram
  participant C as caller
  participant P as Mercado Pago provider
  participant W as www.mercadopago.com.mx

  C->>P: get_provider("mercado_pago")(cookies)
  C->>P: fetch_balances()
  P->>W: GET /home  (Cookie, browser User-Agent, no redirects)
  alt redirect to login, or a login page
    W-->>P: 302 to mercadolibre.com login
    P-->>C: SessionExpired
  else signed in
    W-->>P: server-rendered HTML
    Note over P: script __NORDIC_RENDERING_CTX__ holds the page state as JSON
    Note over P: appProps.pageProps.gridData, section type BANKING, content.tabs
    loop each tab - balance, pots, crypto
      Note over P: amount.value fraction and cents, iso MXN
    end
    P-->>C: list of Balance - Saldo, Apartados, Criptomonedas - in MXN
  end
```
