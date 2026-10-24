---
description: Add a new finscraper-mx provider end to end (module, registry, test, README, CLAUDE.md diagram, flow doc, credential guide, gate).
argument-hint: <institution name or id>
---

Use the `provider-builder` agent to add a provider for: **$ARGUMENTS**

The agent must follow the `scaffold-provider` skill step by step and finish by running the gate
(`.venv/bin/python .claude/hooks/precommit_gate.py --check`) until it reports `COMPLIANCE OK`.
