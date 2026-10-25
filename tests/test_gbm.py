"""GBM provider, against a mocked API. All ids, tokens, secrets and amounts here are fake."""
import json
from decimal import Decimal

import httpx
import pytest

from finscraper_mx import AccountKind, AuthRequired, LayoutChanged, SessionExpired, get_provider
from finscraper_mx.institutions.gbm import Gbm, _totp

# RFC 6238 test vector: base32 of ASCII "12345678901234567890", at T=59s -> 8-digit 94287082.
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
SECRET = "JBSWY3DPEHPK3PXP"  # fake account seed
CREDS = {"username": "me@example.com", "password": "pw", "totp_secret": SECRET, "device_id": "DEV-UUID"}
ACCOUNTS = {"ETFs": "BWO00001", "Fibras": "BWO00002"}
VALUES = {"BWO00001": "302933.11", "BWO00002": "291344.62", "BWO00003": "0.00"}
CONTRACT = "contract-123"
ACCOUNT_ROWS = [
    {"name": "ETFs", "legacy_contract_id": "BWO00001", "status": "active"},
    {"name": "Fibras", "legacy_contract_id": "BWO00002", "status": "active"},
    {"name": "Smart Cash", "legacy_contract_id": "BWO00003", "status": "active"},
    {"name": "Deprecated1", "legacy_contract_id": "BWO09998", "status": "active"},  # skipped (name)
    {"name": "Closed", "legacy_contract_id": "BWO09999", "status": "inactive"},     # skipped (status)
]


def fake_api(login_ok=True, mfa_ok=True):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/session/user":
            if not login_ok:
                return httpx.Response(422, json={"id": "ChallengeRequired"})
            return httpx.Response(200, json={"challengeInfo": {"session": "sess"}})
        if path == "/api/v1/session/user/challenge":
            if not mfa_ok:
                return httpx.Response(400, json={"message": "invalid code"})
            # the provider must send a 6-digit code it generated from the secret
            assert len(json.loads(request.content)["code"]) == 6
            return httpx.Response(200, json={"authorizationCode": "authz"})
        if path == "/api/v1/session/token":
            return httpx.Response(200, json={"accessToken": "fresh-access-token", "tokenType": "Bearer"})
        if path == "/v2/onboarding/contracts":
            return httpx.Response(200, json=[{"id": CONTRACT}])
        if path == f"/v1/dashboard/contracts/{CONTRACT}/accounts":
            assert request.headers["Authorization"] == "Bearer fresh-access-token"
            return httpx.Response(200, json=ACCOUNT_ROWS)
        if path.endswith("/Operation/GetContractBuyingPower"):
            assert request.headers["Authorization"] == "Bearer fresh-access-token"
            return httpx.Response(200, json={"marketValueTotal": VALUES[json.loads(request.content)["request"]]})
        return httpx.Response(404, json={})

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://auth.gbm.com")


def test_requires_credentials():
    with pytest.raises(AuthRequired, match="username"):
        get_provider("gbm")()


def test_totp_matches_rfc6238_vector():
    assert _totp(RFC_SECRET, when=59) == "287082"  # last 6 digits of the RFC 8-digit 94287082


def test_discovers_contract_and_reads_all_by_default():
    provider = get_provider("gbm")(**CREDS, client=fake_api())
    names = {b.account for b in provider.fetch_balances()}
    assert names == {"ETFs", "Fibras", "Smart Cash"}


def test_reads_explicit_accounts_in_mxn():
    provider = Gbm(**CREDS, accounts=ACCOUNTS, client=fake_api())
    balances = {b.account: b for b in provider.fetch_balances()}
    assert balances["ETFs"].amount == Decimal("302933.11")
    assert balances["Fibras"].amount == Decimal("291344.62")
    assert balances["ETFs"].kind is AccountKind.INVESTMENT
    assert balances["ETFs"].currency == "MXN"


def test_total_matches_dashboard():
    total = sum(b.amount for b in Gbm(**CREDS, accounts=ACCOUNTS, client=fake_api()).fetch_balances())
    assert total == Decimal("594277.73")


