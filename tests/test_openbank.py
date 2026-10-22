"""Openbank provider tests. All ids, amounts and credentials are fake."""
from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from finscraper_mx import AuthRequired, get_provider
from finscraper_mx.core import AccountKind, SessionExpired
from finscraper_mx.institutions.openbank import Openbank

FAKE_USER = "test.user@example.com"
FAKE_PASS = "fakePass123!"
FAKE_DEVICE = "00000000-0000-4000-8000-000000000000"
FAKE_TOKEN = "fake.access.token"

LOGIN_RESP = {
    "accessToken": FAKE_TOKEN,
    "refreshToken": "fake.refresh.token",
    "sessionExpiresAt": "2099-01-01T00:00:00Z",
    "expiresIn": 899768,
}
POSITIONS_RESP = {"positions": ["*"], "nonce": "fakeNonce=="}
SUMMARY_RESP = {
    "screen": {"id": "GP_SUMMARY"},
    "summaries": [{"amount": 30191.14, "currency": "MXN", "balanceType": "MAIN"}],
    "categories": [{"id": "c3"}, {"id": "c12"}],
}
CAT_ACCOUNTS = {
    "id": "c3",
    "subProducts": {
        "accounts": [
            {"subProductCode": "CHECKING_ACCOUNT", "alias": "Cuenta Débito Open +",
             "detailSummary": {"currency": "MXN", "availableBalance": 0.0}},
            {"subProductCode": "SAVINGS_ACCOUNT", "alias": "Apartado Open",
             "detailSummary": {"currency": "MXN", "availableBalance": 30191.14}},
        ]
    },
}
CAT_CARDS = {
    "id": "c12",
    "subProducts": {
        "cards": [
            {"subProductCode": "DEBIT_CARD", "alias": "Tarjeta de Débito",
             "detailSummary": {"associatedAccount": {"accountId": "x"}}},
            {"subProductCode": "CREDIT_CARD", "alias": "Tarjeta de Crédito Open",
             "detailSummary": {"currency": "MXN", "availableBalance": 2000.0, "drawn": 0.0}},
        ]
    },
}


def _resp(status: int = 200, body: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else {}
    return r


def _client(resp: MagicMock) -> MagicMock:
    c = MagicMock()
    c.post.return_value = resp
    return c


def _provider(client: MagicMock) -> Openbank:
    return Openbank(username=FAKE_USER, password=FAKE_PASS, device_uuid=FAKE_DEVICE, client=client)


def _full_client() -> MagicMock:
    """A client whose get/post return the right response per URL (login + balance flow)."""
    c = MagicMock()

    def get_side(url, headers=None, params=None):
        if "/v7/summary" in url:
            return _resp(200, SUMMARY_RESP)
        if "/categories/c3" in url:
            return _resp(200, CAT_ACCOUNTS)
        if "/categories/c12" in url:
            return _resp(200, CAT_CARDS)
        return _resp(200, {})  # captcha-flag, etc.

    def post_side(url, headers=None, content=None):
        if "password-positions" in url:
            return _resp(200, POSITIONS_RESP)
        if url.endswith("/login"):
            return _resp(200, LOGIN_RESP)
        if url.endswith("/logout"):
            return _resp(204, {})
        return _resp(200, {})

    c.get.side_effect = get_side
    c.post.side_effect = post_side
    return c


# ---------------------------------------------------------------------------
# Registry / interface
# ---------------------------------------------------------------------------

def test_registered():
    assert get_provider("openbank") is Openbank
    assert Openbank.id == "openbank"
    assert Openbank.auth_fields == ("username", "password", "device_uuid")


def test_missing_auth_raises():
    with pytest.raises(AuthRequired):
        Openbank(username=FAKE_USER, password=FAKE_PASS)  # no device_uuid


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def test_login_posts_expected_request():
    client = _client(_resp(200, LOGIN_RESP))
    data = _provider(client)._login()
    assert data["accessToken"] == FAKE_TOKEN

    (url,), kwargs = client.post.call_args  # last post = login
    assert url.endswith("/mb-api-auth/authentication/login")
    headers = kwargs["headers"]
    assert headers["bankid"] == "OK_MX"
    assert headers["user-agent"] == "okhttp/5.1.0"
    assert json.loads(headers["platforminformation"])["uuid"] == FAKE_DEVICE
    body = json.loads(kwargs["content"])
    assert body["username"] == FAKE_USER
    assert body["passwordPosition"] == ["*"]
    assert body["deviceInformation"]["deviceUUID"] == FAKE_DEVICE
    assert body["deviceInformation"]["mobileDeviceInfo"]["rooted"] is False


def test_login_rejected_raises_session_expired():
    with pytest.raises(SessionExpired):
        _provider(_client(_resp(401, {"error": "bad creds"})))._login()


def test_optional_proxy_routes_the_client():
    proxied = get_provider("openbank")(username=FAKE_USER, password=FAKE_PASS, device_uuid=FAKE_DEVICE,
                                       proxy="http://127.0.0.1:8899")
    assert any(str(t._pool._proxy_url.origin) == "http://127.0.0.1:8899" for t in proxied._client._mounts.values())


def test_login_without_token_raises_auth_required():
    with pytest.raises(AuthRequired):
        _provider(_client(_resp(200, {"someOtherField": 1})))._login()


def test_auth_headers_use_custom_accesstoken_header():
    headers = _provider(_client(_resp()))._auth_headers(FAKE_TOKEN)
    assert headers["accesstoken"] == FAKE_TOKEN
    assert "authorization" not in {k.lower() for k in headers}  # NOT Bearer


# ---------------------------------------------------------------------------
# Balances (global position)
# ---------------------------------------------------------------------------

def test_fetch_balances_maps_products():
    client = _full_client()
    balances = _provider(client).fetch_balances()

    by_account = {b.account: b for b in balances}
    assert by_account["Cuenta Débito Open +"].kind == AccountKind.CHECKING
    assert by_account["Cuenta Débito Open +"].amount == Decimal("0.00")
    assert by_account["Apartado Open"].kind == AccountKind.SAVINGS
    assert by_account["Apartado Open"].amount == Decimal("30191.14")
    # cards are excluded: credit is not wanted, debit mirrors its checking account
    assert "Tarjeta de Crédito Open" not in by_account
    assert "Tarjeta de Débito" not in by_account
    assert len(balances) == 2
    assert all(b.currency == "MXN" for b in balances)
    assert all(b.kind != AccountKind.CREDIT for b in balances)


def test_fetch_balances_logs_out_even_on_error():
    """A failure mid-read must still release the session (logout in finally)."""
    client = _full_client()

    def get_side(url, headers=None, params=None):
        if "/v7/summary" in url:
            return _resp(500, {})  # break the read after login
        return _resp(200, {})

    client.get.side_effect = get_side
    with pytest.raises(Exception):  # noqa: B017
        _provider(client).fetch_balances()
    logout_calls = [c for c in client.post.call_args_list if c.args and c.args[0].endswith("/logout")]
    assert logout_calls, "expected a logout POST even when the read fails"
