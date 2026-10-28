"""Bitso provider, against a mocked API. All keys, balances and prices here are fake."""
import json
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


WALLET = "0x00000000000000000000000000000000000000aa"   # fake vault wallet
MXNB_VAULT = "0x08cc279532f159bdb450ebc0baf6562f585ded58"
USDC_VAULT = "0x39e18c066187c83e860801befe45ae8ecd021575"
MXNB_TOKEN = "0x00000000000000000000000000000000000000bb"
WITHDRAWALS = [
    {"currency": "mxnb", "network": "base", "details": {"withdrawal_address": WALLET.upper().replace("0X", "0x")}},
    {"currency": "mxn", "method": "sp", "details": {"clabe": "000000000000000000"}},  # SPEI: no address
]
NO_PERMISSION = {"success": False, "error": {"code": "0202", "message": "API key has no permission"}}


def fake_api(balances=BALANCES, books=BOOKS, withdrawals=()):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v3/withdrawals/":
            if withdrawals is None:
                return httpx.Response(403, json=NO_PERMISSION)
            return httpx.Response(200, json={"success": True, "payload": list(withdrawals)})
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


def fake_rpc(shares: dict[str, int]):
    """Base JSON-RPC: `shares` of each vault held by WALLET; 1 share = 2.5 assets; MXNB has 6 decimals."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        call = json.loads(request.content)["params"][0]
        to, data = call["to"].lower(), call["data"]
        calls.append((to, data[:10]))
        if data.startswith("0x70a08231"):  # balanceOf(owner)
            owner = "0x" + data[-40:]
            value = shares.get(to, 0) if owner == WALLET else 0
        elif data.startswith("0x07a2d13a"):  # convertToAssets(shares)
            value = int(data[10:], 16) * 5 // 2
        elif data.startswith("0x38d52e0f"):  # asset()
            value = int(MXNB_TOKEN, 16)
        elif data.startswith("0x313ce567") and to == MXNB_TOKEN:  # decimals()
            value = 6
        else:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"message": "revert"}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": f"0x{value:064x}"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.calls = calls
    return client


def provider(**over):
    return Bitso(
        api_key="key",
        api_secret="secret",
        client=over.get("client", fake_api()),
        rpc_client=over.get("rpc_client", fake_rpc({})),
        **({"vault_address": over["vault_address"]} if "vault_address" in over else {}),
    )


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


def test_mxnb_stablecoin_is_valued_one_to_one_without_a_market():
    balances = [{"currency": "mxnb", "total": "2500.123456"}, {"currency": "btc", "total": "0.001"}]
    held = {b.account: b.amount for b in provider(client=fake_api(balances=balances)).fetch_balances()}
    assert held == {"MXNB": Decimal("2500.12"), "BTC": Decimal("2000.00")}


def test_signature_is_sent_for_private_calls():
    provider().fetch_balances()  # fake_api asserts the Authorization header shape on /v3/balance/


def test_reads_vault_found_through_base_withdrawals():
    rpc = fake_rpc({MXNB_VAULT: 4_000_000_000})  # 4,000 shares (6 decimals here) -> 10,000 MXNB
    balances = {b.account: b for b in provider(client=fake_api(withdrawals=WITHDRAWALS), rpc_client=rpc).fetch_balances()}
    vault = balances["Bóveda MXNB"]
    assert vault.amount == Decimal("10000.00")
    assert vault.kind is AccountKind.INVESTMENT and vault.currency == "MXN"
    assert "Bóveda USDC" not in balances  # no shares: skipped
    assert balances["MXN"].amount == Decimal("1500.00")  # regular holdings still there


def test_vault_address_can_be_given():
    rpc = fake_rpc({MXNB_VAULT: 400_000})
    balances = provider(client=fake_api(withdrawals=None), rpc_client=rpc, vault_address=WALLET).fetch_balances()
    assert next(b for b in balances if b.account == "Bóveda MXNB").amount == Decimal("1.00")


def test_vaults_skipped_when_key_cannot_see_withdrawals():
    rpc = fake_rpc({MXNB_VAULT: 4_000_000_000})
    accounts = {b.account for b in provider(client=fake_api(withdrawals=None), rpc_client=rpc).fetch_balances()}
    assert accounts == {"MXN", "BTC", "ATOM"}
    assert rpc.calls == []  # no wallet, no on-chain reads


def test_no_base_withdrawals_means_no_vault_reads():
    rpc = fake_rpc({})
    provider(client=fake_api(withdrawals=WITHDRAWALS[1:]), rpc_client=rpc).fetch_balances()
    assert rpc.calls == []
