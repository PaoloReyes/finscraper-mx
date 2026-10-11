"""Mercado Pago México, through its official API (https://api.mercadopago.com).

Reads the account money ("dinero en cuenta") with an access token (`APP_USR-…`) from an application the
user creates in Mercado Pago Developers: `/users/me` identifies the account, then
`/users/{id}/mercadopago_account/balance` returns its total (available + not yet released) in MXN.
Cajitas / reservas are not part of that balance.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

import httpx

from ..core import AccountKind, Balance, FinscraperError, LayoutChanged, Provider, SessionExpired

BASE_URL = "https://api.mercadopago.com"
CENT = Decimal("0.01")


class MercadoPago(Provider):
    id = "mercado_pago"
    name = "Mercado Pago"
    auth_fields = ("access_token",)

    def __init__(self, *, client: httpx.Client | None = None, base_url: str = BASE_URL, **auth: str) -> None:
        super().__init__(**auth)
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=20)

    def fetch_balances(self) -> list[Balance]:
        user_id = self._get("/users/me").get("id")
        if not user_id:
            raise LayoutChanged("mercado_pago: /users/me returned no id; Mercado Pago's response changed")
        body = self._get(f"/users/{user_id}/mercadopago_account/balance")
        currency = body.get("currency_id")
        if currency != "MXN":
            raise FinscraperError(f"mercado_pago: account currency is {currency!r}, expected MXN (México only)")
        try:
            total = Decimal(str(body["total_amount"])).quantize(CENT)
        except (KeyError, InvalidOperation):
            raise LayoutChanged("mercado_pago: no usable total_amount; Mercado Pago's response changed") from None
        return [Balance(self.id, "Dinero en cuenta", AccountKind.CHECKING, total, "MXN")]

    def _get(self, path: str) -> dict:
        response = self._client.get(path, headers={"Authorization": f"Bearer {self.auth['access_token']}"})
        if response.status_code in (401, 403):
            raise SessionExpired("mercado_pago: access token rejected; create a new one in Mercado Pago Developers")
        response.raise_for_status()
        return response.json()
