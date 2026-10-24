"""Command line for trying providers by hand.

    finscraper-mx list
    finscraper-mx balances <institution> --auth auth_<institution>.json

Credentials are read from a JSON file (never from arguments, which end up in shell history).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

from .core import FinscraperError
from .institutions import PROVIDERS, get_provider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="finscraper-mx", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list supported institutions and the auth fields they need")
    balances = commands.add_parser("balances", help="print an institution's balances as JSON")
    balances.add_argument("institution", help="institution id, see `list`")
    balances.add_argument("--auth", type=Path, required=True, help="JSON file with the auth fields")
    args = parser.parse_args(argv)

    if args.command == "list":
        for id in sorted(PROVIDERS):
            try:
                provider = get_provider(id)
            except ImportError as error:
                print(f"{id:<16} ({error})")
                continue
            print(f"{id:<16} {provider.name:<24} auth: {', '.join(provider.auth_fields) or '-'}")
        return 0

    try:
        provider = get_provider(args.institution)(**json.loads(args.auth.read_text()))
        result = [balance.to_dict() for balance in provider.fetch_balances()]
    except (KeyError, ImportError, OSError, json.JSONDecodeError, FinscraperError, NotImplementedError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except httpx.HTTPError as error:
        print(f"error: {args.institution}: {_describe(error)}", file=sys.stderr)
        return 1
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    print()
    return 0


def _describe(error: httpx.HTTPError) -> str:
    """One line for a failed request, instead of httpx's multi-line message and traceback."""
    try:
        url = error.request.url.copy_with(query=None)
    except RuntimeError:  # no request attached
        return f"request failed: {error}"
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code} from {url}"
    return f"request to {url} failed: {type(error).__name__}"


if __name__ == "__main__":
    sys.exit(main())
