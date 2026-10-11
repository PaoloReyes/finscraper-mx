"""Skandia México provider tests. All ids, amounts and credentials are fake."""
from __future__ import annotations

import base64
import io
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageDraw

from finscraper_mx import AuthRequired, FinscraperError, get_provider
from finscraper_mx.core import AccountKind, LayoutChanged, SessionExpired
from finscraper_mx.institutions.skandia import Skandia, _ocr_captcha, _UnreadableCaptcha

# ---------------------------------------------------------------------------
# Fake data (nothing real)
# ---------------------------------------------------------------------------

FAKE_USERNAME = "XEXX010101HNEXXXA4"  # generic CURP
FAKE_PASSWORD = "123456"
FAKE_PART_ID = "000000001"
FAKE_PLAN_ID = "X00001"
FAKE_BALANCE = "$50,000.00"

# Preset captcha answer used by the mock — avoids calling real tesseract in flow tests
FAKE_CAPTCHA_ANSWER = "83"


def _make_captcha_png(text: str = "4 + 79") -> str:
    """Return a base64-encoded PNG with the given arithmetic text."""
    img = Image.new("RGBA", (250, 80), "white")
    draw = ImageDraw.Draw(img)
    draw.text((10, 25), text, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _make_login_html(captcha_b64: str) -> str:
    return (
        '<div id="pnlCaptcha">'
        f'<img id="Captcha1_imgOperation" src="data:image/png;base64,{captcha_b64}" />'
        '<input id="Captcha1_publicKey" value="fakekey==" />'
        '</div>'
    )


def _d(obj: dict) -> dict:
    """Wrap in Skandia's double-encoded envelope."""
    return {"d": json.dumps(obj)}


LOAD_USER_RESP = _d({
    "UserName": FAKE_USERNAME, "MessageError": None, "IdMessage": 0,
    "ReturPage": None, "LentPass": 6, "TypeChars": "N",
    "PlanNum": FAKE_PLAN_ID, "IsNewPortal": False,
})

VALIDATE_LOGIN_RESP = _d({
    "Result": {"IdMessage": 0, "ReturPage": "partinfo.aspx", "MessageError": None},
    "PlanNum": FAKE_PLAN_ID, "PlanName": FAKE_PLAN_ID,
    "PartId": FAKE_PART_ID, "UserName": FAKE_USERNAME,
    "UserPass": "", "firstloginnws": "0", "UserGroupNum": "001",
    "PerfilId": "3", "Timeout": "30", "NumberOfAttemps": 0,
})

UPDATE_SESSION_RESP = {"d": "{}"}

LOAD_PAGE_RESP = _d({
    "features": {
        "Products": {
            "Detail": [["S,T", "FONDO DE AHORRO", FAKE_BALANCE]],
            "GranTotal": FAKE_BALANCE,
            "dspTotal": True,
        },
        "Layout": [],
        "Insurance": [],
        "Loans": [],
    }
})


# ---------------------------------------------------------------------------
# Mock transport — mocks out the full HTTP layer
# ---------------------------------------------------------------------------

def fake_client(
    *,
    validate_resp: dict | None = None,
    load_page_resp: dict | None = None,
    load_page_content: bytes | None = None,
) -> httpx.Client:
    captcha_b64 = _make_captcha_png()
    login_html = _make_login_html(captcha_b64)
    vresp = validate_resp if validate_resp is not None else VALIDATE_LOGIN_RESP
    lpresp = load_page_resp if load_page_resp is not None else LOAD_PAGE_RESP

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path

        if path.endswith("Login1.aspx") and b"user=" in (request.content or b""):
            return httpx.Response(200, text=login_html, headers={"Content-Type": "text/html"})
        if path.endswith("Login1.aspx/LoadUser"):
            return httpx.Response(200, json=LOAD_USER_RESP)
        if path.endswith("Login1.aspx/ValidateLogin"):
            return httpx.Response(200, json=vresp)
        if path.endswith("partinfo.aspx") and request.method == "POST" and b"PlanNum=" in (request.content or b""):
            return httpx.Response(200, text="<html></html>")
        if path.endswith("UpdateUserSession"):
            return httpx.Response(200, json=UPDATE_SESSION_RESP)
        if path.endswith("LoadPage"):
            if load_page_content is not None:
                return httpx.Response(200, content=load_page_content, headers={"Content-Type": "application/json"})
            return httpx.Response(200, json=lpresp)
        return httpx.Response(404, text="not found")

    return httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://www.skandia.com.mx",
        follow_redirects=True,
        headers={"User-Agent": "test"},
    )


