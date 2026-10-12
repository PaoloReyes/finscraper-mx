"""Plata provider tests — fully synthetic gRPC/protobuf fixtures, no real data."""
from __future__ import annotations

import base64
from decimal import Decimal

import httpx
import pytest

from finscraper_mx import AuthRequired, get_provider
from finscraper_mx.core import AccountKind, LayoutChanged, SessionExpired

# ---------------------------------------------------------------------------
# tiny protobuf / gRPC encoders (test-only, to build fake response bodies)
# ---------------------------------------------------------------------------


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _tag(field_no: int, wire: int) -> bytes:
    return _varint((field_no << 3) | wire)


def _ld(field_no: int, payload: bytes) -> bytes:
    """A length-delimited (wire type 2) field."""
    return _tag(field_no, 2) + _varint(len(payload)) + payload


def _vint(field_no: int, value: int) -> bytes:
    """A varint (wire type 0) field."""
    return _tag(field_no, 0) + _varint(value)


def _msg(*fields: bytes) -> bytes:
    return b"".join(fields)


def _money(amount: str, currency: bytes = b"MXN") -> bytes:
    d = Decimal(amount)
    units = int(d)
    nanos = int((d - units) * 1_000_000_000)
    return _msg(_ld(1, currency), _vint(2, units), _vint(3, nanos))


def _usd(amount: str) -> bytes:
    return _money(amount, b"USD")


def _grpc_frame(message: bytes) -> bytes:
    return b"\x00" + len(message).to_bytes(4, "big") + message


# ---------------------------------------------------------------------------
# fake captured Auth request (the credential) + canned responses
# ---------------------------------------------------------------------------

FAKE_HASH = "A" * 64
FAKE_DEVICE_ID = "11111111-2222-3333-4444-555555555555"
FAKE_BLOB = b"\x99" * 40

# Auth request body: f1 = user-id hash, f2 = login blob, f3 = {f1: device id}
AUTH_REQUEST_BODY = _grpc_frame(
    _msg(
        _ld(1, FAKE_HASH.encode()),
        _ld(2, FAKE_BLOB),
        _ld(3, _msg(_ld(1, FAKE_DEVICE_ID.encode()))),
    )
)
AUTH_REQUEST_B64 = base64.b64encode(AUTH_REQUEST_BODY).decode()

# Auth response: f1 = {f1: 32-char session token}
FAKE_TOKEN = "T" * 32
AUTH_RESPONSE = _grpc_frame(_msg(_ld(1, _msg(_ld(1, FAKE_TOKEN.encode())))))

# GetInvestPlatformMainWidget: f1>f1>f1>f3 = portfolio value (Money, USD)
INVEST_RESPONSE = _grpc_frame(
    _ld(
        1,
        _ld(
            1,
            _ld(
                1,
                _msg(_ld(2, b"Inversiones"), _ld(3, _usd("258.98"))),
            ),
        ),
    )
)

# GetAccounts response: f1 = wrapper { repeated f1 = account envelopes, f2 = summary }
_CUENTA = _ld(
    4,  # wrap f4 -> debit Cuenta
    _msg(
        _ld(2, b"646100000000000001"),
        _ld(4, b"Cuenta"),
        _ld(9, _money("1234.56")),  # principal
        _ld(10, _money("5.44")),    # accrued interest -> 1240.00
    ),
)
_POCKET = _ld(
    6,  # wrap f6 -> named savings/investment pocket
    _msg(
        _ld(2, b"646100000000000002"),
        _ld(4, b"My Pocket"),
        _ld(7, _money("8000.00")),  # principal
        _ld(8, _money("250.75")),   # accrued interest -> 8250.75
    ),
)
_PRIMARY = _ld(
    1,  # wrap f1 -> primary Mastercard credit card (not reported)
    _msg(
        _ld(2, b"5280000000001234"),
        _ld(10, _money("10000.00")),
        _ld(12, _money("7000.00")),
    ),
)
_SUMMARY = _ld(2, _msg(_ld(2, _money("8000.00")), _ld(3, _money("3250.75"))))

