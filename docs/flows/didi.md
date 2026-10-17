# DiDi — flow

Auth: the signed-in DiDi Dinero app's session, captured by the user (e.g. with mitmproxy): the SOFIPO
session `token` (inside the `sofi-header-hint-content` header), the `wsgsig` integrity header and the
`device_id`. The provider never logs in; it replays the captured session against the app's home-screen
endpoint and reads the DiDi Cuenta balance, in MXN.

## One-time setup (the user captures their own app session)

```mermaid
sequenceDiagram
  participant U as user
  participant A as DiDi Dinero app
  participant M as mitmproxy
  participant S as wallet.didipay.com

  U->>A: sign in (phone + password)
  A->>S: signByPassword
  S-->>A: ticket (the session token)
  A->>S: POST /sofipo/uchome/v0/main  (token in sofi-header-hint-content, wsgsig, deviceId)
  S-->>A: home screen JSON
  U->>M: read the token, wsgsig and deviceId from the capture
  Note over U: store them in a local auth_didi.json - account access, keep it private
```

## Each read — `fetch_balances()`

```mermaid
sequenceDiagram
  participant C as caller
  participant P as DiDi provider
  participant S as wallet.didipay.com

  C->>P: get_provider("didi")(token, wsgsig, device_id)
  C->>P: fetch_balances()
  P->>S: POST /sofipo/uchome/v0/main  (app query + body, sofi-header-hint-content, wsgsig)
  alt token rejected
    S-->>P: 401 or 403, or errno not 0
    P-->>C: SessionExpired
  else signed in
    S-->>P: home screen JSON
    Note over P: data.content.cardList, cardType sellingPoint, balance - else logData.deposit_amount
    P-->>C: list of Balance - DiDi Cuenta - in MXN
  end
```
