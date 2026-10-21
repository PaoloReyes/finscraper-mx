#!/usr/bin/env python3
r"""Klar credential-capture helper — one self-contained file.

Klar's `klar` provider needs four things captured once from your own paired phone:
`private_key_pem`, `device_id`, `username`, `password`. This script automates the laptop/proxy side:
it unlocks Klar's hidden "Inspect MFA keys" dev screen (by flipping a Firebase Remote Config flag that
Klar does NOT pin) and prints the trusted `deviceId` off a gw.klar.mx refresh. You copy the private key
off the on-screen inspector and the username/password are your own.

This file is BOTH the runner and the mitmproxy addon:

    python scripts/klar_capture.py          # prints your IP + phone steps, then launches mitmproxy

(Equivalent to: mitmdump -s scripts/klar_capture.py --allow-hosts '(firebaseremoteconfig\.googleapis\.com|gw\.klar\.mx)' --listen-port 8080)

Prerequisite: `pip install mitmproxy`. Run it on a laptop the phone can reach on the same Wi-Fi (if your
router isolates clients, use a phone/laptop hotspot). Full walkthrough: docs/flows/klar.md.

Legal/safety: use only with your own Klar account. The key + credentials grant full account access — keep
auth.json private and never commit it.

The capture reusable pattern (flip a non-pinned Remote Config flag to unlock an in-app inspector, read the
device id off an un-pinned auth call) applies to other capture-based providers too; klar is the first.
"""
from __future__ import annotations

import json
import re

# ---------------------------------------------------------------------------
# mitmproxy addon (used when loaded with `mitmdump -s this_file.py`)
# ---------------------------------------------------------------------------
RC_HOST = "firebaseremoteconfig.googleapis.com"
GW_HOST = "gw.klar.mx"
FLAGS = ["instrumentation_enabled", "file_logging_enabled"]  # unlock the dev menu + on-device file logs
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

try:  # only importable inside mitmproxy; the __main__ runner below does not need it
    from mitmproxy import ctx, http

    def _scan_gw(label: str, text: str | None) -> None:
        if not text:
            return
        try:
            obj = json.loads(text)
            if isinstance(obj, dict) and obj.get("deviceId"):
                ctx.log.alert(f"[klar] >>> deviceId = {obj['deviceId']}   (copy this into auth.json)")
                return
        except ValueError:
            pass
        for found in sorted(set(_UUID.findall(text))):
            ctx.log.alert(f"[klar] uuid seen in gw {label}: {found}")

    def request(flow: "http.HTTPFlow") -> None:  # noqa: F821
        if flow.request.pretty_host == GW_HOST and "/auth/" in flow.request.path:
            ctx.log.alert(f"[klar] gw auth: {flow.request.method} {flow.request.path}")
            _scan_gw("request", flow.request.get_text())

    def response(flow: "http.HTTPFlow") -> None:  # noqa: F821
        host = flow.request.pretty_host
        if host == GW_HOST and "/auth/" in flow.request.path:
            _scan_gw("response", flow.response.get_text())
            return
        if host != RC_HOST or ":fetch" not in flow.request.path:
            return
        if "namespaces/firebase:fetch" not in flow.request.path:  # skip fireperf / other namespaces
            return
        try:
            data = json.loads(flow.response.get_text() or "{}")
        except ValueError as error:
            ctx.log.alert(f"[klar] firebase:fetch parse failed ({error})")
            return
        entries = data.get("entries")
        if not isinstance(entries, dict):
            entries = {}
        for flag in FLAGS:
            entries[flag] = "true"
        data["entries"] = entries
        data["state"] = "UPDATE"
        flow.response.set_text(json.dumps(data))
        ctx.log.alert(f"[klar] >>> injected {FLAGS} = true into Firebase Remote Config (dev menu unlocked)")
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Runner (used when you do `python scripts/klar_capture.py`)
# ---------------------------------------------------------------------------
def _lan_ip() -> str:
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))  # no packets sent; just resolves the primary interface
        return sock.getsockname()[0]
    except OSError:
        return "<your-laptop-ip>"
    finally:
        sock.close()


def main() -> int:
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    ip = _lan_ip()
    here = Path(__file__).resolve()
    print(f"""
============================ Klar capture ============================
Laptop IP (point the phone's Wi-Fi proxy here):   {ip}   port 8080

On the phone (same Wi-Fi):
  1. Settings -> Wi-Fi -> (i) -> Configure Proxy -> Manual -> Server {ip}, Port 8080.
  2. Safari -> http://mitm.it -> install the profile, then Settings -> General -> About ->
     Certificate Trust Settings -> turn ON full trust for mitmproxy.
  3. Reinstall Klar (forces a fresh Remote Config fetch so the dev flags apply), then sign in.
  4. Open the hidden dev menu: multi-finger press-and-hold on the app -> MFA -> Inspect MFA keys.
     Copy the  -----BEGIN PRIVATE KEY----- ... -----END PRIVATE KEY-----  block  ->  private_key_pem
  5. Watch this terminal for:   [klar] >>> deviceId = <uuid>     ->  device_id

Then save auth.json (keep it private, never commit):
  {{"private_key_pem": "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n",
   "device_id": "<uuid>", "username": "you@example.mx", "password": "..."}}

Watch below for  [klar] >>> injected ...  (flag injection worked) and  [klar] >>> deviceId = ...
Press Ctrl-C to stop.
=====================================================================
""")
    mitmdump = shutil.which("mitmdump")
    if not mitmdump:
        print("mitmdump not found. Install it:  pip install mitmproxy", file=sys.stderr)
        return 1
    cmd = [mitmdump, "-s", str(here),
           "--allow-hosts", r"(firebaseremoteconfig\.googleapis\.com|gw\.klar\.mx)",
           "--listen-port", "8080"]
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
