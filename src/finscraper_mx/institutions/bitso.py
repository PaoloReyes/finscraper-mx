"""Bitso, through its official REST API (https://docs.bitso.com).

Reads the account's balance per asset with a read-only API key and values each holding in MXN using
Bitso's public tickers. Not every asset has a direct MXN market (e.g. ATOM trades only against USD),
so an asset is valued through the first available market: MXN, then USD, USDT, or BTC — each of those
bridges itself having an MXN market. The key needs only the permission to read balances.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from decimal import Decimal

import httpx

from ..core import AccountKind, Balance, FinscraperError, Provider

BASE_URL = "https://api.bitso.com"
QUOTE = "mxn"                      # value everything in Mexican pesos
BRIDGES = ("usd", "usdt", "btc")  # tried in order when there is no direct MXN market
CENT = Decimal("0.01")


class Bitso(Provider):
    id = "bitso"
    name = "Bitso"
    auth_fields = ("api_key", "api_secret")

    def __init__(self, *, client: httpx.Client | None = None, base_url: str = BASE_URL, **auth: str) -> None:
        super().__init__(**auth)
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(base_url=self._base_url, timeout=20)

    def fetch_balances(self) -> list[Balance]:
        self._books = {book["book"] for book in self._get("/v3/available_books/")}
        self._price_cache: dict[str, Decimal] = {}
        return [
            Balance(
                institution=self.id,
                account=currency.upper(),
                kind=AccountKind.CRYPTO,
                amount=(amount * self._price_mxn(currency)).quantize(CENT),
                currency=QUOTE.upper(),
            )
            for currency, amount in self._holdings().items()
        ]

    def _holdings(self) -> dict[str, Decimal]:
        """Non-zero balances, as {currency: amount in that currency}."""
        held = {b["currency"]: Decimal(b["total"]) for b in self._get("/v3/balance/", auth=True)["balances"]}
        return {currency: total for currency, total in held.items() if total > 0}

    def _price_mxn(self, currency: str) -> Decimal:
        """MXN value of one unit of `currency`, bridging through USD/USDT/BTC when needed."""
        if currency == QUOTE:
            return Decimal(1)
        if currency in self._price_cache:
            return self._price_cache[currency]
        for quote in (QUOTE, *BRIDGES):
            if f"{currency}_{quote}" in self._books:
                last = Decimal(self._get("/v3/ticker/", params={"book": f"{currency}_{quote}"})["last"])
                price = last if quote == QUOTE else last * self._price_mxn(quote)
                self._price_cache[currency] = price
                return price
        raise FinscraperError(f"bitso: cannot value {currency.upper()} in MXN (no mxn/usd/usdt/btc market)")

    def _get(self, path: str, *, auth: bool = False, params: dict[str, str] | None = None):
        headers = {"Authorization": self._sign("GET", path)} if auth else {}
        response = self._client.get(path, params=params, headers=headers)
        response.raise_for_status()
        body = response.json()
        if not body.get("success"):
            raise FinscraperError(f"bitso: request to {path} was not successful")
        return body["payload"]

    def _sign(self, method: str, path: str) -> str:
        nonce = str(int(time.time() * 1000))
        message = f"{nonce}{method}{path}".encode()
        signature = hmac.new(self.auth["api_secret"].encode(), message, hashlib.sha256).hexdigest()
        return f"Bitso {self.auth['api_key']}:{nonce}:{signature}"
