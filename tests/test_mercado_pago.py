"""Mercado Pago provider, against a mocked home page. All cookies and amounts here are fake."""
import json
from decimal import Decimal

import httpx
import pytest

from finscraper_mx import AccountKind, AuthRequired, FinscraperError, LayoutChanged, SessionExpired, get_provider

COOKIES = "ssid=fake-session; _d2id=fake-device"


def amount(fraction, cents, iso="MXN"):
    return {"iso": iso, "currency": "$", "value": {"fraction": fraction, "cents": cents}, "hidden": False}


def tab(id, title, *amounts, type="success"):
    return {"id": id, "type": type, "title": title, "balances": {"balances": [{"amount": a, "yields": []} for a in amounts]}}


TABS = [
    tab("balance", "Saldo", amount("12345", "67")),
    tab("pots", "Apartados", amount("2,500", "00")),
    tab("crypto", "Criptomonedas", amount("0", "00")),
]


def page(tabs=TABS, sections=None):
    sections = sections if sections is not None else [{"id": "banking", "type": "BANKING", "content": {"tabs": tabs}}]
    state = {"title": "Inicio", "appProps": {"pageProps": {"gridData": [{"sections": []}, {"sections": sections}]}}}
    return (
        '<html><head><script id="__NORDIC_SCOPE__">x</script>'
        f'<script id="__NORDIC_RENDERING_CTX__" nonce="n">_n.ctx.r={json.dumps(state)};'
        '_n.ctx.r.scopes={};</script></head><body>…</body></html>'
    )


def fake_web(html=None, redirect=False):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("Cookie") != COOKIES or redirect:
            return httpx.Response(302, headers={"Location": "https://www.mercadolibre.com/jms/mlm/lgz/login"})
        assert request.url.path == "/home"
        return httpx.Response(200, text=html if html is not None else page(), headers={"Content-Type": "text/html"})

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://www.mercadopago.com.mx")


def provider(client=None, cookies=COOKIES):
    return get_provider("mercado_pago")(cookies=cookies, client=client or fake_web())


def test_requires_auth():
    with pytest.raises(AuthRequired):
        get_provider("mercado_pago")()


def test_reads_every_banking_tab_in_mxn():
    balances = {b.account: b for b in provider().fetch_balances()}
    assert set(balances) == {"Saldo", "Apartados", "Criptomonedas"}
    assert balances["Saldo"].amount == Decimal("12345.67")
    assert balances["Saldo"].kind is AccountKind.CHECKING
    assert balances["Apartados"].amount == Decimal("2500.00")  # thousands separator dropped
    assert balances["Apartados"].kind is AccountKind.SAVINGS
    assert balances["Criptomonedas"].kind is AccountKind.CRYPTO
    assert all(b.currency == "MXN" and b.institution == "mercado_pago" for b in balances.values())


def test_unknown_tab_is_kept_as_other():
    tabs = [*TABS, tab("investments", "Inversiones", amount("10", "5"))]
    other = next(b for b in provider(fake_web(page(tabs))).fetch_balances() if b.account == "Inversiones")
    assert other.kind is AccountKind.OTHER
    assert other.amount == Decimal("10.05")


def test_redirect_to_login_is_session_expired():
    with pytest.raises(SessionExpired):
        provider(cookies="ssid=stale").fetch_balances()


def test_login_page_without_redirect_is_session_expired():
    with pytest.raises(SessionExpired):
        provider(fake_web('<html><a href="https://www.mercadolibre.com/jms/mlm/lgz/login">Ingresa</a></html>')).fetch_balances()


def test_missing_banking_widget_is_layout_changed():
    with pytest.raises(LayoutChanged):
        provider(fake_web(page(sections=[{"id": "credit", "type": "CREDIT", "content": {}}]))).fetch_balances()


def test_tab_that_failed_to_load_fails_loudly():
    tabs = [TABS[0], tab("pots", "Apartados", amount("1", "00"), type="error")]
    with pytest.raises(FinscraperError, match="Apartados"):
        provider(fake_web(page(tabs))).fetch_balances()


def test_non_mxn_amount_fails_loudly():
    with pytest.raises(FinscraperError, match="expected MXN"):
        provider(fake_web(page([tab("balance", "Saldo", amount("1", "00", iso="USD"))]))).fetch_balances()
