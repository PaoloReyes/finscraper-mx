#!/usr/bin/env python3
"""finscraper-mx compliance gate, as a Claude Code hook and as a manual check.

Two modes, one file:

  Hook (default): a PreToolUse hook on Bash. Reads the tool call from stdin. If it is a `git commit` or
  `git push`, runs the gate under the project venv and exits 2 on failure (blocks the command and shows
  the reason). Anything else is allowed.

  Manual: `.venv/bin/python .claude/hooks/precommit_gate.py --check` runs the gate directly and prints
  the report. The provider-builder agent and anyone else can use this.

The gate checks: provider standard interface, per-provider tests, README / CLAUDE.md diagram / flow docs /
credential guide in sync, no tracked secrets, and a green test suite.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REL = Path(".claude/hooks/precommit_gate.py")


def repo_root(start: Path) -> Path | None:
    """The directory that contains this hook file, searched upward from `start`."""
    return next((base for base in [start, *start.parents] if (base / REL).exists()), None)


# ---------------------------------------------------------------------------
# Gate (runs under the project venv: it imports finscraper_mx)
# ---------------------------------------------------------------------------

def run_gate() -> int:
    root = HERE.parents[2]
    sys.path.insert(0, str(root / "src"))

    failures: list[str] = []

    def check(ok: bool, msg: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'}  {msg}")
        if not ok:
            failures.append(msg)

    def section(title: str) -> None:
        print(f"\n== {title}")

    from finscraper_mx.core import Provider
    from finscraper_mx.institutions import PROVIDERS, get_provider

    section("provider compliance (standard interface only)")
    for pid in PROVIDERS:
        try:
            cls = get_provider(pid)
        except Exception as error:  # noqa: BLE001
            check(False, f"{pid}: cannot load ({error})")
            continue
        check(isinstance(cls, type) and issubclass(cls, Provider), f"{pid}: subclasses Provider")
        check(getattr(cls, "id", None) == pid, f"{pid}: class.id matches the registry key")
        check(bool(getattr(cls, "name", "")), f"{pid}: has a display name")
        check(isinstance(getattr(cls, "auth_fields", None), tuple), f"{pid}: auth_fields is a tuple")
        check(not getattr(cls, "__abstractmethods__", set()), f"{pid}: implements fetch_balances")

    section("per-provider tests")
    for pid in PROVIDERS:
        check((root / "tests" / f"test_{pid}.py").exists(), f"{pid}: has tests/test_{pid}.py")

    section("README in sync")
    readme = (root / "README.md").read_text(encoding="utf-8")
    for pid in PROVIDERS:
        check(re.search(rf"^\|.*`{re.escape(pid)}`.*\|\s*$", readme, re.M) is not None, f"{pid}: listed in the README table")
    check("get_provider(" in readme, "README shows the standard get_provider() usage")
    check("from finscraper_mx.institutions" not in readme, "README avoids direct-import examples (use get_provider)")

    section("architecture diagram up to date (CLAUDE.md)")
    claude = (root / "CLAUDE.md").read_text(encoding="utf-8")
    match = re.search(r"```mermaid(.+?)```", claude, re.S)
    diagram = match.group(1) if match else ""
    check(bool(diagram.strip()), "CLAUDE.md has a mermaid architecture diagram")
    for pid in PROVIDERS:
        name = get_provider(pid).name
        check(name in diagram or pid in diagram, f"{pid}: shown in the architecture diagram ({name})")

    section("per-provider flow diagrams")
    for pid in PROVIDERS:
        flow = root / "docs" / "flows" / f"{pid}.md"
        has_diagram = flow.exists() and "```mermaid" in flow.read_text(encoding="utf-8")
        check(has_diagram, f"{pid}: has docs/flows/{pid}.md with a mermaid diagram")

    section("credential guide (docs/credentials.md)")
    creds = root / "docs" / "credentials.md"
    guide = creds.read_text(encoding="utf-8") if creds.exists() else ""
    for pid in PROVIDERS:
        check(f"`{pid}`" in guide, f"{pid}: has a section in docs/credentials.md")

    section("no secrets tracked")
    tracked = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout.split()
    bad = [f for f in tracked if re.search(r"(_tokens|_config|_values|_auth)\.json$|(^|/)auth.*\.json$|\.env$|\.har$", f)]
    check(not bad, "no credential/session files tracked" + (f" (found: {', '.join(bad)})" if bad else ""))

    section("tests pass")
    result = subprocess.run([sys.executable, "-m", "pytest"], cwd=root)
    check(result.returncode == 0, "pytest passes")

    print()
    if failures:
        print(f"COMPLIANCE FAILED — {len(failures)} issue(s):")
        for msg in failures:
            print(f"  - {msg}")
        return 1
    print("COMPLIANCE OK")
    return 0


# ---------------------------------------------------------------------------
# Hook entry (runs with the system python: no library imports here)
# ---------------------------------------------------------------------------

def hook() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    command = (payload.get("tool_input") or {}).get("command", "")
    if not re.search(r"\bgit\s+(commit|push)\b", command):
        return 0  # not a commit or push: allow

    root = repo_root(Path(payload.get("cwd") or Path.cwd()).resolve())
    if root is None:
        return 0  # not this repo: allow

    venv_python = root / ".venv" / "bin" / "python"
    interpreter = str(venv_python) if venv_python.exists() else sys.executable
    result = subprocess.run([interpreter, str(root / REL), "--check"], cwd=root, capture_output=True, text=True)
    if result.returncode != 0:
        print(
            "Blocked: finscraper-mx compliance gate failed. Fix before committing or pushing.\n\n"
            + (result.stdout or "")[-1800:],
            file=sys.stderr,
        )
        return 2  # blocks the git commit or push
    return 0


if __name__ == "__main__":
    sys.exit(run_gate() if "--check" in sys.argv else hook())
