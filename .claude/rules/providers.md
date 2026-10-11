# Rules — finscraper-mx

These are enforced by the compliance gate (`.claude/hooks/precommit_gate.py --check`). Keep them true.

## Interface

- The **only** public way to use a provider is `get_provider(id)(**auth).fetch_balances()`.
- A provider is a `finscraper_mx.core.Provider` subclass with:
  - `id` — short snake_case, **equal to its registry key** in `institutions/__init__.py`.
  - `name` — human display name.
  - `auth_fields` — a **tuple** of the credential field names the caller passes.
  - `fetch_balances() -> list[Balance]` — the one read path.
- Do **not** add provider-specific public read methods, and do **not** document direct-import usage
  (`from finscraper_mx.institutions.x import X`). A one-off credential helper (e.g. a `login` classmethod
  to mint a token) is allowed, but it is setup, not the "call".
- Values are in **MXN**; amounts are **`Decimal`**, never float.

## Statelessness & secrets

- The library **never persists** credentials or sessions. The caller supplies `auth` and stores it.
- **Never commit secrets.** Account ids, tokens, passwords belong in local gitignored files
  (`*_config.json`, `*_tokens.json`, `*_values.json`, `auth*.json`, `.env`, `.har`). A fixed public app
  identifier is fine; nothing personal is hardcoded.

## Dependencies

- Runtime dependencies are declared in `pyproject.toml` (`dependencies`): `httpx` for every provider, plus
  `curl-cffi` for Mifel's login (it impersonates
  an iOS TLS fingerprint to pass Mifel's WAF; see `docs/flows/mifel.md`). Add a new one only when a provider
  can't work without it. No per-provider extras, no import guards: import dependencies directly.

## Keep these in sync (the gate checks all of them)

For every provider `<id>`:
1. `src/finscraper_mx/institutions/<id>.py` — the provider.
2. Registered in `src/finscraper_mx/institutions/__init__.py`.
3. `tests/test_<id>.py` — mocked `httpx`, **fake** ids/tokens/amounts only.
4. A row in the README supported-institutions table.
5. Shown in the `CLAUDE.md` architecture diagram (and its providers table).
6. `docs/flows/<id>.md` — a detailed mermaid call-flow (auth + reads).

## Mermaid

GitHub is strict: in flowchart edge labels **quote** text that contains `()` (`|"get_provider(id)"|`),
and in sequence diagrams **avoid `;`** (it's a statement separator).
