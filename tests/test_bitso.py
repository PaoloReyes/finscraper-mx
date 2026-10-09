"""Bitso provider, against a mocked API. All keys, balances and prices here are fake."""
from decimal import Decimal

import httpx
import pytest

from finscraper_mx import AccountKind, FinscraperError
from finscraper_mx.institutions.bitso import Bitso

# markets: btc & eth have direct MXN; atom only has USD; usd has usd_mxn (the bridge).
BOOKS = ["btc_mxn", "eth_mxn", "atom_usd", "usd_mxn"]
PRICES = {"btc_mxn": "2000000.00", "eth_mxn": "90000.00", "atom_usd": "5.00", "usd_mxn": "20.00"}
BALANCES = [
    {"currency": "mxn", "total": "1500.00"},
    {"currency": "btc", "total": "0.50000000"},   # 0.5 * 2,000,000 = 1,000,000
    {"currency": "atom", "total": "10.00000000"},  # 10 * 5 USD * 20 MXN/USD = 1,000
    {"currency": "eth", "total": "0"},             # zero: skipped
]


def fake_api(balances=BALANCES, books=BOOKS):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v3/available_books/":
            return httpx.Response(200, json={"success": True, "payload": [{"book": b} for b in books]})
        if path == "/v3/balance/":
            assert request.headers.get("Authorization", "").startswith("Bitso key:")
            return httpx.Response(200, json={"success": True, "payload": {"balances": balances}})
        if path == "/v3/ticker/":
            book = request.url.params["book"]
            if book not in PRICES:
                return httpx.Response(404, json={"success": False})
            return httpx.Response(200, json={"success": True, "payload": {"book": book, "last": PRICES[book]}})
        return httpx.Response(404, json={"success": False})

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bitso.com")


def provider(**over):
    return Bitso(api_key="key", api_secret="secret", client=over.get("client", fake_api()))


def test_values_assets_in_mxn_and_skips_zero():
    balances = {b.account: b for b in provider().fetch_balances()}
    assert set(balances) == {"MXN", "BTC", "ATOM"}            # ETH (zero) skipped
    assert balances["MXN"].amount == Decimal("1500.00")
    assert balances["BTC"].amount == Decimal("1000000.00")
    assert balances["BTC"].currency == "MXN"
    assert balances["BTC"].kind is AccountKind.CRYPTO


def test_bridges_usd_only_asset_through_usd_mxn():
    atom = next(b for b in provider().fetch_balances() if b.account == "ATOM")
    assert atom.amount == Decimal("1000.00")  # 10 * 5 USD * 20 MXN/USD


def test_total_is_the_sum_in_mxn():
    total = sum(b.amount for b in provider().fetch_balances())
    assert total == Decimal("1002500.00")


def test_asset_with_no_usable_market_fails_loudly():
    balances = [{"currency": "doge", "total": "100"}]
    with pytest.raises(FinscraperError, match="cannot value DOGE"):
        provider(client=fake_api(balances=balances, books=["btc_mxn"])).fetch_balances()


def test_signature_is_sent_for_private_calls():
    provider().fetch_balances()  # fake_api asserts the Authorization header shape on /v3/balance/
