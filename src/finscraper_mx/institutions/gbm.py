"""GBM+ (GBM México broker), through its own API.

Read-only: refreshes a session and reads account values. No order or transfer code.

Standard provider: build it with a `refresh_token` and call `fetch_balances()`, like any other provider.
It silently refreshes the session, discovers the login's contracts and active strategies (or uses the
`contract_id`/`accounts` you pass), and raises `SessionExpired` when the refresh token has lapsed.

Obtaining the refresh token is a one-time setup, done with the `Gbm.login(user, password, code)` helper
(GBM requires an MFA code once). GBM's login also needs a device location — pass `location=(lat, lon)`
(defaults to Mexico City).

`CLIENT_ID` is GBM's public web client (same for every user). Not affiliated with GBM; automated access
may be against GBM's terms of use, and the refresh token is trade-capable — keep it off shared hosts.
"""
from __future__ import annotations

import base64
import hashlib
import os
import time
from decimal import Decimal, InvalidOperation

import httpx

from ..core import AccountKind, AuthRequired, Balance, LayoutChanged, Provider, SessionExpired

CLIENT_ID = "7c464570619a417080b300076e163289"  # GBM's public web client id (same for every user)
AUTH = "https://auth.gbm.com/api/v1"
APPGBM = "https://api.appgbm.com/v1"
CONTRACTS_URL = "https://api.appgbm.com/v2/onboarding/contracts"  # lists the login's contracts
HOMEBROKER = "https://homebroker-api.gbm.com/GBMP/api"
DEFAULT_LOCATION = (19.4326, -99.1332)  # Mexico City; override per customer with location=(lat, lon)
CENT = Decimal("0.01")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _client(client: httpx.Client | None, location: tuple[float, float]) -> httpx.Client:
    if client is not None:
        return client
    lat, lon = location
    headers = {"Content-Type": "application/json", "device-latitude": str(lat), "device-longitude": str(lon)}
    return httpx.Client(timeout=30, headers=headers)


class Gbm(Provider):
    id = "gbm"
    name = "GBM+"
    auth_fields = ("refresh_token",)

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
        self._client = _client(client, location)

    def fetch_balances(self) -> list[Balance]:
        token = self._refresh(self.auth["refresh_token"])
        accounts = self.accounts if self.accounts is not None else self._list_accounts(token)
        return [
            Balance(self.id, name, AccountKind.INVESTMENT, self._value(token, legacy), "MXN")
            for name, legacy in accounts.items()
        ]

    # --- internals ---------------------------------------------------------------------------

    def _refresh(self, refresh_token: str) -> str:
        """Exchange the refresh token for a fresh access token (GBM's own refresh endpoint; no MFA)."""
        r = self._client.post(f"{AUTH}/session/user/refresh", json={"clientId": CLIENT_ID, "refreshToken": refresh_token})
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code >= 400 or body.get("id") != "Success" or not body.get("accessToken"):
            raise SessionExpired("gbm: refresh token expired or invalid; get a new one with Gbm.login()")
        return body["accessToken"]

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

    @classmethod
    def login(
        cls,
        user: str,
        password: str,
        code: str,
        *,
        location: tuple[float, float] = DEFAULT_LOCATION,
        client: httpx.Client | None = None,
    ) -> str:
        """One-time setup: interactive login (password + 6-digit TOTP code). Returns a refresh token."""
        c = _client(client, location)
        verifier = _b64url(os.urandom(32))
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())

        r = c.post(f"{AUTH}/session/user", json={"clientid": CLIENT_ID, "user": user, "password": password})
        r.raise_for_status()
        session = r.json()["challengeInfo"]["session"]

        r = c.post(
            f"{AUTH}/session/user/challenge",
            json={
                "applicationName": "GBM+",
                "challengeType": "SOFTWARE_TOKEN_MFA",
                "clientId": CLIENT_ID,
                "code": code,
                "codeChallenge": challenge,
                "codeChallengeMethod": "SHA256",
                "destination": "iPhone",
                "responseType": "code",
                "session": session,
                "timestamp": int(time.time() * 1000),
                "user": user,
            },
        )
        if r.status_code >= 400:
            raise AuthRequired(f"gbm: MFA code rejected: {r.text[:120]}")
        authorization_code = r.json()["authorizationCode"]

        r = c.post(f"{AUTH}/session/token", json={"clientId": CLIENT_ID, "code": authorization_code, "codeVerifier": verifier})
        r.raise_for_status()
        token = r.json()
        if "refreshToken" not in token:
            raise AuthRequired("gbm: login did not return a refresh token")
        return token["refreshToken"]
