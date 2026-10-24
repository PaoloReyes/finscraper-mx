"""Openbank México, through the Openbank MX app API (``mx.openbank.modelbank``).

Read-only. Authenticates against the same Grupo Santander "modelbank" backend the
Openbank MX app uses (`api.openbank.mx`), with the app's own device identity.

Auth fields
-----------
username
    Login identifier — the email you sign in with (see ``document_type`` for others).
password
    Online-banking password.
device_uuid
    The app's registered device UUID. Openbank binds a trusted device at onboarding;
    the same UUID is sent both as ``deviceInformation.deviceUUID`` in the body and as
    ``platforminformation.uuid`` in the header. Captured once from the Android app (see
    ``docs/credentials.md``).
proxy (optional)
    Openbank's AWS load balancer rejects datacenter IPs (a cloud server gets a ``403`` on
    login). A proxy URL (e.g. ``http://warp:1080``) routes this provider's calls, and only
    these, through a residential or WARP connection.

How it works
------------
``fetch_balances()`` logs in (``POST /mb-api-auth/authentication/login``; the token travels in a
custom ``accesstoken:`` header, not ``Authorization: Bearer``), reads the global position
(``GET /mb-api-gp-exp/v7/summary`` then ``GET /mb-api-gp-exp/v6/categories/{id}`` per category),
and logs out on the way out. See ``docs/flows/openbank.md``.

Not affiliated with Openbank / Grupo Santander. Automated access may be against Openbank's
terms of use; use only with your own account.
"""
from __future__ import annotations

import json
import uuid
from decimal import Decimal, InvalidOperation

import httpx

from ..core import AccountKind, AuthRequired, Balance, LayoutChanged, Provider, SessionExpired

BASE = "https://api.openbank.mx"
CAPTCHA_FLAG_URL = f"{BASE}/mb-api-auth/authentication/captcha-flag"
PASSWORD_POSITIONS_URL = f"{BASE}/mb-api-auth/authentication/users/password-positions"
LOGIN_URL = f"{BASE}/mb-api-auth/authentication/login"
LOGOUT_URL = f"{BASE}/mb-api-auth/authentication/logout"

# Global-position / balance endpoints (verified against a decrypted capture).
SUMMARY_URL = f"{BASE}/mb-api-gp-exp/v7/summary"  # ?screenName=GP_SUMMARY -> {summaries, categories[]}
CATEGORY_URL = f"{BASE}/mb-api-gp-exp/v6/categories/{{category_id}}"  # per-category product list

# subProductCode -> AccountKind, for the accounts[] list only. Cards are not emitted:
# debit mirrors its linked checking account, and credit is intentionally excluded.
_KIND = {
    "CHECKING_ACCOUNT": AccountKind.CHECKING,
    "SAVINGS_ACCOUNT": AccountKind.SAVINGS,
    "INVESTMENT_ACCOUNT": AccountKind.INVESTMENT,
}
CENT = Decimal("0.01")

# Fixed, non-secret app identifiers (same for every Openbank MX install)
BANK_ID = "OK_MX"
APP_INFO = "Openbank-Android 1.19.0"
_UA = "okhttp/5.1.0"

# Default device descriptor — override to match your registered device so the login
# does not trigger a new-device step-up. None of these are secrets.
DEFAULT_MODEL = "HONOR RMO-NX3"
DEFAULT_APP_VERSION = "1.19.1"
DEFAULT_PRODUCT_VERSION = "9.4.12"
DEFAULT_OS_VERSION = "13"
DEFAULT_LOCATION = (19.4326, -99.1332)  # Mexico City


