"""GBM (GBM México broker), through its own API.

Read-only: logs in and reads account values. No order or transfer code.

Fully headless: build it with `username` + `password` + `totp_secret` + `device_id` and call
`fetch_balances()`, like any other provider. It logs in on every read, handling both of GBM's login
shapes — an MFA challenge (answered with a code generated from `totp_secret` against GBM's server
time) or an authorization code returned directly — then discovers the login's contracts and active
strategies (or uses the `contract_id`/`accounts` you pass). Raises `AuthRequired` when GBM rejects the
MFA code and `SessionExpired` when `device_id` is no longer the account's authorized device.

Obtaining `totp_secret` and `device_id` is a one-time setup, done from a capture and with Claude (the
`capture-credentials` skill), not through the provider: `device_id` is the app's `device-mac-address`,
and `totp_secret` is a seed *activated* for the account by a TOTP takeover (it replaces the GBM app's
own authenticator). See `docs/credentials.md#gbm`.

GBM is **one active client** — one authorized device, one TOTP seed, one session — so a read logs the
phone app's session out, and using the phone app (its login or re-enrolling its authenticator) breaks
the scraper until setup is re-run. `CLIENT_ID` is GBM's public mobile-app client (same for every user).
Not affiliated with GBM; automated access may be against GBM's terms of use, and a session can place
trades — keep the credentials off shared hosts.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
import time
from decimal import Decimal, InvalidOperation

import httpx

from ..core import AccountKind, AuthRequired, Balance, LayoutChanged, Provider, SessionExpired

CLIENT_ID = "4c392d524cc1460c93c2c2f564c38215"  # GBM mobile app ("app-evolution") client id (same for every user)
AUTH = "https://auth.gbm.com/api/v1"
APPGBM = "https://api.appgbm.com/v1"
CONTRACTS_URL = "https://api.appgbm.com/v2/onboarding/contracts"  # lists the login's contracts
HOMEBROKER = "https://homebroker-api.gbm.com/GBMP/api"
DEFAULT_LOCATION = (19.4326, -99.1332)  # Mexico City; override per customer with location=(lat, lon)
CENT = Decimal("0.01")

# Mobile app identity (GBM iOS app); not secrets, the login API expects these headers.
APP_VERSION = "199"
USER_AGENT = "GBM/199 CFNetwork/3896.100.1.2.1 Darwin/27.0.0"
DEVICE_TYPE = "iPhone18,4"
OS_VERSION = "27.0.1"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _totp(secret: str, when: float | None = None) -> str:
    """Current 6-digit code for GBM's authenticator seed (RFC 6238: base32 secret, HMAC-SHA1, 30 s)."""
    secret = secret.strip().replace(" ", "").upper()
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    counter = int((time.time() if when is None else when) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{code % 1_000_000:06d}"


def _client(client: httpx.Client | None, location: tuple[float, float], device_id: str) -> httpx.Client:
    if client is not None:
        return client
    lat, lon = location
    headers = {
        "content-type": "application/json; charset=UTF-8",
        "accept": "application/json, text/plain, */*",
        "application-name": "app-evolution",
        "app-version": APP_VERSION,
        "platform": "iOS",
        "mobile-platform": "iOS",
        "device-type": DEVICE_TYPE,
        "device": DEVICE_TYPE,
        "os-version": OS_VERSION,
        "user-agent": USER_AGENT,
        "device-mac-address": device_id,
        "device-latitude": str(lat),
        "device-longitude": str(lon),
    }
    return httpx.Client(timeout=30, headers=headers)


def _login(c: httpx.Client, user: str, password: str, totp_secret: str, location: tuple[float, float]) -> str:
    """Run GBM's mobile PKCE + SOFTWARE_TOKEN_MFA login and return a fresh access token.

    The MFA code is generated from `totp_secret` against GBM's **server** time (the `timestamp` the
    challenge step returns), not the local clock, so a skewed host doesn't cause a code mismatch.
    """
    lat, lon = location
    verifier = _b64url(os.urandom(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())

    r = c.post(f"{AUTH}/session/user", json={
        "responseType": "code", "codeChallenge": challenge, "codeChallengeMethod": "SHA256",
        "longitude": lon, "latitude": lat, "clientId": CLIENT_ID, "user": user, "password": password})
    if r.status_code >= 400:
        raise SessionExpired(
            "gbm: login rejected; device_id may no longer be the account's authorized device "
            "(the phone app takes the single-device slot when you sign in there) — re-run setup"
        )
    body = r.json()
    if body.get("authorizationCode") and "challengeInfo" not in body:
        authorization_code = body["authorizationCode"]  # GBM issued the code without an MFA challenge
    else:
        info = body["challengeInfo"]
        server_ms = info.get("timestamp")
        code = _totp(totp_secret, when=server_ms / 1000 if server_ms else None)
        r = c.post(f"{AUTH}/session/user/challenge", json={
            "codeChallengeMethod": "SHA256", "code": code, "clientId": CLIENT_ID, "responseType": "code",
            "session": info["session"], "codeChallenge": challenge, "codeVerifier": verifier, "user": user,
            "challengeType": "SOFTWARE_TOKEN_MFA"})
        if r.status_code >= 400:
            raise AuthRequired(f"gbm: MFA code rejected: {r.text[:120]}")
        authorization_code = r.json()["authorizationCode"]

    r = c.post(f"{AUTH}/session/token", json={
        "clientId": CLIENT_ID, "code": authorization_code, "codeVerifier": verifier})
    r.raise_for_status()
    token = r.json().get("accessToken")
    if not token:
        raise AuthRequired("gbm: login did not return an access token")
    return token


class Gbm(Provider):
    id = "gbm"
    name = "GBM"
    auth_fields = ("username", "password", "totp_secret", "device_id")

    def __init__(
        self,
        *,
        accounts: dict[str, str] | None = None,
        contract_id: str | None = None,
        location: tuple[float, float] = DEFAULT_LOCATION,
        client: httpx.Client | None = None,
        **auth,
    ) -> None:
        super().__init__(**auth)
        self.accounts = dict(accounts) if accounts else None
        self.contract_id = contract_id
        self._location = location
        self._client = _client(client, location, self.auth.get("device_id", ""))

    def fetch_balances(self) -> list[Balance]:
        token = self._mint()
        accounts = self.accounts if self.accounts is not None else self._list_accounts(token)
        return [
            Balance(self.id, name, AccountKind.INVESTMENT, self._value(token, legacy), "MXN")
            for name, legacy in accounts.items()
        ]

    # --- internals ---------------------------------------------------------------------------

    def _mint(self) -> str:
        """Log in headlessly (MFA code generated from totp_secret), returning a fresh access token."""
        return _login(self._client, self.auth["username"], self.auth["password"],
                      self.auth["totp_secret"], self._location)

    def _discover_contracts(self, token: str) -> list[str]:
        r = self._client.get(CONTRACTS_URL, headers={"Authorization": "Bearer " + token})
        r.raise_for_status()
        data = r.json()
        rows = data if isinstance(data, list) else (data.get("data") or data.get("contracts") or [])
        ids = []
        for row in rows:
            if isinstance(row, str):
                ids.append(row)
                continue
            cid = row.get("id") or row.get("contract_id") or row.get("contractId") or (row.get("contract") or {}).get("id")
            if cid:
                ids.append(cid)
        if not ids:
            raise AuthRequired("gbm: could not discover any contract; pass contract_id explicitly")
        return ids

    def _accounts_for(self, token: str, contract_id: str) -> dict[str, str]:
        r = self._client.get(
            f"{APPGBM}/dashboard/contracts/{contract_id}/accounts",
            headers={"Authorization": "Bearer " + token},
        )
        r.raise_for_status()
        data = r.json()
        rows = data if isinstance(data, list) else data.get("data", [])
        return {
            row["name"]: row["legacy_contract_id"]
            for row in rows
            if row.get("status") == "active"
            and row.get("legacy_contract_id")
            and not str(row.get("name", "")).lower().startswith("deprecated")
        }

    def _list_accounts(self, token: str) -> dict[str, str]:
        """Active strategies across every contract; same-named ones are suffixed with a short contract id."""
        contracts = [self.contract_id] if self.contract_id else self._discover_contracts(token)
        merged: dict[str, str] = {}
        for contract_id in contracts:
            for name, legacy in self._accounts_for(token, contract_id).items():
                label = name if name not in merged else f"{name} ({contract_id[:4]})"
                merged[label] = legacy
        return merged

    def _value(self, token: str, legacy_contract_id: str) -> Decimal:
        r = self._client.post(
            f"{HOMEBROKER}/Operation/GetContractBuyingPower",
            headers={"Authorization": "Bearer " + token},
            json={"request": legacy_contract_id},
        )
        r.raise_for_status()
        value = r.json().get("marketValueTotal")
        if value is None:
            raise LayoutChanged(f"gbm: no marketValueTotal for {legacy_contract_id}; GBM's response changed")
        try:
            return Decimal(str(value)).quantize(CENT)
        except InvalidOperation:
            raise LayoutChanged(f"gbm: unexpected marketValueTotal {value!r} for {legacy_contract_id}") from None
