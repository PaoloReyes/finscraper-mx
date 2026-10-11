"""Skandia México provider tests. All ids, amounts and credentials are fake."""
from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from finscraper_mx import AuthRequired, get_provider
from finscraper_mx.core import AccountKind, LayoutChanged, SessionExpired
from finscraper_mx.institutions.skandia import Skandia

# ---------------------------------------------------------------------------
# Fake data (nothing real)
# ---------------------------------------------------------------------------

FAKE_USER = "XEXX010101HNEXXXA4"
FAKE_PASS = "123456"
FAKE_TOKEN = "fake.jwt.token"
FAKE_PLAN = "X00001"

LOGIN_RESP = {
    "User": FAKE_USER,
    "PartId": "000000001",
    "Plan": FAKE_PLAN,
    "Token": FAKE_TOKEN,
    "Message": None,
}

EMPLOYEES_RESP = {
    "Products": [
        {
            "ProductId": "2",
            "ProductName": "FONDO DE AHORRO",
            "ProductType": "FondoAhorro",
            "TotalAmmount": 50000.00,
            "Detail": [
                {"Id": "S", "Key": "APORTACION EMPLEADO", "Value": 25000.00},
                {"Id": "T", "Key": "APORTACION EMPRESA",  "Value": 25000.00},
            ],
        }
    ],
    "PartId": "000000001",
    "Plan": FAKE_PLAN,
}


# ---------------------------------------------------------------------------
# Mock transport
# ---------------------------------------------------------------------------

def fake_client(
    *,
    login_status: int = 200,
    login_resp: dict | None = None,
    employees_status: int = 200,
    employees_resp: dict | None = None,
) -> httpx.Client:
    lresp = login_resp if login_resp is not None else LOGIN_RESP
    eresp = employees_resp if employees_resp is not None else EMPLOYEES_RESP

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "Login/login" in url:
            return httpx.Response(login_status, json=lresp)
        if "Employees/Employees" in url:
            return httpx.Response(employees_status, json=eresp)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def provider(**kwargs) -> Skandia:
    return Skandia(username=FAKE_USER, password=FAKE_PASS, client=fake_client(**kwargs))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_requires_username_and_password():
    with pytest.raises(AuthRequired):
        get_provider("skandia")()


def test_requires_password():
    with pytest.raises(AuthRequired):
        get_provider("skandia")(username=FAKE_USER)


def test_reads_balance():
    balances = provider().fetch_balances()
    assert len(balances) == 1
    b = balances[0]
    assert b.institution == "skandia"
    assert b.account == "FONDO DE AHORRO"
    assert b.kind is AccountKind.INVESTMENT
    assert b.amount == Decimal("50000.00")
    assert b.currency == "MXN"


def test_multiple_products():
    eresp = {
        "Products": [
            {"ProductName": "FONDO DE AHORRO", "ProductType": "FondoAhorro", "TotalAmmount": 30000.00},
            {"ProductName": "SEGURO DE VIDA",  "ProductType": "Seguro",      "TotalAmmount": 20000.00},
        ],
        "PartId": "000000001", "Plan": FAKE_PLAN,
    }
    balances = provider(employees_resp=eresp).fetch_balances()
    assert len(balances) == 2
    assert {b.account for b in balances} == {"FONDO DE AHORRO", "SEGURO DE VIDA"}
    assert sum(b.amount for b in balances) == Decimal("50000.00")


def test_wrong_password_raises_auth_required():
    with pytest.raises(AuthRequired, match="invalid username or password"):
        provider(login_status=401).fetch_balances()


def test_login_server_error_raises_session_expired():
    with pytest.raises(SessionExpired):
        provider(login_status=500).fetch_balances()


def test_login_returns_error_message_raises_auth_required():
    bad = {"Token": None, "Message": "Usuario o contraseña incorrectos"}
    with pytest.raises(AuthRequired, match="Usuario o contraseña"):
        provider(login_resp=bad).fetch_balances()


def test_employees_401_raises_session_expired():
    with pytest.raises(SessionExpired):
        provider(employees_status=401).fetch_balances()


def test_missing_products_raises_layout_changed():
    with pytest.raises(LayoutChanged, match="Products"):
        provider(employees_resp={"SomethingElse": True}).fetch_balances()


def test_empty_products_raises_layout_changed():
    with pytest.raises(LayoutChanged, match="empty"):
        provider(employees_resp={"Products": []}).fetch_balances()


def test_bearer_token_and_plan_sent():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "Login/login" in url:
            return httpx.Response(200, json=LOGIN_RESP)
        if "Employees/Employees" in url:
            seen["auth"] = request.headers.get("authorization", "")
            seen["plan"] = request.headers.get("corpplan", "")
            return httpx.Response(200, json=EMPLOYEES_RESP)
        return httpx.Response(404)

    Skandia(username=FAKE_USER, password=FAKE_PASS,
            client=httpx.Client(transport=httpx.MockTransport(handler))).fetch_balances()
    assert seen["auth"] == f"Basic {FAKE_TOKEN}"
    assert seen["plan"] == FAKE_PLAN


def test_credentials_sent_as_query_params():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "Login/login" in str(request.url):
            seen["user"] = request.url.params.get("user", "")
            seen["password"] = request.url.params.get("password", "")
            return httpx.Response(200, json=LOGIN_RESP)
        return httpx.Response(200, json=EMPLOYEES_RESP)

    Skandia(username=FAKE_USER, password=FAKE_PASS,
            client=httpx.Client(transport=httpx.MockTransport(handler))).fetch_balances()
    assert seen["user"] == FAKE_USER
    assert seen["password"] == FAKE_PASS
