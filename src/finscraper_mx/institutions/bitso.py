"""Bitso, through its official REST API (https://docs.bitso.com).

Reads the account's balance per asset with a read-only API key and values each holding in MXN using
Bitso's public tickers. Not every asset has a direct MXN market (e.g. ATOM trades only against USD),
so an asset is valued through the first available market: MXN, then USD, USDT, or BTC — each of those
bridges itself having an MXN market. The peso stablecoin MXNB has no market and is valued 1:1 in MXN.

Bitso Vaults (Bóvedas) are not in Bitso's API: the coins move to a wallet in the user's name on Base and
are deposited into Morpho vaults. The provider finds that wallet among the account's Base withdrawals
(which needs the key's "Ver información de cuenta" permission, or pass `vault_address=`) and reads each
vault position on-chain, interest included. Without either, vaults are skipped.
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
PEGGED = {"mxnb"}                 # MXN stablecoins, redeemable 1:1 for pesos
CENT = Decimal("0.01")

BASE_RPC = "https://mainnet.base.org"
# Bitso Vaults (Morpho Vault V2 on Base) -> label and the Bitso currency their asset is valued as.
VAULTS = {
    "0x08cc279532f159bdb450ebc0baf6562f585ded58": ("MXNB", "mxnb"),  # Juno MXNB Prime
    "0x39e18c066187c83e860801befe45ae8ecd021575": ("USDC", "usd"),
}
BALANCE_OF, CONVERT_TO_ASSETS, ASSET, DECIMALS = "0x70a08231", "0x07a2d13a", "0x38d52e0f", "0x313ce567"
NO_PERMISSION = "0202"


class Bitso(Provider):
    id = "bitso"
    name = "Bitso"
    auth_fields = ("api_key", "api_secret")

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        base_url: str = BASE_URL,
        rpc_client: httpx.Client | None = None,
        base_rpc: str = BASE_RPC,
        vault_address: str | None = None,
        **auth: str,
    ) -> None:
        super().__init__(**auth)
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(base_url=self._base_url, timeout=20)
        self._rpc = rpc_client or httpx.Client(timeout=20)
        self._base_rpc = base_rpc
        self._vault_address = vault_address

    def fetch_balances(self) -> list[Balance]:
        self._books = {book["book"] for book in self._get("/v3/available_books/")}
        self._price_cache: dict[str, Decimal] = {}
        balances = [
            Balance(
                institution=self.id,
                account=currency.upper(),
                kind=AccountKind.CRYPTO,
                amount=(amount * self._price_mxn(currency)).quantize(CENT),
                currency=QUOTE.upper(),
            )
            for currency, amount in self._holdings().items()
        ]
        for label, currency, amount in self._vault_positions():
            balances.append(
                Balance(
                    institution=self.id,
                    account=f"Bóveda {label}",
                    kind=AccountKind.INVESTMENT,
                    amount=(amount * self._price_mxn(currency)).quantize(CENT),
                    currency=QUOTE.upper(),
                )
            )
        return balances

    def _holdings(self) -> dict[str, Decimal]:
        """Non-zero balances, as {currency: amount in that currency}."""
        held = {b["currency"]: Decimal(b["total"]) for b in self._get("/v3/balance/", auth=True)["balances"]}
        return {currency: total for currency, total in held.items() if total > 0}

    def _vault_wallets(self) -> set[str]:
        """The user's on-chain vault wallet: given, or the destination of their Base withdrawals."""
        if self._vault_address:
            return {self._vault_address.lower()}
        try:
            withdrawals = self._get("/v3/withdrawals/?limit=100", auth=True)
        except FinscraperError as error:
            if NO_PERMISSION in str(error):
                return set()  # the key can't see withdrawals: vaults are skipped
            raise
        return {
            w["details"]["withdrawal_address"].lower()
            for w in withdrawals
            if w.get("network") == "base" and (w.get("details") or {}).get("withdrawal_address")
        }

    def _vault_positions(self) -> list[tuple[str, str, Decimal]]:
        """(label, Bitso currency, amount of the vault's asset) for every non-empty vault position."""
        wallets = self._vault_wallets()
        positions = []
        for vault, (label, currency) in VAULTS.items():
            shares = sum(self._call_uint(vault, BALANCE_OF + _word(wallet)) for wallet in wallets)
            if not shares:
                continue
            assets = self._call_uint(vault, CONVERT_TO_ASSETS + f"{shares:064x}")
            asset = "0x" + f"{self._call_uint(vault, ASSET):040x}"
            amount = Decimal(assets).scaleb(-self._call_uint(asset, DECIMALS))
            if amount.quantize(CENT):
                positions.append((label, currency, amount))
        return positions

    def _call_uint(self, to: str, data: str) -> int:
        """eth_call on Base, decoding the result as one uint256."""
        response = self._rpc.post(
            self._base_rpc,
            json={"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params": [{"to": to, "data": data}, "latest"]},
        )
        response.raise_for_status()
        body = response.json()
        if "result" not in body:
            raise FinscraperError(f"bitso: Base RPC call to {to} failed: {body.get('error')}")
        return int(body["result"], 16) if body["result"] not in ("0x", "") else 0

    def _price_mxn(self, currency: str) -> Decimal:
        """MXN value of one unit of `currency`, bridging through USD/USDT/BTC when needed."""
        if currency == QUOTE or currency in PEGGED:
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
        try:
            body = response.json()
        except ValueError:
            response.raise_for_status()
            raise
        if not body.get("success"):
            code = (body.get("error") or {}).get("code", "")
            raise FinscraperError(f"bitso: request to {path} was not successful {code}".rstrip())
        return body["payload"]

    def _sign(self, method: str, path: str) -> str:
        nonce = str(int(time.time() * 1000))
        message = f"{nonce}{method}{path}".encode()
        signature = hmac.new(self.auth["api_secret"].encode(), message, hashlib.sha256).hexdigest()
        return f"Bitso {self.auth['api_key']}:{nonce}:{signature}"


def _word(address: str) -> str:
    """An address as a 32-byte ABI argument."""
    return address.lower().removeprefix("0x").rjust(64, "0")