class Openbank(Provider):
    """Reads balances from Openbank México via the Openbank MX app API."""

    id = "openbank"
    name = "Openbank México"
    auth_fields = ("username", "password", "device_uuid")

    def __init__(
        self,
        *,
        document_type: str = "email",
        model: str = DEFAULT_MODEL,
        app_version: str = DEFAULT_APP_VERSION,
        product_version: str = DEFAULT_PRODUCT_VERSION,
        os_version: str = DEFAULT_OS_VERSION,
        location: tuple[float, float] = DEFAULT_LOCATION,
        client: httpx.Client | None = None,
        **auth: str,
    ) -> None:
        super().__init__(**auth)
        self._document_type = document_type
        self._model = model
        self._app_version = app_version
        self._product_version = product_version
        self._os_version = os_version
        self._location = location
        self._client = client or httpx.Client(timeout=30, proxy=self.auth.get("proxy") or None)

    def fetch_balances(self) -> list[Balance]:
        token = self._login()
        access_token = token["accessToken"]
        try:
            return self._read_balances(access_token)
        finally:
            self._logout(access_token)  # Openbank allows one session; release it

    # ------------------------------------------------------------------
    # Balances (private)
    # ------------------------------------------------------------------

    def _read_balances(self, access_token: str) -> list[Balance]:
        """Global position: summary lists the categories, each category lists its products."""
        summary = self._get_json(SUMMARY_URL, access_token, params={"screenName": "GP_SUMMARY"})
        balances: list[Balance] = []
        for cat in summary.get("categories", []):
            cat_id = cat.get("id")
            if not cat_id:
                continue
            body = self._get_json(CATEGORY_URL.format(category_id=cat_id), access_token)
            subproducts = body.get("subProducts", {})
            # Only deposit/investment accounts. Cards (credit + debit) are excluded:
            # debit mirrors its linked checking account, and credit is not wanted.
            for product in subproducts.get("accounts", []):
                balances.append(self._account_balance(product))
        return balances

    def _account_balance(self, product: dict) -> Balance | None:
        """An accounts[] entry (checking / savings / investment)."""
        kind = _KIND.get(product.get("subProductCode"), AccountKind.OTHER)
        detail = product.get("detailSummary") or {}
        amount = self._money(detail.get("availableBalance", 0))
        return Balance(
            institution=self.id,
            account=product.get("alias") or product.get("subProductCode", "account"),
            kind=kind,
            amount=amount,
            currency=detail.get("currency", "MXN"),
        )

    @staticmethod
    def _money(raw: object) -> Decimal:
        try:
            return Decimal(str(raw)).quantize(CENT)
        except (InvalidOperation, ValueError):
            raise LayoutChanged(f"openbank: cannot parse amount {raw!r}")

    def _get_json(self, url: str, access_token: str, params: dict | None = None) -> dict:
        resp = self._client.get(url, headers=self._auth_headers(access_token), params=params)
        if resp.status_code in (401, 403):
            raise SessionExpired(f"openbank: {url} rejected ({resp.status_code})")
        if resp.status_code >= 400:
            raise LayoutChanged(f"openbank: {url} -> HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError:
            raise LayoutChanged(f"openbank: {url} response was not JSON")

    # ------------------------------------------------------------------
    # Auth (private)
    # ------------------------------------------------------------------

    def _platform_information(self) -> str:
        return json.dumps(
            {
                "type": "android",
                "productVersion": self._product_version,
                "appVersion": self._app_version,
                "platformVersion": self._os_version,
                "model": self._model,
                "uuid": self.auth["device_uuid"],
            },
            separators=(",", ":"),
        )

    def _base_headers(self) -> dict[str, str]:
        lat, lon = self._location
        return {
            "hybrid": "false",
            "latitude": str(lat),
            "longitude": str(lon),
            "bankid": BANK_ID,
            "app-info": APP_INFO,
            "accept-language": "es-MX",
            "applicationlocale": "es-MX",
            "devicelocale": "es-MX",
            "devicetimezone": "-06:00",
            "platforminformation": self._platform_information(),
            "accept-encoding": "gzip",
            "user-agent": _UA,
        }

    def _login(self) -> dict[str, str]:
        """Run the app's pre-login handshake, then log in, returning the token payload.

        The login is validated against session state the server establishes during the
        pre-login calls (an ``AWSALBTG`` load-balancer cookie + a server-side ``nonce``
        from ``password-positions``), so all three calls must share one client/cookie jar.
        A bare login without the handshake is rejected with HTTP 401.

        Returns the raw ``{accessToken, refreshToken, sessionExpiresAt, expiresIn}`` dict.
        """
        base = self._base_headers()
        json_ct = {"content-type": "application/json; charset=UTF-8"}

        # 1. captcha-flag — seeds the AWSALBTG session cookie (stored on self._client)
        self._client.get(CAPTCHA_FLAG_URL, headers=base)

        # 2. password-positions — issues {positions, nonce} and binds the auth session
        positions = ["*"]
        pp = self._client.post(
            PASSWORD_POSITIONS_URL,
            headers={**base, **json_ct},
            content=json.dumps(
                {"documentType": self._document_type, "username": self.auth["username"]}
            ),
        )
        if pp.status_code < 400:
            try:
                positions = pp.json().get("positions") or ["*"]
            except ValueError:
                pass

        # 3. login — same client carries the handshake cookies
        headers = {**base, "captcha": "", **json_ct}
        body = {
            "username": self.auth["username"],
            "password": self.auth["password"],
            "documentType": self._document_type,
            "passwordPosition": positions,  # typically ["*"] = full password
            "deviceInformation": {
                "deviceUUID": self.auth["device_uuid"],
                "mobileDeviceInfo": {
                    "device": self._model,
                    "osVersion": self._os_version,
                    "platform": "ANDROID",
                    "rooted": False,
                    "version": self._app_version,
                    "pushEnabled": True,
                    "biometryUpdated": True,
                },
            },
            "customerSessionId": str(uuid.uuid4()),
        }
        resp = self._client.post(LOGIN_URL, headers=headers, content=json.dumps(body))
        if resp.status_code in (401, 403):
            raise SessionExpired(
                f"openbank: login rejected ({resp.status_code}, {resp.headers.get('content-type') or 'no content-type'})"
                " — check credentials/device, or set `proxy` if this runs on a datacenter IP"
            )
        if resp.status_code >= 400:
            raise SessionExpired(f"openbank: login failed with HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            raise LayoutChanged("openbank: login response was not JSON")
        if not data.get("accessToken"):
            raise AuthRequired("openbank: login returned no accessToken (step-up/MFA may be required on this device)")
        return data

    def _auth_headers(self, access_token: str) -> dict[str, str]:
        """Headers for a protected call: the custom ``accesstoken`` header, not Bearer."""
        headers = self._base_headers()
        headers.update(
            {
                "accesstoken": access_token,
                "accept": "application/json; charset=utf-8",
                "content-type": "application/json; charset=utf-8",
                "interceptor": "AuthorizationInterceptor",
            }
        )
        return headers

    def _logout(self, access_token: str) -> int | None:
        """Release the server session (best-effort). Returns the HTTP status, or None on error.

        Openbank enforces a single active session: a login left open makes the user's own
        device report "sesión activa en otro dispositivo" (the app's ``AlreadyLogged`` error)
        until the token expires (~15 min). Always call this after a login/read so the real
        device is not blocked. The body is a ``SessionLogoutRequest`` — ``{"accessToken": …}``.
        """
        try:
            resp = self._client.post(
                LOGOUT_URL,
                headers=self._auth_headers(access_token),
                content=json.dumps({"accessToken": access_token}),
            )
            return resp.status_code
        except httpx.HTTPError:
            return None  # best-effort; the session also expires with the token TTL
