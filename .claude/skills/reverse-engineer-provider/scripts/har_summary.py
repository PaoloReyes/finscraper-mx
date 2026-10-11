#!/usr/bin/env python3
"""Summarize a browser HAR capture without leaking what's in it.

    python3 har_summary.py capture.har [--host mercadopago] [--find 1534.50 --find 820.00]

Prints one line per JSON/XHR request: method, status, host+path (no query values), and the *names* of
auth-looking headers and cookies sent. Then, for each response, the shape of its JSON with values
redacted (types only), so the output is safe to paste into a chat or a PR.

`--find` takes amounts you can see on screen (your balance, a Cajita) and lists the requests whose
response contains them, with the JSON path to each match. That is usually how the balance endpoint is
found. Matched values are shown as `<match>`, never the number itself.

Standard library only. The HAR itself is a secret (cookies, tokens): keep it gitignored (`*.har`).
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

AUTH_HINTS = ("auth", "token", "session", "csrf", "xsrf", "cookie", "device", "x-", "bearer", "nonce")
SKIP_TYPES = ("image/", "font/", "text/css", "javascript", "text/html")


def shape(value, depth=0, max_depth=6):
    """JSON structure with leaf values replaced by their type."""
    if depth >= max_depth:
        return "…"
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(value[0], depth + 1), f"…{len(value)} items"] if value else []
    return type(value).__name__


def find_amounts(value, targets, path="$"):
    """Yield JSON paths whose leaf equals one of the target amounts."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from find_amounts(v, targets, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from find_amounts(v, targets, f"{path}[{i}]")
    elif isinstance(value, (int, float, str)) and not isinstance(value, bool):
        try:
            number = Decimal(str(value).replace(",", "").replace("$", "").strip())
        except InvalidOperation:
            return
        if any(abs(number - t) < Decimal("0.005") or abs(number - t * 100) < Decimal("0.5") for t in targets):
            yield path  # also catches amounts sent in cents


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("har")
    parser.add_argument("--host", help="only requests whose host contains this text")
    parser.add_argument("--find", action="append", default=[], help="an amount visible on screen")
    parser.add_argument("--shapes", action="store_true", help="also print each response's JSON shape")
    args = parser.parse_args()
    targets = [Decimal(t.replace(",", "")) for t in args.find]

    entries = json.load(open(args.har, encoding="utf-8"))["log"]["entries"]
    hits = []
    for n, entry in enumerate(entries):
        req, res = entry["request"], entry["response"]
        url = urlsplit(req["url"])
        mime = res.get("content", {}).get("mimeType", "")
        if args.host and args.host not in url.netloc:
            continue
        if any(t in mime for t in SKIP_TYPES):
            continue
        headers = sorted({h["name"].lower() for h in req.get("headers", []) if any(x in h["name"].lower() for x in AUTH_HINTS)})
        cookies = sorted({c["name"] for c in req.get("cookies", [])})
        query = sorted({q["name"] for q in req.get("queryString", [])})
        print(f"#{n:<4} {req['method']:<6} {res['status']:<4} {url.netloc}{url.path}" + (f"  ?{','.join(query)}" if query else ""))
        if headers:
            print(f"        headers: {', '.join(headers)}")
        if cookies:
            print(f"        cookies: {', '.join(cookies)}")
        text = res.get("content", {}).get("text") or ""
        if "json" not in mime or not text:
            continue
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            continue
        if args.shapes:
            print("        shape: " + json.dumps(shape(body))[:800])
        if targets:
            paths = list(find_amounts(body, targets))
            if paths:
                hits.append((n, f"{req['method']} {url.netloc}{url.path}", paths))

    if targets:
        print("\n== responses containing the amounts you gave")
        for n, where, paths in hits or [(None, "none found (try --find with cents, or another page)", [])]:
            print(f"#{n} {where}" if n is not None else where)
            for p in paths[:10]:
                print(f"        <match> at {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
