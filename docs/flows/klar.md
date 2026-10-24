# Klar — flow

Getting the credentials: [`docs/credentials.md`](../credentials.md#klar).

Auth: the device RSA keypair + its trusted-device id, captured once from the user's own **paired phone**,
plus the account `username` + `password`. Klar gates every data call with a per-request RSA
challenge-response bound to a trusted device, and pairing a *new* device is KYC-gated (face scan + SMS),
so the library can't enrol itself — it reuses the phone's already-registered key. Each `fetch_balances()`
does the app's real flow: a two-step Keycloak login (provisional `klar-client` → RBAC `klar-client-mfa`,
solving an RSA login challenge in between), then a `307`-challenged read of `/balances2/api/v0/user`.
Reads one `Balance` per non-credit account — `DEBIT` (Cuenta, checking), `SAVINGS` (Ahorro) and
`SECURED_DEPOSIT` (Depósito en garantía, investment) — in MXN. `CREDIT` (a credit line, not a holding) and
`DEBIT_PENDING` (unsettled) are excluded.

Klar allows **one active session per account**: while the phone app is signed in, login returns `423` and
the read raises `SessionExpired`. Close or idle the phone app (its session lapses after ~30 min) and retry;
logging the phone out does **not** un-pair it, so the phone and this reader share the one trusted device —
just not a live session at the same moment.

## One-time setup (capture the device key + id from your phone)

Klar ships an internal "Inspect MFA keys" dev screen gated by a Firebase Remote Config flag
(`instrumentation_enabled`), and Firebase config is **not** TLS-pinned, so a local mitmproxy can flip that
flag true in the Remote Config response; after a reinstall the screen shows the device's
`-----BEGIN PRIVATE KEY-----`. The `deviceId` travels in `mfa-accept: TRUSTED_DEVICE:<uuid>` and in
`gw.klar.mx` auth bodies, so it can be found in the same dump. Step by step:
[`docs/credentials.md`](../credentials.md#klar).

```mermaid
sequenceDiagram
  participant U as user
  participant Ph as Klar app (paired phone)
  participant M as mitmproxy (laptop)
  participant FB as firebaseremoteconfig.googleapis.com
  participant GW as gw.klar.mx

  U->>M: start mitmproxy, saving a dump
  U->>Ph: set Wi-Fi proxy to the laptop, trust the mitm CA
  Ph->>FB: Remote Config fetch (firebase namespace)
  M->>Ph: inject instrumentation_enabled=true into the RC response
  U->>Ph: open the hidden dev menu (multi-finger press-and-hold) -> Inspect MFA keys
  Ph-->>U: shows the device private key (copy it -> private_key_pem)
  Ph->>GW: POST /api/v2/auth/refreshToken {token, deviceId}
  Note over U: find deviceId in the dump (ask Claude)
  Note over U: save {private_key_pem, device_id, username, password} in a local auth_klar.json - keep it private
```

## Each read — `fetch_balances()`

```mermaid
sequenceDiagram
  participant C as caller
  participant P as Klar provider
  participant S as api.klar.mx

  C->>P: get_provider("klar")(private_key_pem, device_id, username, password)
  C->>P: fetch_balances()
  P->>S: POST /login/api/auth/v1/login (mfaAccept TRUSTED_DEVICE:device_id)
  alt a session is already active
    S-->>P: HTTP 423 "session already exists"
    P-->>C: SessionExpired (close/idle the phone app, retry)
  else device not trusted
    S-->>P: HTTP 412 MFA_PAIRING_REQUIRED
    P-->>C: SessionExpired (recapture key + device_id)
  else ok
    S-->>P: provisional klar-client token + RSA mfaChallenge
    P->>P: decrypt challenge with private key (PKCS#1 v1.5)
    P->>S: POST /login/api/auth/v1/login (mfa mfaResponse) -> klar-client-mfa token
    P->>S: GET /balances2/api/v0/user (mfa-accept TRUSTED_DEVICE:device_id)
    S-->>P: HTTP 307 with mfa-challenge + mfa-challenge-id
    P->>P: decrypt challenge
    P->>S: GET /balances2/api/v0/user (mfa-response + mfa-challenge-id)
    S-->>P: balances JSON (accounts with availableBalance {units, nanos})
    P->>P: map DEBIT/SAVINGS/SECURED_DEPOSIT to MXN Decimal; skip CREDIT + DEBIT_PENDING
    P-->>C: list of Balance
  end
```

Notes:

- Klar's AWS load balancer rejects datacenter IPs: from a cloud server, login returns an HTML `403`
  (`awselb/2.0`) whatever the client. Pass the optional `proxy` auth value (`http://host:port`) to send
  this provider's calls through a proxy on a residential connection, e.g. a home machine reached over
  Tailscale running `pip install proxy.py && proxy --hostname <tailscale-ip> --port 8899`.

- After the read, the provider calls `POST gw.klar.mx/api/v2/auth/logout` (best-effort) to **release its
  session**. Klar allows one session per account and it lingers after the token expires, so skipping logout
  would make the next read — or the phone — hit `423`. Logout does not un-pair the device.
- The device key is software (pointycastle RSA-2048) stored in the phone's keychain; it survives app
  reinstalls, so a captured key keeps working until you re-pair (face + SMS), which rotates it.
- `CREDIT`/`DEBIT_PENDING` are excluded on purpose; the reported total mirrors the app's holdings.
- Balance amounts arrive as `{units, nanos}` (nanos = 1e-9 of a unit) and are combined to 2-decimal MXN.
