"""Skandia México — corporate savings plan (Fondo de Ahorro), through the Skandia mobile app API.

Read-only. Authenticates with a simple JWT login against the same backend the
SKANDIA NET MX iOS app uses, then reads product balances from the Employees endpoint.

Auth fields
-----------
username
    CURP (18 characters), e.g. ``"XEXX010101HNEXXXA4"`` (a fake placeholder).
password
    6-digit numeric PIN.

The library re-logs in on every ``fetch_balances()`` call. The JWT expires in 20 minutes
but since we never cache it there is no session to manage.

Not affiliated with Skandia México. Covers only the corporate Fondo de Ahorro portal.
Automated access may be against Skandia's terms of use; use only with your own account.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

import httpx

from ..core import AccountKind, AuthRequired, Balance, LayoutChanged, Provider, SessionExpired

BASE_URL = "https://mobile.skandia.com.mx/api/CoporateApp"
LOGIN_URL = f"{BASE_URL}/Login/login"
EMPLOYEES_URL = f"{BASE_URL}/Employees/Employees"

CENT = Decimal("0.01")
_UA = "MobileApp/4.0.1 (com.oldmutualmex.appiphone; build:5; iOS 27.0.0) Alamofire/4.0.1"


def _parse_amount(raw: float | str, label: str = "") -> Decimal:
    try:
        return Decimal(str(raw)).quantize(CENT)
    except InvalidOperation:
        raise LayoutChanged(
            f"skandia: cannot parse amount {raw!r}{' for ' + label if label else ''}"
        )


class Skandia(Provider):
    """Reads Fondo de Ahorro balances from Skandia México via the mobile app API."""

    id = "skandia"
    name = "Skandia México"
    auth_fields = ("username", "password")

    def __init__(self, *, client: httpx.Client | None = None, **auth: str) -> None:
        super().__init__(**auth)
        self._client = client or httpx.Client(
            headers={"User-Agent": _UA, "Accept": "*/*"},
            timeout=30,
        )

    def fetch_balances(self) -> list[Balance]:
        token, plan = self._login()
        return self._load_balances(token, plan)

    # ------------------------------------------------------------------
    # Auth (private)
    # ------------------------------------------------------------------

    def _login(self) -> tuple[str, str]:
        """POST login endpoint → (jwt_token, plan_id)."""
        resp = self._client.post(
            LOGIN_URL,
            params={"user": self.auth["username"], "password": self.auth["password"]},
        )
        if resp.status_code == 401:
            raise AuthRequired("skandia: invalid username or password")
        if resp.status_code >= 400:
            raise SessionExpired(f"skandia: login returned {resp.status_code}")
        body = resp.json()
        token = body.get("Token")
        plan = body.get("Plan")
        if not token:
            msg = body.get("Message") or "missing Token"
            raise AuthRequired(f"skandia: login failed — {msg}")
        if not plan:
            raise LayoutChanged("skandia: login response missing Plan")
        return token, plan

    # ------------------------------------------------------------------
    # Balance read (private)
    # ------------------------------------------------------------------

    def _load_balances(self, token: str, plan: str) -> list[Balance]:
        resp = self._client.post(
            EMPLOYEES_URL,
            headers={
                "Authorization": f"Basic {token}",
                "corpplan": plan,
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            },
        )
        if resp.status_code in (401, 403):
            raise SessionExpired("skandia: token rejected by Employees endpoint")
        resp.raise_for_status()
        body = resp.json()

        products = body.get("Products")
        if not isinstance(products, list):
            raise LayoutChanged("skandia: 'Products' key missing or not a list")
        if not products:
            raise LayoutChanged("skandia: 'Products' is empty")

        balances: list[Balance] = []
        for product in products:
            name = str(product.get("ProductName", "")).strip() or product.get("ProductType", "")
            raw = product.get("TotalAmmount", 0)
            balances.append(
                Balance(
                    institution=self.id,
                    account=name,
                    kind=AccountKind.INVESTMENT,
                    amount=_parse_amount(raw, name),
                    currency="MXN",
                )
            )
        return balances
