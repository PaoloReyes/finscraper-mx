"""Klar provider, against a mocked Keycloak login + MFA-gated balances. All ids/amounts here are fake.

A real RSA keypair is generated per test run: the fake server encrypts each challenge with the public
key and the provider must decrypt it with the private key (the real PKCS#1 v1.5 path), so the handshake
is genuinely exercised, not stubbed.
"""
import base64
import json
from decimal import Decimal

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from finscraper_mx import AccountKind, AuthRequired, LayoutChanged, SessionExpired, get_provider

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
AUTH = {"private_key_pem": PRIVATE_PEM, "device_id": "fake-device-uuid", "username": "u@example.mx", "password": "pw"}

LOGIN_CODE = "100200"   # plaintext behind the login challenge
MFA_CODE = "300400"     # plaintext behind the per-request balance challenge


def enc(text: str) -> str:
    return base64.b64encode(_KEY.public_key().encrypt(text.encode(), padding.PKCS1v15())).decode()


def balances_body():
    def acct(id, type, units, nanos=0):
        return {"id": id, "type": type, "availableBalance": {"units": units, "nanos": nanos, "currencyCode": "MXN"}}
    return {"balances": [
        acct("a1", "DEBIT", "200000"),
        acct("a2", "SAVINGS", "15020", 400_000_000),
        acct("a3", "SECURED_DEPOSIT", "0"),
        acct("a4", "CREDIT", "3491", 20_000_000),      # excluded (credit line, not a holding)
        acct("a5", "DEBIT_PENDING", "50"),             # excluded (unsettled)
    ]}


def fake_app(*, login_status=200, pairing=False, bad_password=False, balances=None, seen=None):
    balances = balances_body() if balances is None else balances

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "gw.klar.mx" and request.url.path == "/api/v2/auth/logout":
            if seen is not None:
                seen["logged_out"] = True
            return httpx.Response(200, json={})
        if request.url.path == "/login/api/auth/v1/login":
            if login_status == 423:
                return httpx.Response(423, json={"status": 423, "error": "Locked", "message": "session already exists"})
            if bad_password:
                return httpx.Response(401, json={"message": "invalid credentials"})
            body = json.loads(request.content)
            creds = body["credentials"]
            if "mfaAccept" in creds:  # step 1
                if pairing:
                    return httpx.Response(412, json={"mfa": {"status": "MFA_PAIRING_REQUIRED"}})
                return httpx.Response(200, json={"token": {"accessToken": "provisional"},
                                                 "mfa": {"mfaChallengeId": "cid-1", "mfaChallenge": enc(LOGIN_CODE)}})
            # step 2: must echo the decrypted login code
            assert creds["mfa"]["mfaResponse"] == LOGIN_CODE
            return httpx.Response(200, json={"token": {"accessToken": "mfa-token", "refreshToken": "r"}})

        if request.url.path == "/balances2/api/v0/user":
            assert request.headers.get("authorization") == "Bearer mfa-token"
            if "mfa-response" not in request.headers:  # first hit -> 307 challenge
                return httpx.Response(307, headers={"mfa-challenge": enc(MFA_CODE), "mfa-challenge-id": "mc-1"})
            assert request.headers["mfa-response"] == MFA_CODE  # provider decrypted and echoed it
            return httpx.Response(200, json=balances)

        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.klar.mx")


def provider(client=None, **overrides):
    return get_provider("klar")(client=client or fake_app(), **{**AUTH, **overrides})


def test_requires_auth():
    with pytest.raises(AuthRequired):
        get_provider("klar")(private_key_pem=PRIVATE_PEM, device_id="d")  # username/password missing


def test_invalid_private_key_is_auth_required():
    with pytest.raises(AuthRequired):
        get_provider("klar")(client=fake_app(), **{**AUTH, "private_key_pem": "not a key"})


def test_private_key_on_one_line_with_escaped_newlines():
    one_line = PRIVATE_PEM.strip().replace("\n", "\\n")
    assert "\n" not in one_line
    assert len(provider(private_key_pem=one_line).fetch_balances()) == 3


def test_non_json_reply_names_the_call_and_status():
    def handler(request):
        return httpx.Response(403, text="<html>Request blocked</html>", headers={"content-type": "text/html"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.klar.mx")
    with pytest.raises(LayoutChanged, match=r"POST /login/api/auth/v1/login, got HTTP 403 'text/html'.*Request blocked"):
        provider(client).fetch_balances()


def test_reads_non_credit_balances_in_mxn():
    balances = {b.account: b for b in provider().fetch_balances()}
    assert set(balances) == {"Cuenta", "Ahorro", "Depósito en garantía"}  # CREDIT + DEBIT_PENDING excluded
    assert balances["Cuenta"].amount == Decimal("200000.00")
    assert balances["Cuenta"].kind is AccountKind.CHECKING
    assert balances["Ahorro"].amount == Decimal("15020.40")  # units + nanos
    assert balances["Ahorro"].kind is AccountKind.SAVINGS
    assert balances["Depósito en garantía"].kind is AccountKind.INVESTMENT
    assert all(b.currency == "MXN" and b.institution == "klar" for b in balances.values())


def test_logs_out_after_reading_to_release_the_session():
    seen = {}
    provider(fake_app(seen=seen)).fetch_balances()
    assert seen.get("logged_out") is True


def test_session_already_exists_is_session_expired():
    with pytest.raises(SessionExpired, match="one at a time"):
        provider(fake_app(login_status=423)).fetch_balances()


def test_pairing_required_is_session_expired():
    with pytest.raises(SessionExpired, match="trusted device"):
        provider(fake_app(pairing=True)).fetch_balances()


def test_wrong_password_is_session_expired():
    with pytest.raises(SessionExpired, match="password"):
        provider(fake_app(bad_password=True)).fetch_balances()


def test_no_balance_accounts_is_layout_changed():
    only_excluded = {"balances": [
        {"id": "a4", "type": "CREDIT", "availableBalance": {"units": "100", "nanos": 0, "currencyCode": "MXN"}}]}
    with pytest.raises(LayoutChanged):
        provider(fake_app(balances=only_excluded)).fetch_balances()


def test_missing_balances_list_is_layout_changed():
    with pytest.raises(LayoutChanged):
        provider(fake_app(balances={"nope": []})).fetch_balances()
