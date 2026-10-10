---
description: Run the finscraper-mx compliance gate (provider compliance, per-provider tests, README + architecture diagram + flow docs in sync, no tracked secrets, green suite).
---

Run the compliance gate from the repo root and report the result:

```bash
.venv/bin/python .claude/skills/precommit-compliance/scripts/check_compliance.py
```

If it prints `COMPLIANCE OK`, say so. If it prints `COMPLIANCE FAILED`, list each failing item and exactly
what to fix. Do not commit or push while it fails.
