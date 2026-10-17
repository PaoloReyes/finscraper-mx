"""DiDi provider, against a mocked SoFiPo home. All ids and amounts here are fake."""
import json
from decimal import Decimal

import httpx
import pytest

from finscraper_mx import AccountKind, AuthRequired, LayoutChanged, SessionExpired, get_provider

AUTH = {"token": "fake-sofi-token", "signed_query": "appversion=7.7.0&deviceId=fake-dev&countryCode=MX", "wsgsig": "dd06-fake"}


def card(card_type, title, **fields):
    return {"cardType": card_type, "title": {"text": title}, **fields}


def home(cards=None, errno=0, errmsg="Operation successful"):
    if cards is None:
        cards = [
            card("cnbv", "¡Hola, Fulano!"),
            card("sellingPoint", "Saldo (MXN$)", balance="10,058.37", totalEarning="+1,956.99"),
            card("termDeposit", "Mis Ahorros (MXN$)", amount="0.00"),
            card("banner", "Más promociones"),
        ]
    return {"errno": errno, "errmsg": errmsg, "data": {"content": {"cardList": cards}}}


def fake_app(payload=None, status=200):
    body = home() if payload is None else payload

    def handler(request: httpx.Request) -> httpx.Response:
        hint = json.loads(request.headers.get("sofi-header-hint-content", "{}"))
        if hint.get("token") != AUTH["token"] or status in (401, 403):
            return httpx.Response(status if status in (401, 403) else 401, json={"message": "unauthorized"})
        assert request.url.path == "/sofipo/uchome/v0/main"
        return httpx.Response(200, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://wallet.didipay.com")


def provider(client=None, **overrides):
    auth = {**AUTH, **overrides}
    return get_provider("didi")(client=client or fake_app(), **auth)


def test_requires_auth():
    with pytest.raises(AuthRequired):
        get_provider("didi")(token="only-a-token")  # signed_query and wsgsig missing


def test_reads_savings_and_term_deposit_in_mxn():
    balances = {b.account: b for b in provider().fetch_balances()}
    assert set(balances) == {"Saldo", "Mis Ahorros"}
    assert balances["Saldo"].amount == Decimal("10058.37")  # the balance, not the +1,956.99 yield
    assert balances["Saldo"].kind is AccountKind.SAVINGS
    assert balances["Mis Ahorros"].amount == Decimal("0.00")
    assert balances["Mis Ahorros"].kind is AccountKind.INVESTMENT
    assert all(b.currency == "MXN" and b.institution == "didi" for b in balances.values())


def test_amount_with_separators_is_cleaned():
    cards = [card("sellingPoint", "Saldo (MXN$)", balance="1,234,567.89")]
    saldo = provider(fake_app(home(cards))).fetch_balances()[0]
    assert saldo.amount == Decimal("1234567.89")


def test_replays_the_captured_signed_query_and_signature_verbatim():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = request.url.query.decode()
        seen["wsgsig"] = request.headers.get("wsgsig")
        return httpx.Response(200, json=home())

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://wallet.didipay.com")
    provider(client).fetch_balances()
    assert seen["query"] == "appversion=7.7.0&deviceId=fake-dev&countryCode=MX"  # not re-encoded
    assert seen["wsgsig"] == "dd06-fake"


def test_rejected_token_is_session_expired():
    with pytest.raises(SessionExpired):
        provider(fake_app(status=401)).fetch_balances()


def test_nonzero_errno_is_session_expired():
    with pytest.raises(SessionExpired, match="recapture"):
        provider(fake_app(home(errno=101016, errmsg="token invalid"))).fetch_balances()


def test_empty_home_is_session_expired():
    # errno 0 but no cards: the request signature was not accepted
    empty = {"errno": 0, "errmsg": "ok", "data": None}
    with pytest.raises(SessionExpired, match="signature"):
        provider(fake_app(empty)).fetch_balances()


def test_no_balance_cards_is_layout_changed():
    cards = [card("cnbv", "¡Hola, Fulano!"), card("banner", "Más promociones")]
    with pytest.raises(LayoutChanged):
        provider(fake_app(home(cards))).fetch_balances()
