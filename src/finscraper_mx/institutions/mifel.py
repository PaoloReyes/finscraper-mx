"""Mifel (Banco Mifel), through the Mifel Móvil app API.

Read-only. Authenticates via a Keycloak password-grant against the same backend the
Mifel Móvil app uses, then reads linked account balances from the integracion API.

Auth fields
-----------
username
    Mifel online-banking username (e.g. ``"mi_usuario"``).
password
    Online-banking password.

The library logs in with ``grant_type=password`` on every ``fetch_balances()`` call.
It uses ``curl-cffi`` to impersonate iOS Safari's TLS fingerprint, which is required
to pass Mifel's Akamai WAF on ``sso.mifel.net``.

Not affiliated with Banco Mifel. Automated access may be against Mifel's terms of use;
use only with your own account.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from decimal import Decimal, InvalidOperation

import httpx
from curl_cffi import requests as crequests

from ..core import AccountKind, AuthRequired, Balance, LayoutChanged, Provider, SessionExpired

# Keycloak SSO
SSO_TOKEN_URL = (
    "https://sso.mifel.net/auth/realms/banca-electronica-pf"
    "/protocol/openid-connect/token"
)
SESSION_VALIDATION_URL = (
    "https://sso.mifel.net/auth/realms/banca-electronica-pf"
    "/custom-session-provider/mobile-session-validation"
)

# Integration API
INTEGRACION = "https://integracion.mifel.com.mx"
CUENTAS_URL = f"{INTEGRACION}/consultas/cuentas-vinculadas-v2"
DETALLE_URL = f"{INTEGRACION}/consultas/detalle-cuenta-vinculada/{{num_cta}}"

# Public app credentials (same for every user — Mifel Móvil app)
_CLIENT_ID = "app-shell-pf"
_CLIENT_SECRET = "ORFkINAyWfTRs29UPuUrmNfwanL2ZaCU"  # noqa: S105

# New Relic account/app IDs embedded in the Mifel Móvil app (static, not user-specific)
_NR_ACCOUNT = "3371985"
_NR_APP = "1133996306"
_NR_TRUSTED_ACCOUNT = "2530960"

DEFAULT_LOCATION = (19.4326, -99.1332)  # Mexico City
CENT = Decimal("0.01")
_UA = "MifelAppShell/31 CFNetwork/3896.100.1.2.1 Darwin/27.0.0"


def _fingerprint(username: str) -> str:
    return hashlib.sha256(f"finscraper-mx:{username}".encode()).hexdigest().upper()


def _newrelic_header() -> str:
    """Generate a New Relic distributed-tracing header matching the Mifel app format."""
    trace_id = os.urandom(16).hex()
    span_id = os.urandom(8).hex()
    payload = {
        "v": [0, 2],
        "d": {
            "ty": "Mobile",
            "ac": _NR_ACCOUNT,
            "ap": _NR_APP,
            "tr": trace_id,
            "id": span_id,
            "tk": _NR_TRUSTED_ACCOUNT,
            "ti": int(time.time() * 1000),
        },
    }
    return base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def _traceparent() -> str:
    """Generate a W3C traceparent header."""
    trace_id = os.urandom(16).hex()
    span_id = os.urandom(8).hex()
    return f"00-{trace_id}-{span_id}-01"


def _parse_amount(raw: str | int | float, label: str = "") -> Decimal:
    try:
        return Decimal(str(raw)).quantize(CENT)
    except InvalidOperation:
        raise LayoutChanged(
            f"mifel: cannot parse amount {raw!r}{' for ' + label if label else ''}"
        )


class Mifel(Provider):
    """Reads account balances from Banco Mifel via the Mifel Móvil app API."""

    id = "mifel"
    name = "Banco Mifel"
    auth_fields = ("username", "password")

    def __init__(
        self,
        *,
        location: tuple[float, float] = DEFAULT_LOCATION,
        client: httpx.Client | None = None,
        _curl_session: crequests.Session | None = None,
        **auth: str,
    ) -> None:
        super().__init__(**auth)
        self._location = location
        # httpx client for integracion API (no WAF, standard TLS fine)
        self._client = client or httpx.Client(
            headers={"User-Agent": _UA, "Accept": "application/json"},
            timeout=30,
        )
        # curl-cffi session for SSO (needs iOS TLS fingerprint to pass Akamai WAF)
        self._curl = _curl_session or crequests.Session(impersonate="safari18_0_ios")

    def fetch_balances(self) -> list[Balance]:
        access_token = self._login()
        return self._read_balances(access_token)

    # ------------------------------------------------------------------
    # Auth (private)
    # ------------------------------------------------------------------

    def _login(self) -> str:
        """Keycloak password grant via iOS-impersonated TLS → access token."""
        lat, lon = self._location
        data = {
            "client_id": _CLIENT_ID,
            "client_secret": _CLIENT_SECRET,
            "grant_type": "password",
            "username": self.auth["username"],
            "password": self.auth["password"],
            "scope": "openid",
            "fingerprint": _fingerprint(self.auth["username"]),
            "hasInstance": "true",
            "latitude": str(lat),
            "longitude": str(lon),
            "language": "es",
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": _UA,
            "Accept": "application/json",
            "newrelic": _newrelic_header(),
            "traceparent": _traceparent(),
        }
        resp = self._curl.post(SSO_TOKEN_URL, data=data, headers=headers, timeout=30)

        if resp.status_code == 401:
            raise AuthRequired("mifel: invalid username or password")
        if resp.status_code >= 400:
            raise SessionExpired(
                f"mifel: token endpoint returned {resp.status_code}"
            )
        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise LayoutChanged(
                f"mifel: token response missing access_token: {list(body)}"
            )

        # Notify server this is a mobile session (best-effort)
        try:
            self._curl.put(
                SESSION_VALIDATION_URL,
                headers={"Authorization": f"Bearer {token}", "User-Agent": _UA},
                timeout=10,
            )
        except Exception:
            pass

        return token

    # ------------------------------------------------------------------
    # Balance reads (private)
    # ------------------------------------------------------------------

    def _read_balances(self, token: str) -> list[Balance]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Origin": "https://www.mifel.net",
        }
        accounts = self._cuentas_vinculadas(headers)
        balances: list[Balance] = []
        for acct in accounts:
            label = (
                acct.get("descripcionSchemeCode")
                or acct.get("schemeCode")
                or acct.get("numCta", "")
            )
            moneda = acct.get("moneda", "MXN")

            # Liquid/available balance — what can be spent or transferred right now
            disponible = _parse_amount(acct.get("montoDisponible", "0"), label)
            balances.append(
                Balance(
                    institution=self.id,
                    account=str(label).strip(),
                    kind=AccountKind.CHECKING,
                    amount=disponible,
                    currency=moneda,
                )
            )

            # Savings bucket (caja digital) — the invested/earning portion
            caja_raw = acct.get("montoRetenidoCaja", "0")
            if Decimal(str(caja_raw)).quantize(CENT) > 0:
                # detalle gives the authoritative saldoCajas value
                detail = self._detalle_cuenta(acct.get("numCta", ""), headers)
                if detail and detail.get("saldoCajas") is not None:
                    caja_raw = detail["saldoCajas"]
                balances.append(
                    Balance(
                        institution=self.id,
                        account=f"{str(label).strip()} (Caja)",
                        kind=AccountKind.SAVINGS,
                        amount=_parse_amount(caja_raw, f"{label} caja"),
                        currency=moneda,
                    )
                )

        return balances

    def _cuentas_vinculadas(self, headers: dict) -> list[dict]:
        resp = self._client.post(
            CUENTAS_URL,
            json={"tipoCta": "CAA", "limpiaCache": True},
            headers=headers,
        )
        if resp.status_code in (401, 403):
            raise SessionExpired("mifel: token rejected by integracion API")
        resp.raise_for_status()
        body = resp.json()
        if body.get("codigo") != "0":
            raise LayoutChanged(
                f"mifel: cuentas-vinculadas error: {body.get('descripcion')}"
            )
        data = body.get("data", [])
        if not isinstance(data, list):
            raise LayoutChanged("mifel: cuentas-vinculadas 'data' is not a list")
        return data

    def _detalle_cuenta(self, num_cta: str, headers: dict) -> dict | None:
        try:
            resp = self._client.get(
                DETALLE_URL.format(num_cta=num_cta),
                params={"limpiaCache": "false"},
                headers=headers,
            )
            if resp.status_code >= 400:
                return None
            rows = resp.json().get("data", [])
            return rows[0] if rows else None
        except Exception:
            return None