ACCOUNTS_RESPONSE = _grpc_frame(
    _msg(
        _ld(
            1,
            _msg(
                _ld(1, _CUENTA),
                _ld(1, _POCKET),
                _ld(1, _PRIMARY),
                _SUMMARY,
            ),
        )
    )
)


def fake_api(accounts_body: bytes = ACCOUNTS_RESPONSE, auth_body: bytes = AUTH_RESPONSE):
    def handler(request: httpx.Request) -> httpx.Response:
        headers = {"content-type": "application/grpc"}
        if request.url.path.endswith("/Auth"):
            return httpx.Response(200, headers=headers, content=auth_body)
        if request.url.path.endswith("/GetAccounts"):
            return httpx.Response(200, headers=headers, content=accounts_body)
        if request.url.path.endswith("/GetInvestPlatformMainWidgets"):
            return httpx.Response(200, headers=headers, content=INVEST_RESPONSE)
        return httpx.Response(404)

    return httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://prime.platacard.mx"
    )


def build(**over):
    auth = {"auth_request": AUTH_REQUEST_B64, "installation_id": "inst-123"}
    auth.update(over.pop("auth", {}))
    # fixed FX rate so tests never touch the network
    over.setdefault("usd_mxn_rate", "18.00")
    return get_provider("plata")(client=over.pop("client", fake_api()), **over, **auth)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_requires_auth():
    with pytest.raises(AuthRequired):
        get_provider("plata")()


def test_reads_balances():
    balances = build().fetch_balances()
    by_name = {b.account: b for b in balances}

    # debit Cuenta -> checking, principal + accrued interest
    assert by_name["Cuenta"].kind is AccountKind.CHECKING
    assert by_name["Cuenta"].amount == Decimal("1240.00")
    assert by_name["Cuenta"].currency == "MXN"

    # named pocket -> investment, principal + accrued interest
    assert by_name["My Pocket"].kind is AccountKind.INVESTMENT
    assert by_name["My Pocket"].amount == Decimal("8250.75")

    # Plata Acciones brokerage -> investment, USD value converted to MXN
    acciones = by_name["Plata Acciones"]
    assert acciones.kind is AccountKind.INVESTMENT
    assert acciones.amount == Decimal("4661.64")  # 258.98 USD * 18.00
    assert acciones.currency == "MXN"


def test_acciones_failure_is_best_effort():
    # the brokerage widget rejects the call (empty response) -> core balances still returned, with a warning
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/Auth"):
            return httpx.Response(200, content=AUTH_RESPONSE)
        if request.url.path.endswith("/GetAccounts"):
            return httpx.Response(200, content=ACCOUNTS_RESPONSE)
        return httpx.Response(200, content=b"")  # invest widget rejected
    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://prime.platacard.mx"
    )
    with pytest.warns(UserWarning, match="Plata Acciones skipped"):
        balances = build(client=client).fetch_balances()
    names = [b.account for b in balances]
    assert "Plata Acciones" not in names
    assert "Cuenta" in names and len(balances) == 2  # Cuenta + pocket, no brokerage


def test_credit_card_not_reported():
    # the primary Mastercard (envelope f1) is intentionally left out.
    accounts = [b.account for b in build().fetch_balances()]
    assert "*1234" not in accounts
    # Cuenta + pocket + Plata Acciones
    assert len(accounts) == 3


def test_identity_read_from_auth_blob():
    # user-id hash and device id are taken from auth_request, not supplied directly.
    provider = build()
    assert provider._user_id_hash == FAKE_HASH
    assert provider._device_id == FAKE_DEVICE_ID


def test_bad_auth_blob_raises_layout_changed():
    with pytest.raises(LayoutChanged):
        build(auth={"auth_request": base64.b64encode(b"not-a-plata-blob").decode()})


def test_empty_auth_response_is_session_expired():
    # server answers Auth with only trailers (empty body) -> captured blob expired
    provider = build(client=fake_api(auth_body=b""))
    with pytest.raises(SessionExpired):
        provider.fetch_balances()


def test_rejected_token_is_session_expired():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/Auth"):
            return httpx.Response(200, content=AUTH_RESPONSE)
        return httpx.Response(401)

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://prime.platacard.mx"
    )
    with pytest.raises(SessionExpired):
        build(client=client).fetch_balances()
