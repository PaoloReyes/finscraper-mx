"""Mifel provider tests. All ids, amounts and credentials are fake."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import httpx
import pytest

from finscraper_mx import AuthRequired, get_provider
from finscraper_mx.core import AccountKind, LayoutChanged, SessionExpired
from finscraper_mx.institutions.mifel import Mifel

# ---------------------------------------------------------------------------
# Fake data (nothing real)
# ---------------------------------------------------------------------------

FAKE_USER = "testuser"
FAKE_PASS = "testpass"
FAKE_TOKEN = "fake.access.token"
FAKE_NUM_CTA = "00000000001"

TOKEN_RESP = {
    "access_token": FAKE_TOKEN,
    "expires_in": 120,
    "refresh_expires_in": 300,
    "refresh_token": "fake.refresh.token",
    "token_type": "Bearer",
    "scope": "openid email profile",
}

CUENTAS_RESP = {
    "codigo": "0",
    "descripcion": "Operación realizada con Exito",
    "data": [
        {
            "numCta": FAKE_NUM_CTA,
            "clabe": "042180000000000001",
            "schemeCode": "PFDI4",
            "schemeType": "CAA",
            "moneda": "MXN",
            "montoDisponible": "0.00",
            "montoRetenido": "0.00",
            "descripcionSchemeCode": "Digital Evoluciona",
            "montoRetenidoCaja": "50000.00",
        }
    ],
    "meta": {"totalRegistros": "1"},
}

DETALLE_RESP = {
    "codigo": "0",
    "descripcion": "Operacion ejecutada exitosamente",
    "data": [
        {
            "numTarjeta": "5264760000000001",
            "saldoCajas": "50000",
            "descripcionTipoProducto": "Digital Evoluciona",
            "codStatusTarjeta": "ACT",
        }
    ],
}


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _curl_mock(status: int = 200, body: dict | None = None) -> MagicMock:
    """Mock a curl-cffi Session whose post/put returns the given status and body."""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body if body is not None else TOKEN_RESP
    session = MagicMock()
    session.post.return_value = resp
    session.put.return_value = MagicMock(status_code=200)
    return session


def fake_httpx(
    *,
    cuentas_resp: dict | None = None,
    detalle_status: int = 200,
    detalle_resp: dict | None = None,
    cuentas_status: int = 200,
) -> httpx.Client:
    cresp = cuentas_resp if cuentas_resp is not None else CUENTAS_RESP
    dresp = detalle_resp if detalle_resp is not None else DETALLE_RESP

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "cuentas-vinculadas-v2" in url:
            return httpx.Response(cuentas_status, json=cresp)
        if "detalle-cuenta-vinculada" in url:
            return httpx.Response(detalle_status, json=dresp)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def provider(
    *,
    token_status: int = 200,
    token_resp: dict | None = None,
    **httpx_kwargs,
) -> Mifel:
    return Mifel(
        username=FAKE_USER,
        password=FAKE_PASS,
        client=fake_httpx(**httpx_kwargs),
        _curl_session=_curl_mock(token_status, token_resp),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_requires_username_and_password():
    with pytest.raises(AuthRequired):
        get_provider("mifel")()


def test_requires_password():
    with pytest.raises(AuthRequired):
        get_provider("mifel")(username=FAKE_USER)


def test_reads_balance_from_detalle():
    balances = provider().fetch_balances()
    assert len(balances) == 2
    by_kind = {b.kind: b for b in balances}
    checking = by_kind[AccountKind.CHECKING]
    savings = by_kind[AccountKind.SAVINGS]
    assert checking.institution == "mifel"
    assert checking.account == "Digital Evoluciona"
    assert checking.amount == Decimal("0.00")
    assert checking.currency == "MXN"
    assert savings.account == "Digital Evoluciona (Caja)"
    assert savings.amount == Decimal("50000.00")


def test_falls_back_to_montoDisponible_when_detalle_fails():
    balances = provider(detalle_status=500).fetch_balances()
    # caja still emitted (uses montoRetenidoCaja as fallback), checking also emitted
    assert len(balances) == 2
    checking = next(b for b in balances if b.kind is AccountKind.CHECKING)
    assert checking.amount == Decimal("0.00")


def test_multiple_accounts():
    cresp = {
        "codigo": "0",
        "descripcion": "ok",
        "data": [
            {"numCta": "00000000001", "moneda": "MXN", "montoDisponible": "1000.00",
             "montoRetenidoCaja": "0.00",
             "descripcionSchemeCode": "Cuenta A", "schemeCode": "A", "schemeType": "CAA"},
            {"numCta": "00000000002", "moneda": "MXN", "montoDisponible": "2000.00",
             "montoRetenidoCaja": "0.00",
             "descripcionSchemeCode": "Cuenta B", "schemeCode": "B", "schemeType": "CAA"},
        ],
        "meta": {"totalRegistros": "2"},
    }
    balances = provider(cuentas_resp=cresp, detalle_status=500).fetch_balances()
    # Both accounts have zero caja so only checking rows emitted
    assert len(balances) == 2
    assert {b.account for b in balances} == {"Cuenta A", "Cuenta B"}


def test_wrong_password_raises_auth_required():
    with pytest.raises(AuthRequired, match="invalid username or password"):
        provider(token_status=401).fetch_balances()


def test_token_server_error_raises_session_expired():
    with pytest.raises(SessionExpired):
        provider(token_status=500).fetch_balances()


def test_missing_access_token_raises_layout_changed():
    with pytest.raises(LayoutChanged, match="access_token"):
        provider(token_resp={"error": "unsupported_grant_type"}).fetch_balances()


def test_cuentas_error_code_raises_layout_changed():
    bad = {"codigo": "99", "descripcion": "Error interno", "data": []}
    with pytest.raises(LayoutChanged, match="cuentas-vinculadas error"):
        provider(cuentas_resp=bad).fetch_balances()


def test_integracion_401_raises_session_expired():
    with pytest.raises(SessionExpired):
        provider(cuentas_status=401).fetch_balances()


def test_empty_accounts_returns_empty_list():
    cresp = {"codigo": "0", "descripcion": "ok", "data": [], "meta": {"totalRegistros": "0"}}
    assert provider(cuentas_resp=cresp).fetch_balances() == []


def test_bearer_token_sent_to_integracion():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "cuentas-vinculadas" in url:
            seen["auth"] = request.headers.get("authorization", "")
            return httpx.Response(200, json=CUENTAS_RESP)
        if "detalle-cuenta" in url:
            return httpx.Response(200, json=DETALLE_RESP)
        return httpx.Response(404)

    Mifel(
        username=FAKE_USER,
        password=FAKE_PASS,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        _curl_session=_curl_mock(),
    ).fetch_balances()
    assert seen.get("auth") == f"Bearer {FAKE_TOKEN}"


def test_newrelic_header_sent_to_sso():
    """The curl session must receive a newrelic header on the token POST."""
    curl = _curl_mock()
    Mifel(
        username=FAKE_USER,
        password=FAKE_PASS,
        client=fake_httpx(),
        _curl_session=curl,
    ).fetch_balances()
    call_kwargs = curl.post.call_args
    headers = call_kwargs.kwargs.get("headers", {})
    assert "newrelic" in headers
    assert "traceparent" in headers
