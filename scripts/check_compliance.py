#!/usr/bin/env python3
"""Pre-commit compliance checks for finscraper-mx.

Verifies that every provider follows the standard interface, has tests, is listed in the README, that
no secrets are tracked, and that the whole suite passes. Exits non-zero on any failure.

Run from the repo root with the dev venv:  .venv/bin/python scripts/check_compliance.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from finscraper_mx.core import Provider  # noqa: E402
from finscraper_mx.institutions import PROVIDERS, get_provider  # noqa: E402

failures: list[str] = []


def check(ok: bool, msg: str) -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {msg}")
    if not ok:
        failures.append(msg)


def section(title: str) -> None:
    print(f"\n== {title}")


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
    check((ROOT / "tests" / f"test_{pid}.py").exists(), f"{pid}: has tests/test_{pid}.py")

section("README in sync")
readme = (ROOT / "README.md").read_text(encoding="utf-8")
for pid in PROVIDERS:
    check(re.search(rf"\|\s*`{re.escape(pid)}`\s*\|", readme) is not None, f"{pid}: listed in the README table")
check("get_provider(" in readme, "README shows the standard get_provider() usage")
check("from finscraper_mx.institutions" not in readme, "README avoids direct-import examples (use get_provider)")

section("no secrets tracked")
tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
bad = [f for f in tracked if re.search(r"(_tokens|_config|_values)\.json$|(^|/)auth.*\.json$|\.env$|\.har$", f)]
check(not bad, "no credential/session files tracked" + (f" (found: {', '.join(bad)})" if bad else ""))

section("tests pass")
result = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT)
check(result.returncode == 0, "pytest passes")

print()
if failures:
    print(f"COMPLIANCE FAILED — {len(failures)} issue(s):")
    for msg in failures:
        print(f"  - {msg}")
    sys.exit(1)
print("COMPLIANCE OK")
