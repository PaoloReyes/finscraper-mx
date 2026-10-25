---
name: capture-credentials
description: Turn the user's own mitmproxy dump (.flows) or .har capture into a finscraper-mx auth_<id>.json (DiDi, Mercado Pago, Plata, Klar, Openbank, GBM) without exposing secrets. Use when the user hands over a capture, sets up or renews credentials, or a provider raises SessionExpired. Also has the spec for rebuilding Klar's Remote Config mitmproxy addon (Klar pins, so it has no dump) and for GBM's TOTP-seed takeover over its API.
---

# Get credentials out of a capture

**What to look for** is in [`docs/credentials.md`](../../../docs/credentials.md): for each provider, which
request and which header or body field maps to each auth field. That doc is the reference; this skill only
adds how to handle the dump safely. App versions drift, so confirm against what's actually in the dump.
The user's side of the process (proxy setup, saving the dump) is its "Capturing app traffic with
mitmproxy" section.

## Rules

- Only the user's **own** accounts.
- **Never print or quote secret values** (tokens, signatures, keys, passwords, request bodies, cookies),
  and never put them in memory, docs, commits or chat. Show names, lengths and at most 4 characters.
- Read the dump with a short script and **write the fields straight to `auth_<id>.json`** (merge with an
  existing file, `chmod 600`) instead of passing values through your own output.
- Dumps and auth files stay gitignored (`captures/`, `*.flows`, `*.har`, `auth_<id>.json`); never `git add`
  them.
- A host that never shows up in the dump means the app pins it: stop and tell the user. Don't help bypass
  attestation, captcha or biometrics.

## How

1. Read the dump: `.flows` via mitmproxy (`io.FlowReader(open(path, "rb")).stream()`, `flow.request` has
   `.pretty_host`, `.path`, `.headers`, `.content`); `.har` is JSON (`log.entries[].request`, binary
   bodies base64 when `postData.encoding == "base64"`).
2. List host + method + path and auth-looking header **names** first; find the request named in
   `docs/credentials.md`. If several match, use the **latest**.
3. Extract and write. Traps: DiDi's `signed_query` is the raw query string exactly as sent, and its body
   goes in verbatim (`body`); Mercado Pago's `token` drops `Bearer `; Plata's `auth_request` is base64 of
   the raw body **bytes**; Openbank's password and Klar's key/login are typed in by the user, never taken
   from the dump.
4. Tell the user which fields were written and which they add by hand, then verify with
   `finscraper-mx balances <id> --auth auth_<id>.json` (report success or the error class, not balances).
5. If the request's shape no longer matches `docs/credentials.md`, say what changed (by name) so the doc
   and provider can be updated. Remind them to delete the dump.

## Klar: reconstruct the Remote Config addon, then read its logs

Klar pins its own hosts, so there is no useful dump. Its device key is shown on a hidden dev menu that a
Firebase Remote Config flag unlocks, and Remote Config is not pinned. Nothing for this lives in the repo;
when the user asks, write a one-file mitmproxy addon to a gitignored path (e.g. `captures/klar_rc.py`)
to this spec:

- Run it as
  `mitmdump -s captures/klar_rc.py --listen-port 8080 --allow-hosts 'firebaseremoteconfig\.googleapis\.com'`.
  `--allow-hosts` is essential: every other host (all the pinned Klar APIs) is passed through without TLS
  interception, so the app keeps working behind the proxy.
- `response(flow)`: when the host is `firebaseremoteconfig.googleapis.com` and the path contains
  `namespaces/firebase:fetch` (skip other namespaces such as fireperf), parse the JSON body, set
  `entries["instrumentation_enabled"] = "true"` and `entries["file_logging_enabled"] = "true"` (create
  `entries` if missing), set `state` to `"UPDATE"`, write it back with `flow.response.set_text(...)`, and
  log that the flags were injected.
- Print the laptop's LAN IP and the phone steps: proxy to IP:8080, trust http://mitm.it, **reinstall
  Klar, then open it and sign in with the proxy on** (Remote Config is only fully fetched on the first
  launch after an install).

Then, on the phone: **three-finger press-and-hold** opens the dev menu.

- `private_key_pem`: MFA → Inspect MFA keys. The user copies it into `auth_klar.json` themselves; never
  ask them to paste it into chat.
- `device_id`: **Archivos de registro → Compartir archivos de registro**; the user saves the log files to
  the laptop and gives you the path. Search them for `mfa-accept: TRUSTED_DEVICE:<uuid>` (take the most
  recent; they should all be the same UUID) and write that UUID as `device_id` into `auth_klar.json`.
  The logs can hold tokens and personal data: search them with a script, print only the match count and a
  redacted UUID, and remind the user to delete them afterwards.

## GBM: device id from a dump, then TAKE OVER the TOTP seed over the API

GBM (`auth.gbm.com`) doesn't pin, so a normal capture works. But `GET /security-settings/totp/key` mints
a **new** enrollment secret on every call — it is NOT a readout of the active seed, and the app's in-app
seed is encrypted on the phone. So the only route to a headless seed is to **activate one the scraper
controls** (a TOTP takeover), which **replaces the account's in-app authenticator**. This modifies the
user's MFA: **get explicit confirmation first**, and warn that their GBM app's own code generator stops
working until they re-enroll it — and re-enrolling rotates the seed back and breaks the scraper. Only do
it if they'll log into GBM through the scraper, not the app.

Collect:
- `device_id`: from a dump, the `device-mac-address` header on any `auth.gbm.com/api/v1/session/...`
  request (a UUID; the phone's authorized device). Write it to `auth_gbm.json`.
- `username`, `password`: typed by the user, never from the dump.
- `totp_secret`: **activate it.** Run a short script — base `https://auth.gbm.com/api`, mobile headers
  incl. `device-mac-address: <device_id>`, `clientId=4c392d524cc1460c93c2c2f564c38215`:
  1. `POST /v1/session/user` with PKCE `codeChallenge` (SHA256 of a `codeVerifier`) + `latitude`/
     `longitude`. The response is **either** `{authorizationCode}` directly (no MFA this login) **or**
     `{challengeInfo:{session,timestamp}}`. In the challenge case, ask the user for one current code from
     their GBM app and `POST /v1/session/user/challenge` (`code`, `codeVerifier`, `session`,
     `challengeType="SOFTWARE_TOKEN_MFA"`) → `authorizationCode`.
  2. `POST /v1/session/token` `{clientId, code: authorizationCode, codeVerifier}` → `accessToken`.
  3. `GET /v1/security-settings/totp/key` (Bearer) → a fresh `secretCode` (base32 seed).
  4. Generate its TOTP (RFC 6238: base32 → HMAC-SHA1 → 6 digits / 30 s) against GBM **server time**
     (offset = step-1 `challengeInfo.timestamp` − local ms, or ~0 if absent). **After explicit user YES**,
     `POST /v2/security-settings/totp/confirm` (Bearer) body
     `{"code": <that TOTP>, "friendlyDeviceName": "friendlyDeviceName"}` (fall back to `/v1/...` on 4xx).
     On `{"id":"Success"}` the fetched `secretCode` is now the active seed — write it to `auth_gbm.json`
     as `totp_secret`. **Never print the seed.**

GBM is **one active client**: one authorized device, one TOTP seed, one session. A read logs the phone
app out; signing in on the phone bumps `device_id`; re-enrolling the app authenticator rotates the seed —
any makes reads raise `SessionExpired`, so dedicate the account to the scraper or re-run setup. Final
file: `{username, password, totp_secret, device_id}` (optional `contract_id`). Verify with
`finscraper-mx balances gbm --auth auth_gbm.json`.