def provider(**kwargs) -> Skandia:
    """Build a Skandia provider with a mocked HTTP client and mocked OCR."""
    return Skandia(
        username=FAKE_USERNAME,
        password=FAKE_PASSWORD,
        client=fake_client(**kwargs),
    )


# ---------------------------------------------------------------------------
# Flow tests — OCR is mocked, never calls tesseract binary
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_ocr(monkeypatch):
    """Patch _ocr_captcha so flow tests never need the tesseract binary installed."""
    monkeypatch.setattr(
        "finscraper_mx.institutions.skandia._ocr_captcha",
        lambda b64: FAKE_CAPTCHA_ANSWER,
    )


def test_missing_tesseract_is_rejected(monkeypatch):
    import pytesseract

    def not_found():
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr("pytesseract.get_tesseract_version", not_found)
    with pytest.raises(FinscraperError, match="not found on PATH"):
        provider()


def test_requires_username_and_password():
    with pytest.raises(AuthRequired):
        get_provider("skandia")()


def test_requires_password():
    with pytest.raises(AuthRequired):
        get_provider("skandia")(username=FAKE_USERNAME)


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
    resp = _d({
        "features": {
            "Products": {
                "Detail": [
                    ["S", "APORTACION EMPLEADO", "$25,000.00"],
                    ["T", "APORTACION EMPRESA", "$25,000.00"],
                ],
                "GranTotal": "$50,000.00",
                "dspTotal": True,
            },
            "Layout": [],
        }
    })
    balances = provider(load_page_resp=resp).fetch_balances()
    assert len(balances) == 2
    names = {b.account for b in balances}
    assert names == {"APORTACION EMPLEADO", "APORTACION EMPRESA"}
    assert sum(b.amount for b in balances) == Decimal("50000.00")


def test_wrong_password_raises_auth_required():
    bad_validate = _d({
        "Result": {"IdMessage": 20, "MessageError": "Contraseña incorrecta", "ReturPage": None},
        "NumberOfAttemps": 1,
    })
    with pytest.raises(AuthRequired, match="wrong password or captcha"):
        provider(validate_resp=bad_validate).fetch_balances()


def test_account_locked_raises_session_expired():
    locked = _d({
        "Result": {"IdMessage": 19, "MessageError": "Cuenta bloqueada", "ReturPage": None},
        "NumberOfAttemps": 3,
    })
    with pytest.raises(SessionExpired, match="login rejected"):
        provider(validate_resp=locked).fetch_balances()


def test_missing_features_raises_layout_changed():
    bad_page = _d({"SomethingElse": True})
    with pytest.raises(LayoutChanged, match="'features'"):
        provider(load_page_resp=bad_page).fetch_balances()


def test_empty_detail_raises_layout_changed():
    bad_page = _d({"features": {"Products": {"Detail": [], "GranTotal": "$0.00"}}})
    with pytest.raises(LayoutChanged, match="Detail"):
        provider(load_page_resp=bad_page).fetch_balances()


def test_reads_latin1_response():
    """LoadPage can arrive Latin-1 encoded (accented text), not UTF-8."""
    inner = {
        "features": {"Products": {"Detail": [["S,T", "FONDO DE AHORRO", FAKE_BALANCE]]}},
        "Messages": "Valuación",
    }
    outer = {"d": json.dumps(inner, ensure_ascii=False)}
    balances = provider(load_page_content=json.dumps(outer, ensure_ascii=False).encode("cp1252")).fetch_balances()
    assert balances[0].amount == Decimal("50000.00")


def test_bad_amount_raises_layout_changed():
    bad_page = _d({
        "features": {"Products": {"Detail": [["S", "FONDO", "not-a-number"]]}}
    })
    with pytest.raises(LayoutChanged, match="cannot parse amount"):
        provider(load_page_resp=bad_page).fetch_balances()


