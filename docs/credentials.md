# Getting credentials, per institution

Every provider is used the same way, `get_provider(id)(**auth).fetch_balances()`, but what goes into
`auth` differs a lot between institutions. Some take a normal username and password. Others need values
captured once from your own phone, because the institution's app signs requests on the device in a way
that can't be reproduced anywhere else. This page says, for each provider, what to collect, where it comes
from, and how long it lasts.

**Everything here is a secret.** These values give access to your accounts (GBM's token can even place
trades). The library never stores them; you keep them in a local file that git ignores. All examples on
this page use placeholders.

## At a glance

| Id | What you supply | How you get it | How long it lasts |
|---|---|---|---|
| [`bitso`](#bitso) | `api_key`, `api_secret` | create a read-only API key on Bitso | until you revoke it |
| [`didi`](#didi) | `token`, `signed_query`, `wsgsig` (+ `secdd_*`, `oid`, `rid`) | capture one app request with a proxy | short; re-capture often |
| [`gbm`](#gbm) | `refresh_token` | run `Gbm.login(...)` once with your MFA code | until GBM expires it |
| [`klar`](#klar) | `private_key_pem`, `device_id`, `username`, `password` | capture once from your paired phone (scripted) | until you re-pair the phone |
| [`mercado_pago`](#mercado_pago) | `token`, `session_id`, `device_id` | capture one app request with a proxy | until the app login lapses |
| [`mifel`](#mifel) | `username`, `password` | your Mifel online-banking login | no expiry |
| [`openbank`](#openbank) | `username`, `password`, `device_uuid` | your login + the app's device id, captured once | until the device is re-registered |
| [`plata`](#plata) | `auth_request`, `installation_id` | capture the app's Auth call with a proxy | long (hours+); until the device key rotates |
| [`skandia`](#skandia) | `username` (CURP), `password` (PIN) | your Skandia Net login | no expiry |

## Where to keep them

Put each provider's fields in a JSON file named `auth_<id>.json` (for example `auth_klar.json`). Files
matching `auth*.json` and `*_auth.json` are gitignored, and the compliance gate fails if one is ever
tracked.

```bash
finscraper-mx list                                  # every provider and its auth fields
finscraper-mx balances klar --auth auth_klar.json   # prints the balances as JSON
```

The CLI passes every key in the file to the provider, so the same file can also hold a provider's optional
values (such as `proxy` for Klar and Openbank, or `contract_id` for GBM).

From Python, load the file and unpack it:

```python
import json
from finscraper_mx import get_provider

auth = json.load(open("auth_klar.json"))
balances = get_provider("klar")(**auth).fetch_balances()
```

When a credential stops working, the provider raises `SessionExpired`; follow the "Renewing" step for that
institution. `AuthRequired` means a field is missing from the file, or (for password logins) the
institution rejected the username or password.

## Capturing app traffic (DiDi, Mercado Pago, Plata, Klar, Openbank)

Five providers need a value that only the institution's mobile app ever sees. You get it by routing your
phone through [mitmproxy](https://mitmproxy.org) on your laptop once and reading the app's own request:

1. `pip install mitmproxy`, then run `mitmweb` (a browser UI) on a laptop on the same Wi-Fi as the phone.
   If the router isolates clients, use a hotspot.
2. On the phone, set the Wi-Fi proxy to the laptop's IP, port `8080`. Open `http://mitm.it`, install the
   certificate, and trust it fully (on iOS: Settings → General → About → Certificate Trust Settings). On
   Android, apps ignore user-installed certificates by default, which is why Openbank needs a patched APK.
3. Open the institution's app and sign in normally. Find the request named in that institution's section
   below and copy the listed values.
4. Turn the phone's proxy off when you're done.

Capture from your own account only. A saved capture (`.har`, `.flows`) holds your session, so delete it
or keep it out of the repo (`*.har` and `captures/` are gitignored).

---

## bitso

**Fields:** `api_key`, `api_secret`. This is Bitso's official API, so nothing is captured.

1. On bitso.com, open your profile → **API** → add a new API key.
2. Give it **only the permission to view balances**. The provider never trades or withdraws, so the key
   shouldn't be able to either.
3. Copy the key and the secret (the secret is shown once).

```json
{"api_key": "YOUR_API_KEY", "api_secret": "YOUR_API_SECRET"}
```

**Lasts:** until you revoke the key on Bitso.

## didi

**Fields:** `token`, `signed_query`, `wsgsig`; also pass `secdd_authentication`, `secdd_challenge`,
`oid` and `rid` when the captured request has them.

DiDi signs every request on the phone, and each signature is valid only for the exact request it was made
for. So the provider never logs in; it resends one captured request exactly as the app sent it.

1. Capture app traffic (see above), open DiDi Cuenta (the SoFiPo wallet) and let its home screen load.
2. Find `POST wallet.didipay.com/sofipo/uchome/v0/main` and copy:

   | Field | From the captured request |
   |---|---|
   | `token` | the `token` value inside the `sofi-header-hint-content` header (a small JSON) |
   | `signed_query` | the whole query string after `?`, exactly as sent |
   | `wsgsig` | the `wsgsig` header |
   | `secdd_authentication`, `secdd_challenge` | the `secdd-authentication` and `secdd-challenge` headers |
   | `oid` | the `oid` header |
   | `rid` | the `didi-header-rid` header |

```json
{
  "token": "YOUR_TOKEN",
  "signed_query": "PASTE_THE_EXACT_QUERY_STRING",
  "wsgsig": "YOUR_WSGSIG",
  "secdd_authentication": "…",
  "secdd_challenge": "…",
  "oid": "…",
  "rid": "…"
}
```

If your capture's headers or body differ from the provider's defaults, also pass `sofi_hint` (the full
`sofi-header-hint-content` header), `didi_hint` (`didi-header-hint-content`) or `body` (the JSON body).

**Lasts:** the shortest of any provider. When a read raises `SessionExpired`, capture a fresh request.

## gbm

**Field:** `refresh_token`, minted once by a login helper.

GBM's login needs a 6-digit code from your authenticator app, so it runs once by hand:

```python
from finscraper_mx import get_provider

refresh_token = get_provider("gbm").login("you@example.com", "YOUR_PASSWORD", "123456")
print(refresh_token)  # save this; don't keep the password
```

```json
{"refresh_token": "YOUR_REFRESH_TOKEN"}
```

**Optional:** `contract_id` (in the file or as a keyword) pins one contract instead of discovering all of
them. In Python, `location=(lat, lon)` sets the device location GBM's API expects (default: Mexico City).

**Lasts:** each read refreshes the session silently. When GBM expires the refresh token, reads raise
`SessionExpired`; run `login` again.

**Careful:** the refresh token is trade-capable. Keep it off shared machines.

## klar

**Fields:** `private_key_pem`, `device_id`, `username`, `password`. **Optional:** `proxy`.

Klar answers every request with an RSA challenge that only a paired ("trusted") device can solve, and
pairing a new device needs a face scan and SMS. So the provider reuses your phone's existing device key.

1. On a laptop: `pip install mitmproxy`, then `python scripts/klar_capture.py`. It prints the laptop's IP
   and the phone steps, then starts the proxy.
2. Point the phone at the proxy and trust its certificate (see above).
3. **Reinstall Klar** and sign in. Reinstalling forces Klar to re-fetch its remote config, which the
   script uses to switch on Klar's hidden developer menu.
4. In the app, press and hold with several fingers to open the developer menu, then go to **MFA → Inspect
   MFA keys**. Copy the whole `-----BEGIN PRIVATE KEY----- … -----END PRIVATE KEY-----` block →
   `private_key_pem`.
5. Watch the script's terminal for `>>> deviceId = <uuid>` → `device_id`.
6. `username` and `password` are your Klar login.

```json
{
  "private_key_pem": "-----BEGIN PRIVATE KEY-----\nPLACEHOLDER\n-----END PRIVATE KEY-----\n",
  "device_id": "00000000-0000-0000-0000-000000000000",
  "username": "you@example.com",
  "password": "YOUR_PASSWORD"
}
```

The key can be pasted on one line with `\n` escapes (as above) or with real line breaks.

**Lasts:** until you re-pair the phone (that rotates the key). Reinstalling the app keeps the key.

**Gotchas:**
- Klar allows **one session per account**. If the phone app is open and signed in, the read raises
  `SessionExpired`; close the app (or wait about 30 minutes) and retry. The provider logs out after every
  read so it doesn't lock the phone out in turn.
- Klar blocks datacenter IPs. On a cloud server, set `proxy` to an HTTP proxy on a home connection (for
  example `"proxy": "http://100.64.0.2:8899"` over Tailscale).

Details and the full sequence: [`docs/flows/klar.md`](flows/klar.md).

## mercado_pago

**Fields:** `token`, `session_id`, `device_id`. **Optional:** `client_info`, `d2id`, `device_fsid`,
`frontend_session_id`.

Mercado Pago's login needs device attestation and a face check, so the provider can't log in; it reuses
the session from your signed-in app.

1. Capture app traffic (see above) and open the Mercado Pago app's home screen.
2. Find `GET mobile.mercadopago.com/mp-home-hub/home` and copy:

   | Field | From the captured request |
   |---|---|
   | `token` | the `authorization` header, without `Bearer ` (it starts with `APP_USR-`) |
   | `session_id` | the `x-meli-session-id` header |
   | `device_id` | the `x-device-id` header |
   | optional | `x-client-info` → `client_info`, `x-d2id` → `d2id`, `x-device-fsid` → `device_fsid`, `x-frontend-session-id` → `frontend_session_id` |

```json
{"token": "APP_USR-PLACEHOLDER", "session_id": "YOUR_SESSION_ID", "device_id": "YOUR_DEVICE_ID"}
```

Add the optional values only if reads get rejected without them.

**Lasts:** the token belongs to one app login. When reads raise `SessionExpired`, capture a fresh one.

**Note:** the first read of each day re-reads a few times to skip a stale snapshot, and remembers that day
in a small cache file (`finscraper-mx/mercado_pago.json` in your user cache folder). It holds only a date
and a hash, nothing secret; delete it any time.

## mifel

**Fields:** `username`, `password` — your Banco Mifel online-banking login. Nothing to capture.

```json
{"username": "YOUR_USERNAME", "password": "YOUR_PASSWORD"}
```

The provider logs in on every read, the same way the Mifel Móvil app does, so there's no push approval.
In Python, `location=(lat, lon)` sets the reported device location (default: Mexico City).

**Lasts:** as long as your password.

## openbank

**Fields:** `username` (the email you sign in with), `password`, `device_uuid`. **Optional:** `proxy`.

Openbank ties logins to the device registered at sign-up, so the provider sends that device's id.

1. On an **Android** phone with the Openbank MX app (`mx.openbank.modelbank`) signed in, capture the app's
   login, `POST api.openbank.mx/mb-api-auth/authentication/login`. The stock app pins its certificates, so
   the capture needs the patched APK described in [`docs/flows/openbank.md`](flows/openbank.md#why-the-patched-capture-was-needed)
   (Android's user-installed certificate also has to be trusted by that build).
2. Copy `deviceInformation.deviceUUID` from the request body → `device_uuid` (it's the same value as
   `uuid` in the `platforminformation` header).

```json
{"username": "you@example.com", "password": "YOUR_PASSWORD", "device_uuid": "00000000-0000-0000-0000-000000000000"}
```

**Lasts:** until the app registers a new device (new phone or re-enrolment); then capture the new id.

**Gotcha:** Openbank blocks datacenter IPs. On a cloud server, set `proxy` (for example
`"proxy": "http://warp:1080"`). In Python, `document_type=` changes the login type from email.

## plata

**Fields:** `auth_request`, `installation_id`.

Plata's app logs in with a blob signed by the phone's secure hardware, which can't be recreated. The
provider resends that captured login request to get a fresh session on every read.

1. Capture app traffic (see above) and open the Plata app until the home screen loads.
2. Find `POST prime.platacard.mx/client.diftech.platform.auth.v2.AuthFlowService/Auth`.
3. `auth_request` = the request's **raw body, base64-encoded**. `installation_id` = its
   `x-plata-installation-id` header.

The body is binary, so copying it by hand is fiddly. Saving the capture with `mitmdump -w plata.flows` and
running this prints the finished file:

```python
import base64, json
from mitmproxy import io

with open("plata.flows", "rb") as f:
    for flow in io.FlowReader(f).stream():
        if flow.request.path.endswith("/AuthFlowService/Auth"):
            print(json.dumps({
                "auth_request": base64.b64encode(flow.request.content).decode(),
                "installation_id": flow.request.headers["x-plata-installation-id"],
            }))
            break
```

```json
{"auth_request": "BASE64_OF_THE_AUTH_REQUEST_BODY", "installation_id": "00000000-0000-0000-0000-000000000000"}
```

**Lasts:** long. A capture kept working for more than 10 hours, and the exact limit isn't known. It stops
when Plata rotates the device key (logging out, reinstalling or switching phones); then reads raise
`SessionExpired` and you capture again.

**Note:** Plata Acciones is held in USD and converted to MXN at Google Finance's live rate. In Python,
`usd_mxn_rate=` fixes the rate instead.

## skandia

**Fields:** `username` = your **CURP** (18 characters), `password` = your **6-digit PIN**. These are the
Skandia Net login. Nothing to capture.

```json
{"username": "XEXX010101HNEXXXA4", "password": "000000"}
```

Only the corporate savings plan (Fondo de Ahorro) is supported.

**Lasts:** as long as your PIN.

---

## Not supported yet

- **Edenred** — in progress. The app pins its certificates, so its traffic can't be captured with a
  normal proxy yet.
