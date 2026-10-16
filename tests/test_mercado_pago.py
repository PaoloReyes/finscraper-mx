"""Mercado Pago provider, against a mocked mobile home hub. All ids and amounts here are fake."""
from decimal import Decimal

import httpx
import pytest

from finscraper_mx import AccountKind, AuthRequired, FinscraperError, LayoutChanged, SessionExpired, get_provider

AUTH = {"token": "APP_USR-fake-token", "session_id": "fake-session", "device_id": "fake-device"}


def money(amount, currency="MXN"):
    return {"amount": amount, "country": "MX", "currency": currency}


def balance_item(*amounts):
    return {"type": "banking_balance_andes", "params": {"money_amount_items": list(amounts)}}


def tab(title, *items):
    return {"title": title, "items": list(items)}


def banking_tabs():
    return [
        # Saldo carries the balance first and the 12-month yield second; only the balance is reported.
        tab("Saldo", balance_item(money("12345.67"), money("1503.39")), {"type": "banner_medium", "params": {}}),
        tab("Apartados", balance_item(money("2500.00")), {"type": "banner_medium", "params": {}}),
        tab("Criptomonedas", balance_item(money("0.00"))),
    ]


def home(tabs=None, sections=None):
    tabs = banking_tabs() if tabs is None else tabs
    if sections is None:
        sections = [
            {"type": "FINTECH_SDUI", "content": {}},
            {"type": "banking_andes", "content": {"tabs": tabs}},
        ]
    return {"id": "mp3_home", "sections": sections}


def fake_app(payload=None, status=200):
    body = home() if payload is None else payload

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != f"Bearer {AUTH['token']}" or status in (401, 403):
            return httpx.Response(401, json={"message": "unauthorized"})
        assert request.url.path == "/mp-home-hub/home"
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://mobile.mercadopago.com")


def provider(client=None, **overrides):
    auth = {**AUTH, **overrides}
    return get_provider("mercado_pago")(client=client or fake_app(), **auth)


def test_requires_auth():
    with pytest.raises(AuthRequired):
        get_provider("mercado_pago")(token="only-a-token")  # session_id and device_id missing


def test_reads_every_banking_tab_in_mxn():
    balances = {b.account: b for b in provider().fetch_balances()}
    assert set(balances) == {"Saldo", "Apartados", "Criptomonedas"}
    assert balances["Saldo"].amount == Decimal("12345.67")  # the balance, not the 1503.39 yield
    assert balances["Saldo"].kind is AccountKind.CHECKING
    assert balances["Apartados"].amount == Decimal("2500.00")
    assert balances["Apartados"].kind is AccountKind.SAVINGS
    assert balances["Criptomonedas"].amount == Decimal("0.00")
    assert balances["Criptomonedas"].kind is AccountKind.CRYPTO
    assert all(b.currency == "MXN" and b.institution == "mercado_pago" for b in balances.values())


def test_unknown_tab_is_kept_as_other():
    tabs = [*banking_tabs(), tab("Inversiones", balance_item(money("10.05")))]
    other = next(b for b in provider(fake_app(home(tabs))).fetch_balances() if b.account == "Inversiones")
    assert other.kind is AccountKind.OTHER
    assert other.amount == Decimal("10.05")


def test_amount_with_separators_is_cleaned():
    tabs = [tab("Saldo", balance_item(money("1,234,567.89")))]
    saldo = provider(fake_app(home(tabs))).fetch_balances()[0]
    assert saldo.amount == Decimal("1234567.89")


def test_rejected_token_is_session_expired():
    with pytest.raises(SessionExpired):
        provider(fake_app(status=401)).fetch_balances()


def test_missing_banking_section_is_layout_changed():
    with pytest.raises(LayoutChanged):
        provider(fake_app(home(sections=[{"type": "FINTECH_SDUI", "content": {}}]))).fetch_balances()


def test_no_balances_is_layout_changed():
    # a banking section whose tabs hold only non-balance items
    tabs = [tab("Saldo", {"type": "banner_medium", "params": {}})]
    with pytest.raises(LayoutChanged):
        provider(fake_app(home(tabs))).fetch_balances()


def test_non_mxn_amount_fails_loudly():
    tabs = [tab("Saldo", balance_item(money("1.00", currency="USD")))]
    with pytest.raises(FinscraperError, match="expected MXN"):
        provider(fake_app(home(tabs))).fetch_balances()


def test_optional_ids_are_sent_as_headers():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json=home())

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://mobile.mercadopago.com")
    provider(client, client_info="app_id=1;user_id=2", d2id="fake-d2id").fetch_balances()
    assert seen.get("x-client-info") == "app_id=1;user_id=2"
    assert seen.get("x-d2id") == "fake-d2id"
    assert "x-device-fsid" not in seen  # not supplied → not sent
