"""GBM+ (GBM México broker), through its own API.

Read-only: refreshes a session and reads account values. No order or transfer code.

Everything routes through the single `Gbm` provider:
  - `Gbm(...).fetch_balances()` silently refreshes the session and returns a `Balance` per strategy;
    it raises `SessionExpired` when the refresh token has lapsed.
  - `Gbm(...).renew(password, code)` does the one-time MFA login and stores a new refresh token.
  - The session comes from `refresh_token=...` (stateless) or a `token_file=...` the provider loads and
    rewrites (owner-only). Accounts are discovered from the contract unless `contract_id`/`accounts` is given.

GBM's login requires a device location; pass your own via `location=(lat, lon)` (defaults to Mexico City).

Nothing account-specific is hardcoded (`CLIENT_ID` is GBM's public web client). Not affiliated with GBM;
automated access may be against GBM's terms of use, and the refresh token is trade-capable — keep it
(and any token file) off shared/exposed hosts.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from decimal import Decimal
from pathlib import Path

import httpx

from ..core import AccountKind, AuthRequired, Balance, Provider, SessionExpired

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
    auth_fields = ()  # the session comes from a refresh_token or a token_file

    def __init__(
        self,
        *,
        refresh_token: str | None = None,
        token_file: str | Path | None = None,
        user: str | None = None,
        accounts: dict[str, str] | None = None,
        contract_id: str | None = None,
        location: tuple[float, float] = DEFAULT_LOCATION,
        client: httpx.Client | None = None,
        **auth,
    ) -> None:
        super().__init__(**auth)
        self.user = user
        self.accounts = dict(accounts) if accounts else None
        self.contract_id = contract_id
        self.location = location
        self.token_file = Path(token_file) if token_file else None
        self._refresh_token = refresh_token or self._load_refresh()
        self._client = _client(client, location)

    # --- balances ----------------------------------------------------------------------------

    def fetch_balances(self) -> list[Balance]:
        if not self._refresh_token:
            raise SessionExpired("gbm: no refresh token; call renew(password, code) to log in")
        token = self._refresh(self._refresh_token)
        accounts = self.accounts if self.accounts is not None else self._list_accounts(token)
        return [
            Balance(self.id, name, AccountKind.INVESTMENT, self._value(token, legacy), "MXN")
            for name, legacy in accounts.items()
        ]

    def list_accounts(self) -> dict[str, str]:
        """Every active strategy across the login's contracts, as {name: legacy_contract_id}."""
        if not self._refresh_token:
            raise SessionExpired("gbm: no refresh token; call renew(password, code) to log in")
        return self._list_accounts(self._refresh(self._refresh_token))

    # --- session -----------------------------------------------------------------------------

    def renew(self, password: str, code: str) -> str:
        """One-time MFA login (needs `user`). Stores the new refresh token (and to token_file). Returns it."""
        if not self.user:
            raise AuthRequired("gbm: renew() needs the account 'user' (email)")
        refresh_token = self.login(self.user, password, code, client=self._client)
        self._refresh_token = refresh_token
        if self.token_file is not None:
            self.token_file.write_text(json.dumps({"refreshToken": refresh_token}), encoding="utf-8")
            os.chmod(self.token_file, 0o600)  # owner-only: it is trade-capable
        return refresh_token

    def _load_refresh(self) -> str | None:
        if self.token_file is None:
            return None
        try:
            return json.loads(self.token_file.read_text(encoding="utf-8")).get("refreshToken")
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _refresh(self, refresh_token: str) -> str:
        """Exchange the refresh token for a fresh access token (GBM's own refresh endpoint; no MFA)."""
        r = self._client.post(f"{AUTH}/session/user/refresh", json={"clientId": CLIENT_ID, "refreshToken": refresh_token})
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code >= 400 or body.get("id") != "Success" or not body.get("accessToken"):
            raise SessionExpired("gbm: refresh token expired or invalid; call renew(password, code)")
        return body["accessToken"]

    # --- discovery / reads -------------------------------------------------------------------

    def _discover_contracts(self, token: str) -> list[str]:
        """Every contract id on this login, so the caller needn't know them."""
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
        """Active strategies under one contract (skips closed 'Deprecated' ones)."""
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
        """Total market value (positions + cash) of one account, in MXN."""
        r = self._client.post(
            f"{HOMEBROKER}/Operation/GetContractBuyingPower",
            headers={"Authorization": "Bearer " + token},
            json={"request": legacy_contract_id},
        )
        r.raise_for_status()
        return Decimal(str(r.json().get("marketValueTotal", 0))).quantize(CENT)

    @classmethod
    def login(
        cls,
        user: str,
        password: str,
        code: str,
        *,
        client: httpx.Client | None = None,
        location: tuple[float, float] = DEFAULT_LOCATION,
    ) -> str:
        """One-time interactive login (password + 6-digit TOTP code). Returns a refresh token."""
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
