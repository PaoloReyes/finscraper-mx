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
| [`didi`](#didi) | `token`, `signed_query`, `wsgsig` (+ `secdd_*`, `oid`, `rid`) | capture one app request with a proxy | no expiry |
| [`gbm`](#gbm) | `refresh_token` | run `Gbm.login(...)` once with your MFA code | until GBM expires it |
| [`klar`](#klar) | `private_key_pem`, `device_id`, `username`, `password` | capture once from your paired phone | until you re-pair the phone |
| [`mercado_pago`](#mercado_pago) | `token`, `session_id`, `device_id` | capture one app request with a proxy | until you re-pair the phone |
| [`mifel`](#mifel) | `username`, `password` | your Mifel online-banking login | no expiry |
| [`openbank`](#openbank) | `username`, `password`, `device_uuid` | your login + the app's device id, captured once | until you re-pair the phone |
| [`plata`](#plata) | `auth_request`, `installation_id` | capture the app's Auth call with a proxy | until you re-pair the phone |
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

## Capturing app traffic with mitmproxy

Four providers (DiDi, Mercado Pago, Plata, and Openbank's device id) need values that only the
institution's mobile app ever sends: the app signs or attests its login on the phone, so the library can't
log in by itself. Instead you capture what your own app sends once, and the provider replays it.

The process is the same for every app: **save a dump of the app's traffic, then ask Claude to find the
fields in it.** The per-provider sections below list which request carries which field. They describe the
captures each provider was built from; app versions drift, so letting Claude read the dump (the
`capture-credentials` skill in this repo) is more reliable than copying values by hand.

Use this only with your own accounts. A dump holds live sessions, so treat it like a password.

Klar is different (its app pins its certificates): see [its section](#klar).

### 1. Start mitmproxy on your laptop and save everything

```bash
pip install mitmproxy
mkdir -p captures                                     # gitignored
mitmweb --listen-port 8080 -w captures/<id>.flows     # e.g. captures/plata.flows
```

`mitmweb` shows the traffic live at http://127.0.0.1:8081 and `-w` saves all of it to the dump. (A `.har`
export from mitmweb, File → Export, works too.) Find the laptop's Wi-Fi IP with `ipconfig getifaddr en0`
(macOS) or `hostname -I` (Linux). Phone and laptop must be on the same Wi-Fi; if the router isolates
devices, use a hotspot.

### 2. Point the phone at it

**iPhone**

1. Settings → Wi-Fi → (i) on your network → Configure Proxy → **Manual**: server = the laptop's IP,
   port = `8080`.
2. In Safari open **http://mitm.it**, download the iOS profile, then Settings → General → VPN & Device
   Management → install it.
3. Settings → General → About → **Certificate Trust Settings** → turn on full trust for mitmproxy.

**Android**

1. Wi-Fi → your network → Proxy → **Manual**: host = the laptop's IP, port = `8080`.
2. Open **http://mitm.it**, download the Android certificate and install it (Settings → Security →
   Encryption & credentials → Install a certificate → CA certificate).
3. Android apps ignore user-installed certificates by default, so many banking apps won't talk through the
   proxy unless the app is patched to trust them (that's how Openbank was captured; see
   [`flows/openbank.md`](flows/openbank.md#why-the-patched-capture-was-needed)).

Check it works: open any website on the phone and watch it appear in mitmweb.

### 3. Open the app

Open the institution's app, **sign in, and let the home screen load**. That's when the app sends the
request with its auth. Then turn the phone's proxy **off** and stop mitmweb with Ctrl-C so the dump is
written out.

### 4. Ask Claude to pull out the fields

In Claude Code, in this repo:

> Use the capture-credentials skill to get the `plata` credentials from `captures/plata.flows`.

Claude finds the right request in the dump, writes the fields into a gitignored `auth_<id>.json`, and
never shows the secret values in the chat. It tells you which fields you still have to type in yourself
(your own `username` / `password`, for example). Then check it:

```bash
finscraper-mx balances plata --auth auth_plata.json
```

If the app's request isn't in the dump at all, the app is probably pinning its certificates for that
host, which can't be fixed from the proxy side.

### 5. Clean up

```bash
rm captures/<id>.flows
```

Keep only the `auth_<id>.json` files (gitignored). When a provider later raises `SessionExpired`, repeat
these steps for that app.

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

Klar **pins its certificates** for its own hosts, so a normal capture doesn't work: the app refuses the
proxy. What *isn't* pinned is Firebase Remote Config, and Klar has a hidden developer menu switched on by a
Remote Config flag. So the proxy is used for one thing only: flipping that flag the first time the app
opens.

1. **Start a Klar-only mitmproxy addon** on the laptop. It isn't shipped in the repo; ask Claude
   ("reconstruct the Klar Remote Config addon", the `capture-credentials` skill has the spec). It
   intercepts **only** `firebaseremoteconfig.googleapis.com`, so every pinned Klar host passes through
   the proxy untouched and the app keeps working, and it sets `instrumentation_enabled` and
   `file_logging_enabled` to `true` in the Remote Config response.
2. Point the phone at the proxy and trust its certificate (steps above).
3. **Reinstall Klar, open it and sign in with the proxy on.** Remote Config is only fully fetched on the
   first launch after an install, so the flags only stick this way. Once you're signed in, the proxy can
   be turned off.
4. Open the developer menu with a **three-finger press-and-hold** on the app.
5. **`private_key_pem`:** in the menu, **MFA → Inspect MFA keys**. Copy the whole
   `-----BEGIN PRIVATE KEY----- … -----END PRIVATE KEY-----` block.
6. **`device_id`:** in the menu, **Archivos de registro → Compartir archivos de registro**, and save the
   log files to your laptop. Give them to Claude to find the device id: it's the UUID in the
   `mfa-accept: TRUSTED_DEVICE:<uuid>` lines.
7. `username` and `password` are your Klar login.

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

The body is binary, so don't copy it by hand: save a `.flows` dump and ask Claude to base64 it into
`auth_plata.json` (see [above](#capturing-app-traffic-with-mitmproxy)).

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