def test_server_401_raises_session_expired():
    captcha_b64 = _make_captcha_png()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("Login1.aspx") and b"user=" in (request.content or b""):
            return httpx.Response(200, text=_make_login_html(captcha_b64))
        if path.endswith("Login1.aspx/LoadUser"):
            return httpx.Response(200, json=LOAD_USER_RESP)
        if path.endswith("Login1.aspx/ValidateLogin"):
            return httpx.Response(401)
        return httpx.Response(200, json=UPDATE_SESSION_RESP)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://www.skandia.com.mx",
        follow_redirects=True,
    )
    with pytest.raises(SessionExpired):
        Skandia(username=FAKE_USERNAME, password=FAKE_PASSWORD, client=client).fetch_balances()


# ---------------------------------------------------------------------------
# OCR unit tests — run the real tesseract binary (a declared requirement)
# The .b64 fixture is the actual captcha PNG extracted from a real Skandia
# session (sanitized: HAR gitignored, only the PNG/b64 are committed).
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_ocr_real_captcha_image(monkeypatch):
    """Real Skandia captcha PNGs fetched from the live server — exercises the full OCR path."""
    monkeypatch.undo()
    cases = [
        ("skandia_captcha.b64",   "83"),   # 4 + 79  (from HAR)
        ("skandia_captcha_2.b64", "57"),   # 1 + 56
        ("skandia_captcha_3.b64", "3"),    # 1 + 2
        ("skandia_captcha_4.b64", "28"),   # 8 + 20
        ("skandia_captcha_5.b64", "77"),   # 73 + 4
    ]
    for filename, expected in cases:
        b64 = (FIXTURES_DIR / filename).read_text().strip()
        assert _ocr_captcha(b64) == expected, f"{filename}: expected {expected}"


def test_ocr_captcha_addition(monkeypatch):
    monkeypatch.undo()
    b64 = _make_captcha_png("4 + 79")
    assert _ocr_captcha(b64) == "83"


def test_ocr_captcha_subtraction(monkeypatch):
    monkeypatch.undo()
    b64 = _make_captcha_png("90 - 10")
    assert _ocr_captcha(b64) == "80"


def test_ocr_reads_expression_on_transparent_background(monkeypatch):
    """Real captchas are dark text on a fully transparent background; "73 + 4" was misread before."""
    monkeypatch.undo()
    b64 = (FIXTURES_DIR / "skandia_captcha_5.b64").read_text().strip()
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert img.mode == "RGBA" and img.getpixel((0, 0))[3] == 0
    assert _ocr_captcha(b64) == "77"


def test_ocr_disagreeing_readings_are_not_guessed(monkeypatch):
    monkeypatch.undo()
    readings = iter(["73+4", "13+4", "73+4"])
    monkeypatch.setattr("finscraper_mx.institutions.skandia._read_expression", lambda line, height: next(readings))
    with pytest.raises(LayoutChanged, match="could not parse captcha"):
        _ocr_captcha(_make_captcha_png("73 + 4"))


def test_ocr_unreadable_raises_layout_changed(monkeypatch):
    monkeypatch.undo()
    img = Image.new("RGBA", (250, 80), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    with pytest.raises(LayoutChanged, match="could not parse captcha"):
        _ocr_captcha(b64)


def test_unreadable_captcha_fetches_a_new_one(monkeypatch):
    answers = iter([_UnreadableCaptcha("skandia: could not parse captcha"), FAKE_CAPTCHA_ANSWER])

    def ocr(b64):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr("finscraper_mx.institutions.skandia._ocr_captcha", ocr)
    balances = provider().fetch_balances()
    assert balances[0].amount == Decimal("50000.00")


def test_three_unreadable_captchas_never_submit_a_login(monkeypatch):
    def ocr(b64):
        raise _UnreadableCaptcha("skandia: could not parse captcha")

    monkeypatch.setattr("finscraper_mx.institutions.skandia._ocr_captcha", ocr)
    requests = []
    client = fake_client()
    client.event_hooks["request"].append(requests.append)
    with pytest.raises(LayoutChanged):
        Skandia(username=FAKE_USERNAME, password=FAKE_PASSWORD, client=client).fetch_balances()
    paths = [r.url.path for r in requests]
    assert sum(p.endswith("Login1.aspx") for p in paths) == 3
    assert not any(p.endswith("ValidateLogin") for p in paths)
