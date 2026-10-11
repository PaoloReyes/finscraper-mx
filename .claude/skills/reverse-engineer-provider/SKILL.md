---
name: reverse-engineer-provider
description: Reverse-engineer a Mexican institution's own web or app API to build a finscraper-mx provider when there is no official balance API (as done for GBM+). Use before add-provider when the institution needs a login/session instead of an API key.
---

# Reverse-engineer an institution for finscraper-mx

Use this when an institution has no official API for balances (or the official one misses what the user
wants, e.g. savings pockets). It covers **finding** the private API. Building the provider afterwards
follows the `add-provider` skill and its gate. Lessons come from GBM+ (`institutions/gbm.py`,
`docs/flows/gbm.md`).

## Ground rules

- **The user's own account only, read-only.** Map only the calls that read balances. Never touch
  transfer, order or settings endpoints, not even to "see the shape".
- **Never evade bot protection.** No captcha solving, no fingerprint spoofing, no rotating IPs, no
  replaying signed device attestations. If login is guarded like that, the user logs in themselves (in
  their browser or app) and the provider **reuses the session** they created (README: "prefer sessions
  over passwords"). If even session reuse needs evasion, stop and tell the user.
- **Captures are secrets.** A HAR contains cookies and tokens. It stays on the user's machine,
  gitignored (`*.har`, `captures/`). Never paste raw HAR content into chat, a PR or a test. Use
  `scripts/har_summary.py`, which prints names and shapes, not values.
- **Say it's unofficial.** The module docstring and README row say the provider is reverse-engineered
  and may break the institution's terms, as GBM's do.

## 1. Capture (the user does this, in their own browser)

1. Open the institution's **web app** in Chrome or Firefox and sign in normally (MFA, captcha and all).
2. DevTools, **Network** tab: tick *Preserve log*, filter **All**. Many sites (Mercado Pago) render
   balances into the page HTML or a script blob, which a Fetch/XHR filter hides.
3. Reload the dashboard, then open each page that shows a balance you want (main account, savings
   pockets, investments). For the login flow, sign out and sign in again with the log running.
4. Right-click the request list, **Save all as HAR (with sensitive data)**. The plain export strips
   cookies, and then you can't tell which ones the API needs. Save it in the repo folder as e.g.
   `captures/<id>.har` (gitignored). Capture while **already signed in**: a login-only capture has no
   balance calls in it.
5. Write down 2 or 3 amounts visible on screen. These are the search keys for step 2.

The web app is the first choice: mobile apps usually pin certificates and attest the device, so their
traffic can't be read without the kind of tampering the ground rules forbid.

## 2. Find the endpoints

```bash
python3 -I .claude/skills/reverse-engineer-provider/scripts/har_summary.py captures/<id>.har \
  --host <domain> --find 1534.50 --find 820.00 --shapes
```

- `--find` lists the responses that contain the on-screen amounts: the JSON path for JSON responses
  (also matching amounts sent in cents), or the masked surrounding text for HTML and scripts (look for
  an embedded state blob like `__PRELOADED_STATE__`). Those are the balance sources.
- In the browser, Cmd/Ctrl+F inside the Network panel searches every response body for an amount.
- For each, note: method, host and path, the **auth mechanism** (header names like `authorization`,
  `x-csrf-token`, cookie names), any ids in the path and **where those ids came from** (an earlier
  response, which becomes a discovery call).
- Look for a **cheap identity call** (`/me`, `/user`, `/session`) that fails fast with 401 when the
  session lapsed. That becomes the `SessionExpired` check.

## 3. Pick the credential (the `auth_fields`)

Prefer, in order:

1. **A refresh token** the provider can exchange silently for an access token (GBM: OAuth2 PKCE + TOTP
   once via a `login` classmethod, then `POST /session/user/refresh` on every read). Best: long-lived
   and no browser needed.
2. **Session cookies** copied from the user's signed-in browser (one `auth_fields` entry per cookie the
   API actually needs, or one `cookies` string). Simple and no evasion, but it expires when the web
   session does, and the user re-copies it.
3. **Password login** in code: only if the login is plain HTTP calls with no captcha/fingerprint, and
   even then only as a one-time `login` helper that returns (1) or (2). Never store the password.

Find the minimum: replay the balance call with fewer cookies/headers until it breaks (the user runs this
locally; sandboxes usually can't reach bank hosts). Ship only what's required.

Static values from the web bundle (a public OAuth `client_id`, an app version header) may be
hardcoded, like GBM's `CLIENT_ID`. Anything per-user (account ids, contract ids, device ids) must not
be: **discover** it from a response (GBM discovers contracts, then strategies) or take it as an optional
argument.

## 4. Quirks GBM taught us

- **Extra headers the API insists on.** GBM's login rejects requests without `device-latitude` /
  `device-longitude`. Send them from a caller-overridable default (`location=(lat, lon)`), never the
  user's real location hardcoded.
- **Several hosts, one session.** GBM spreads reads over `auth.gbm.com`, `api.appgbm.com` and
  `homebroker-api.gbm.com` with the same bearer token. Draw them all in the flow doc.
- **Shapes drift.** Parse defensively (`row.get("id") or row.get("contractId")`), skip inactive or
  "deprecated" rows, and raise `LayoutChanged` (not `KeyError`) when the field you need is gone.
- **Money arrives as strings, floats or cents.** Always `Decimal(str(value)).quantize(CENT)`.
  Never float.
- **Auth failures aren't always 401.** GBM returns 200 with `id != "Success"`. Check the body too, and
  map it to `SessionExpired` with a message telling the user how to get a new credential.
- **Keep it stateless.** The first GBM version had `token_file` and `renew()`. That was removed: the
  caller passes the credential in and stores it. A `login` classmethod that *returns* a credential is
  fine; public read methods other than `fetch_balances()` are not.

## What Mercado Pago taught us

- The developer API was payments-only, and the login sat behind reCAPTCHA Enterprise plus a
  `browser-assessment` check. So the credential is the browser's `Cookie` header (one `cookies`
  field), and the provider never logs in.
- No balance JSON call at all: the home page is server-rendered, and the numbers live in a script blob
  (`__NORDIC_RENDERING_CTX__`, `_n.ctx.r={...}`). Pull it out with a regex plus
  `json.JSONDecoder().raw_decode` (JavaScript follows the JSON), then walk to the widget by its `type`
  rather than by index.
- Request with `follow_redirects=False`: an expired session shows up as a 302 to the login.
- Amounts were split as `{"fraction": "12345", "cents": "67"}`. Strip separators before `Decimal`.

## 5. Build it

Hand over to the `add-provider` skill (module, registry, tests, README row, CLAUDE.md diagram,
`docs/flows/<id>.md`, gate). For reverse-engineered providers, also:

- Tests mock every host with `httpx.MockTransport`, using **response shapes from the HAR summary**
  with fake values. Cover the expired-session path and a `LayoutChanged` path.
- The flow doc shows the one-time credential setup separately from each read, as `docs/flows/gbm.md`
  does.
- The README "Obtaining credentials" entry tells the user exactly where to get the credential (which
  cookie, which page) and how long it lasts.
- Verify once against the real account (the user runs `finscraper-mx balances <id> --auth auth.json`
  locally) before the PR leaves draft.