def test_device_bumped_raises_session_expired():
    with pytest.raises(SessionExpired, match="authorized device"):
        Gbm(**CREDS, accounts=ACCOUNTS, client=fake_api(login_ok=False)).fetch_balances()


def test_rejected_mfa_raises_auth_required():
    with pytest.raises(AuthRequired, match="MFA code rejected"):
        Gbm(**CREDS, accounts=ACCOUNTS, client=fake_api(mfa_ok=False)).fetch_balances()


def test_login_without_mfa_challenge():
    """GBM sometimes returns the authorization code straight from /session/user (no MFA challenge)."""
    def handler(request):
        path = request.url.path
        if path == "/api/v1/session/user":
            return httpx.Response(200, json={"authorizationCode": "authz", "id": "Success"})
        if path == "/api/v1/session/user/challenge":
            raise AssertionError("challenge must be skipped when an authorizationCode is returned directly")
        if path == "/api/v1/session/token":
            return httpx.Response(200, json={"accessToken": "fresh-access-token"})
        if path == "/v2/onboarding/contracts":
            return httpx.Response(200, json=[{"id": CONTRACT}])
        if path == f"/v1/dashboard/contracts/{CONTRACT}/accounts":
            return httpx.Response(200, json=ACCOUNT_ROWS)
        if path.endswith("/Operation/GetContractBuyingPower"):
            return httpx.Response(200, json={"marketValueTotal": VALUES[json.loads(request.content)["request"]]})
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://auth.gbm.com")
    names = {b.account for b in Gbm(**CREDS, client=client).fetch_balances()}
    assert names == {"ETFs", "Fibras", "Smart Cash"}


def test_reads_across_multiple_contracts_with_disambiguation():
    contracts = {"aaaa1111": [{"name": "ETFs", "legacy_contract_id": "L1", "status": "active"}],
                 "bbbb2222": [{"name": "ETFs", "legacy_contract_id": "L2", "status": "active"},
                              {"name": "Fibras", "legacy_contract_id": "L3", "status": "active"}]}
    amounts = {"L1": "100.00", "L2": "200.00", "L3": "300.00"}

    def handler(request):
        path = request.url.path
        if path == "/api/v1/session/user":
            return httpx.Response(200, json={"challengeInfo": {"session": "sess"}})
        if path == "/api/v1/session/user/challenge":
            return httpx.Response(200, json={"authorizationCode": "authz"})
        if path == "/api/v1/session/token":
            return httpx.Response(200, json={"accessToken": "fresh-access-token"})
        if path == "/v2/onboarding/contracts":
            return httpx.Response(200, json=[{"id": c} for c in contracts])
        for cid, rows in contracts.items():
            if path == f"/v1/dashboard/contracts/{cid}/accounts":
                return httpx.Response(200, json=rows)
        if path.endswith("/Operation/GetContractBuyingPower"):
            return httpx.Response(200, json={"marketValueTotal": amounts[json.loads(request.content)["request"]]})
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://auth.gbm.com")
    balances = {b.account: b.amount for b in Gbm(**CREDS, client=client).fetch_balances()}
    assert balances == {"ETFs": Decimal("100.00"), "ETFs (bbbb)": Decimal("200.00"), "Fibras": Decimal("300.00")}


def test_location_and_device_id_in_headers():
    gbm = Gbm(**CREDS, location=(12.34, -56.78))
    assert gbm._client.headers["device-latitude"] == "12.34"
    assert gbm._client.headers["device-longitude"] == "-56.78"
    assert gbm._client.headers["device-mac-address"] == "DEV-UUID"


@pytest.mark.parametrize("body", [{}, {"marketValueTotal": "n/a"}])
def test_unexpected_value_response_raises_layout_changed(body):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/session/user":
            return httpx.Response(200, json={"challengeInfo": {"session": "sess"}})
        if path == "/api/v1/session/user/challenge":
            return httpx.Response(200, json={"authorizationCode": "authz"})
        if path == "/api/v1/session/token":
            return httpx.Response(200, json={"accessToken": "fresh-access-token"})
        return httpx.Response(200, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://auth.gbm.com")
    provider = Gbm(**CREDS, accounts=ACCOUNTS, client=client)
    with pytest.raises(LayoutChanged, match="marketValueTotal"):
        provider.fetch_balances()
