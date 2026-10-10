#!/usr/bin/env python3
"""PreToolUse(Bash) hook: block `git commit` / `git push` unless the compliance gate passes.

Reads the tool call from stdin. If the Bash command is a git commit or push, it runs the compliance
checker; on failure it exits 2 (which blocks the tool call and shows the reason to the agent). Any other
command, or a passing gate, exits 0 (allow).
"""
import json
import re
import subprocess
import sys
from pathlib import Path


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    command = (payload.get("tool_input") or {}).get("command", "")
    if not re.search(r"\bgit\s+(commit|push)\b", command):
        return 0  # not a commit/push — allow

    # locate the repo root (dir containing the checker)
    rel = Path(".claude/hooks/check_compliance.py")
    start = Path(payload.get("cwd") or Path.cwd())
    root = next((base for base in [start, *start.parents] if (base / rel).exists()), None)
    if root is None:
        return 0  # not this repo — allow

    python = root / ".venv/bin/python"
    result = subprocess.run(
        [str(python) if python.exists() else sys.executable, str(root / rel)],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            "Blocked: finscraper-mx compliance gate failed — fix before committing/pushing.\n\n"
            + (result.stdout or "")[-1800:],
            file=sys.stderr,
        )
        return 2  # block the git commit/push
    return 0


if __name__ == "__main__":
    sys.exit(main())
