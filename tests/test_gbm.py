"""GBM+ provider, against a mocked API. All ids, tokens and amounts here are fake."""
import json
from decimal import Decimal

import httpx
import pytest

from finscraper_mx import AccountKind, AuthRequired, SessionExpired
from finscraper_mx.institutions.gbm import Gbm

ACCOUNTS = {"ETFs": "BWO00001", "Fibras": "BWO00002"}
VALUES = {"BWO00001": "302933.11", "BWO00002": "291344.62", "BWO00003": "0.00"}
CONTRACT = "contract-123"
# what the accounts-listing endpoint returns: two active, one deprecated, one inactive
ACCOUNT_ROWS = [
    {"name": "ETFs", "legacy_contract_id": "BWO00001", "status": "active"},
    {"name": "Fibras", "legacy_contract_id": "BWO00002", "status": "active"},
    {"name": "Smart Cash", "legacy_contract_id": "BWO00003", "status": "active"},
    {"name": "Deprecated1", "legacy_contract_id": "BWO09998", "status": "active"},  # skipped (name)
    {"name": "Closed", "legacy_contract_id": "BWO09999", "status": "inactive"},     # skipped (status)
]


def fake_api(refresh_ok=True):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/session/user/refresh":
            if not refresh_ok:
                return httpx.Response(401, json={"message": "Unauthorized"})
            return httpx.Response(200, json={"id": "Success", "accessToken": "fresh-access-token"})
        if path == "/api/v1/session/user":
            return httpx.Response(200, json={"challengeInfo": {"session": "sess"}})
        if path == "/api/v1/session/user/challenge":
            return httpx.Response(200, json={"authorizationCode": "authz"})
        if path == "/api/v1/session/token":
            return httpx.Response(200, json={"accessToken": "a", "refreshToken": "minted-refresh"})
        if path == "/v2/onboarding/contracts":
            return httpx.Response(200, json=[{"id": CONTRACT}])
        if path == f"/v1/dashboard/contracts/{CONTRACT}/accounts":
            assert request.headers["Authorization"] == "Bearer fresh-access-token"
            return httpx.Response(200, json=ACCOUNT_ROWS)
        if path.endswith("/Operation/GetContractBuyingPower"):
            assert request.headers["Authorization"] == "Bearer fresh-access-token"
            book = json.loads(request.content)["request"]
            return httpx.Response(200, json={"marketValueTotal": VALUES[book]})
        return httpx.Response(404, json={})

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://auth.gbm.com")


def test_discovers_contract_and_reads_all_by_default():
    # only a refresh token: discovers the contract, lists active strategies, reads each
    provider = Gbm(refresh_token="rt", client=fake_api())
    names = {b.account for b in provider.fetch_balances()}
    assert names == {"ETFs", "Fibras", "Smart Cash"}


def test_no_session_raises_session_expired():
    with pytest.raises(SessionExpired):
        Gbm(accounts=ACCOUNTS, client=fake_api()).fetch_balances()


def test_token_file_is_loaded_then_renewed_and_rewritten(tmp_path):
    token_file = tmp_path / "gbm_tokens.json"
    token_file.write_text(json.dumps({"refreshToken": "rt"}))
    # loads the refresh token from the file
    provider = Gbm(token_file=token_file, user="me@example.com", accounts=ACCOUNTS, client=fake_api())
    assert sum(b.amount for b in provider.fetch_balances()) == Decimal("594277.73")
    # renew() logs in and rewrites the file with the new refresh token (owner-only)
    assert provider.renew("pw", "123456") == "minted-refresh"
    assert json.loads(token_file.read_text())["refreshToken"] == "minted-refresh"
    assert oct(token_file.stat().st_mode)[-3:] == "600"


def test_reads_explicit_accounts_in_mxn():
    provider = Gbm(refresh_token="rt", accounts=ACCOUNTS, client=fake_api())
    balances = {b.account: b for b in provider.fetch_balances()}
    assert balances["ETFs"].amount == Decimal("302933.11")
    assert balances["Fibras"].amount == Decimal("291344.62")
    assert balances["ETFs"].kind is AccountKind.INVESTMENT
    assert balances["ETFs"].currency == "MXN"


def test_auto_lists_active_strategies_by_default():
    provider = Gbm(refresh_token="rt", contract_id=CONTRACT, client=fake_api())
    names = {b.account for b in provider.fetch_balances()}
    assert names == {"ETFs", "Fibras", "Smart Cash"}  # Deprecated1 and Closed excluded


def test_list_accounts_helper():
    provider = Gbm(refresh_token="rt", contract_id=CONTRACT, client=fake_api())
    assert provider.list_accounts() == {"ETFs": "BWO00001", "Fibras": "BWO00002", "Smart Cash": "BWO00003"}


def test_total_matches_dashboard():
    provider = Gbm(refresh_token="rt", accounts=ACCOUNTS, client=fake_api())
    assert sum(b.amount for b in provider.fetch_balances()) == Decimal("594277.73")


def test_expired_refresh_token_raises_session_expired():
    provider = Gbm(refresh_token="stale", accounts=ACCOUNTS, client=fake_api(refresh_ok=False))
    with pytest.raises(SessionExpired):
        provider.fetch_balances()


def test_reads_across_multiple_contracts_with_disambiguation():
    contracts = {"aaaa1111": [{"name": "ETFs", "legacy_contract_id": "L1", "status": "active"}],
                 "bbbb2222": [{"name": "ETFs", "legacy_contract_id": "L2", "status": "active"},
                              {"name": "Fibras", "legacy_contract_id": "L3", "status": "active"}]}
    amounts = {"L1": "100.00", "L2": "200.00", "L3": "300.00"}

    def handler(request):
        path = request.url.path
        if path == "/api/v1/session/user/refresh":
            return httpx.Response(200, json={"id": "Success", "accessToken": "fresh-access-token"})
        if path == "/v2/onboarding/contracts":
            return httpx.Response(200, json=[{"id": c} for c in contracts])
        for cid, rows in contracts.items():
            if path == f"/v1/dashboard/contracts/{cid}/accounts":
                return httpx.Response(200, json=rows)
        if path.endswith("/Operation/GetContractBuyingPower"):
            return httpx.Response(200, json={"marketValueTotal": amounts[json.loads(request.content)["request"]]})
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://auth.gbm.com")
    balances = {b.account: b.amount for b in Gbm(refresh_token="rt", client=client).fetch_balances()}
    assert balances == {"ETFs": Decimal("100.00"), "ETFs (bbbb)": Decimal("200.00"), "Fibras": Decimal("300.00")}


def test_login_returns_a_refresh_token():
    def handler(request):
        path = request.url.path
        if path == "/api/v1/session/user":
            return httpx.Response(200, json={"challengeInfo": {"session": "sess"}})
        if path == "/api/v1/session/user/challenge":
            return httpx.Response(200, json={"authorizationCode": "authz"})
        if path == "/api/v1/session/token":
            return httpx.Response(200, json={"accessToken": "a", "refreshToken": "new-refresh"})
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://auth.gbm.com")
    assert Gbm.login("me@example.com", "pw", "123456", client=client) == "new-refresh"


def test_location_is_customer_managed():
    gbm = Gbm(refresh_token="rt", location=(12.34, -56.78))
    assert gbm._client.headers["device-latitude"] == "12.34"
    assert gbm._client.headers["device-longitude"] == "-56.78"
