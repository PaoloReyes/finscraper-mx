---
description: Add a new finscraper-mx provider end to end (module, registry, test, README, CLAUDE.md diagram, flow doc, gate).
argument-hint: <institution name or id>
---

Use the `provider-builder` agent to add a provider for: **$ARGUMENTS**

The agent must follow the `add-provider` skill step by step and finish by running the gate
(`.venv/bin/python .claude/hooks/check_compliance.py`) until it reports `COMPLIANCE OK`.
