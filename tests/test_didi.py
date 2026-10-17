"""DiDi provider, against a mocked DiDi Dinero home screen. All ids, tokens and amounts here are fake."""
import json
from decimal import Decimal

import httpx
import pytest

from finscraper_mx import AccountKind, AuthRequired, LayoutChanged, SessionExpired, get_provider

AUTH = {"token": "fake-ticket", "wsgsig": "dd03-fake-signature", "device_id": "fake-device"}


def home(cards=None, log_data=None, errno=0):
    if cards is None:
        cards = [{"cardType": "banner"}, {"cardType": "sellingPoint", "balance": "10,029.18"}]
    data = {"content": {"cardList": cards}, "logData": log_data or {}}
    return {"errno": errno, "errmsg": "ok" if errno == 0 else "session invalid", "data": data if errno == 0 else None}


def fake_app(payload=None, status=200, seen=None):
    body = home() if payload is None else payload

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["request"] = request
        hint = json.loads(request.headers.get("sofi-header-hint-content", "{}"))
        if hint.get("token") != AUTH["token"] or status in (401, 403):
            return httpx.Response(401, json={"errno": 401})
        assert request.method == "POST" and request.url.path == "/sofipo/uchome/v0/main"
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://wallet.didipay.com")


def provider(client=None, **overrides):
    return get_provider("didi")(client=client or fake_app(), **{**AUTH, **overrides})


def test_requires_auth():
    with pytest.raises(AuthRequired):
        get_provider("didi")(token="only-a-token")  # wsgsig and device_id missing


def test_reads_cuenta_balance_in_mxn():
    [balance] = provider().fetch_balances()
    assert balance.institution == "didi"
    assert balance.account == "DiDi Cuenta"
    assert balance.kind is AccountKind.SAVINGS
    assert balance.amount == Decimal("10029.18")
    assert balance.currency == "MXN"


def test_sends_captured_session_like_the_app():
    seen = {}
    provider(fake_app(seen=seen)).fetch_balances()
    request = seen["request"]
    assert request.headers["wsgsig"] == AUTH["wsgsig"]
    assert request.url.params["deviceId"] == AUTH["device_id"]
    assert json.loads(request.content) == {
        "bioFlag": 0, "sofipo_sdk_version": "7.6.96", "verifySdkVersion": "1.7", "bulletinId": ""
    }


def test_falls_back_to_log_data_amount():
    payload = home(cards=[{"cardType": "banner"}], log_data={"deposit_amount": "512.5"})
    assert provider(fake_app(payload)).fetch_balances()[0].amount == Decimal("512.50")


def test_rejected_token_is_session_expired():
    with pytest.raises(SessionExpired):
        provider(fake_app(status=401)).fetch_balances()


def test_errno_is_session_expired():
    with pytest.raises(SessionExpired, match="errno"):
        provider(fake_app(home(errno=10001))).fetch_balances()


def test_missing_balance_card_is_layout_changed():
    with pytest.raises(LayoutChanged, match="banner"):
        provider(fake_app(home(cards=[{"cardType": "banner"}]))).fetch_balances()


def test_unreadable_balance_is_layout_changed():
    with pytest.raises(LayoutChanged):
        provider(fake_app(home(cards=[{"cardType": "sellingPoint", "balance": "—"}]))).fetch_balances()
