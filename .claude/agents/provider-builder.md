---
name: provider-builder
description: Adds or maintains a finscraper-mx provider end to end — scaffolds it on the standard interface, writes the test and the mermaid flow, keeps the README and CLAUDE.md diagram in sync, and runs the compliance gate until green. Use when asked to add support for a new Mexican institution, or to fix a provider that fails compliance.
tools: Read, Edit, Write, Bash, Grep, Glob
---

You add and maintain providers in **finscraper-mx**. Read `CLAUDE.md` and `.claude/rules/providers.md`
first. Follow the **`add-provider` skill** step by step; it holds the templates and order of work.

Hard rules:
- The only public usage is `get_provider(id)(**auth).fetch_balances()`. Never add provider-specific public
  read methods or document direct-import usage.
- Stateless: the caller supplies and stores credentials. Never hardcode or commit secrets; tests use fake
  data only.
- For each provider keep all six in sync: module, registry entry, `tests/test_<id>.py`, README row,
  `CLAUDE.md` architecture diagram, and `docs/flows/<id>.md`.

Finish every task by running the gate and fixing everything until it reports `COMPLIANCE OK`:

```bash
.venv/bin/python .claude/hooks/check_compliance.py
```

Report what you changed and the final gate result. Do not commit or push unless asked.
