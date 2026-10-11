"""Mercado Pago provider, against a mocked API. All ids, tokens and amounts here are fake."""
from decimal import Decimal

import httpx
import pytest

from finscraper_mx import AccountKind, AuthRequired, FinscraperError, LayoutChanged, SessionExpired, get_provider

TOKEN = "APP_USR-0000-fake"
USER_ID = 123456789
BALANCE = {
    "user_id": USER_ID,
    "total_amount": 1534.5,
    "available_balance": 1200.25,
    "unavailable_balance": 334.25,
    "currency_id": "MXN",
}


def fake_api(balance=BALANCE, me_status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"message": "invalid_token"})
        if request.url.path == "/users/me":
            return httpx.Response(me_status, json={"id": USER_ID, "site_id": "MLM"})
        if request.url.path == f"/users/{USER_ID}/mercadopago_account/balance":
            return httpx.Response(200, json=balance)
        return httpx.Response(404, json={"message": "not_found"})

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.mercadopago.com")


def provider(client=None, token=TOKEN):
    return get_provider("mercado_pago")(access_token=token, client=client or fake_api())


def test_requires_auth():
    with pytest.raises(AuthRequired):
        get_provider("mercado_pago")()


def test_reads_account_money_in_mxn():
    [balance] = provider().fetch_balances()
    assert balance.institution == "mercado_pago"
    assert balance.account == "Dinero en cuenta"
    assert balance.kind is AccountKind.CHECKING
    assert balance.amount == Decimal("1534.50")  # total: available + not yet released
    assert balance.currency == "MXN"


def test_rejected_token_is_session_expired():
    with pytest.raises(SessionExpired):
        provider(token="APP_USR-wrong").fetch_balances()


def test_non_mxn_account_fails_loudly():
    with pytest.raises(FinscraperError, match="expected MXN"):
        provider(client=fake_api(balance={**BALANCE, "currency_id": "ARS"})).fetch_balances()


def test_missing_total_is_layout_changed():
    balance = {k: v for k, v in BALANCE.items() if k != "total_amount"}
    with pytest.raises(LayoutChanged):
        provider(client=fake_api(balance=balance)).fetch_balances()
