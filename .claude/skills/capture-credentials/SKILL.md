---
name: capture-credentials
description: Turn the user's own mitmproxy dump (.flows) or .har capture into a finscraper-mx auth_<id>.json (DiDi, Mercado Pago, Plata, Klar, Openbank) without exposing secrets. Use when the user hands over a capture, sets up or renews credentials, or a provider raises SessionExpired.
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
