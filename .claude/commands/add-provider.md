---
description: Scaffold a new finscraper-mx provider on the standard interface (module, registry, test, README row, CLAUDE.md diagram, flow doc), then run the gate.
argument-hint: <institution name or id>
---

Add a provider for: **$ARGUMENTS**

Use the `add-provider` skill and follow its steps in order: create
`src/finscraper_mx/institutions/<id>.py` (a `Provider` subclass, standard interface), register it, add
`tests/test_<id>.py` (mocked `httpx`, fake data), add the README row, add the provider to the `CLAUDE.md`
architecture diagram and its table, and add `docs/flows/<id>.md` (mermaid). Keep everything on the standard
`get_provider(id)(**auth).fetch_balances()` pattern.

Finish by running the gate and fixing issues until it reports `COMPLIANCE OK`:

```bash
.venv/bin/python .claude/skills/precommit-compliance/scripts/check_compliance.py
```
