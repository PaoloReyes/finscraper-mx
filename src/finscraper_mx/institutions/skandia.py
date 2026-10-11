"""Skandia México — corporate savings plan (Fondo de Ahorro), through SkandiaNet.

Read-only. Logs in automatically by OCR-solving the arithmetic captcha rendered on the login page
(dark text on a transparent background: "Resuelve la operación / 4 + 79").

Requires the ``tesseract-ocr`` system binary (5.5 or newer) in addition to the Python dependencies:
- macOS: ``brew install tesseract``
- Debian/Ubuntu: ``apt-get install tesseract-ocr``

Auth fields
-----------
username
    CURP (18 characters), e.g. ``"XEXX010101HNEXXXA4"``.
password
    6-digit numeric PIN.

Flow
----
1. POST CURP to ``Login1.aspx`` → receives the page with captcha PNG embedded as base64.
2. OCR the PNG to read the arithmetic problem and compute the answer. When the readings disagree,
   fetch a new captcha (step 1 again, up to 3 times) instead of guessing: a wrong answer is a
   failed login.
3. POST ``LoadUser`` (empty body; session cookie in jar from step 1).
4. POST ``ValidateLogin`` with password + captcha answer → returns ``PartId``, ``PlanNum``.
5. POST ``partinfo.aspx`` form to bind the plan to the session.
6. POST ``UpdateUserSession``.
7. POST ``LoadPage`` → balance data (double-encoded JSON envelope).

The ASP.NET session expires after 30 idle minutes; ``fetch_balances()`` performs a fresh login
on every call so the session is always valid.

Not affiliated with Skandia México. Covers only the corporate Fondo de Ahorro portal.
Automated access may be against Skandia's terms of use; use only with your own account.
"""
from __future__ import annotations

import io
import json
import re
from decimal import Decimal, InvalidOperation

import httpx
import pytesseract
from PIL import Image, ImageOps

from ..core import AccountKind, AuthRequired, Balance, FinscraperError, LayoutChanged, Provider, SessionExpired

BASE_URL = "https://www.skandia.com.mx/SkandiaNet"
ENTRY_URL = f"{BASE_URL}/OM.Mx.SecurityAccess/Login1.aspx"

MIN_TESSERACT = (5, 5)  # older builds misread the captcha (5.3.4 does); keep in sync with the gate
CAPTCHA_TRIES = 3  # fresh captchas to try when one can't be read; reading costs no login attempt
CENT = Decimal("0.01")
_AMOUNT_RE = re.compile(r"[\$,]")

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/154.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_amount(raw: str) -> Decimal:
    cleaned = _AMOUNT_RE.sub("", raw.strip())
    try:
        return Decimal(cleaned).quantize(CENT)
    except InvalidOperation:
        raise LayoutChanged(f"skandia: cannot parse amount {raw!r}")


def _require_tesseract() -> None:
    """Fail fast unless the tesseract binary is installed and new enough to read the captcha.

    A misread captcha counts as a failed login, and Skandia locks the account after 3.
    """
    want = ".".join(map(str, MIN_TESSERACT))
    try:
        version = pytesseract.get_tesseract_version()
    except pytesseract.TesseractNotFoundError as exc:
        raise FinscraperError(f"skandia: tesseract {want}+ is required but was not found on PATH") from exc
    if tuple(version.release[:2]) < MIN_TESSERACT:
        raise FinscraperError(f"skandia: tesseract {want}+ is required, found {version}")


def _unwrap(response: httpx.Response, *, allow_empty: bool = False) -> dict:
    """Unwrap Skandia's double-encoded ``{"d": "<JSON string>"}`` envelope."""
    if response.status_code in (401, 403):
        raise SessionExpired("skandia: server rejected the request (401/403)")
    response.raise_for_status()
    try:
        body = response.content.decode("utf-8")
    except UnicodeDecodeError:
        body = response.content.decode("cp1252")  # some pages come back Latin-1 (e.g. "ó" as 0xf3)
    try:
        outer = json.loads(body)
    except json.JSONDecodeError as exc:
        raise LayoutChanged(f"skandia: response is not JSON: {body[:120]!r}") from exc
    if "d" not in outer:
        raise LayoutChanged(f"skandia: response missing 'd' key: {body[:120]}")
    inner_raw = outer["d"]
    if allow_empty and inner_raw in ("{}", ""):
        return {}
    if isinstance(inner_raw, str):
        try:
            return json.loads(inner_raw)
        except json.JSONDecodeError as exc:
            raise LayoutChanged(f"skandia: inner 'd' is not valid JSON: {exc}") from exc
    return inner_raw  # already a dict (future-proofing)


class _UnreadableCaptcha(LayoutChanged):
    """OCR could not read the captcha with confidence. Nothing was submitted, so it costs no login attempt."""


_LINE_HEIGHTS = (20, 24, 28)  # the expression line is OCR'd at each height; all readings must agree
_EXPRESSION_RE = re.compile(r"^(\d{1,2})([+\-])(\d{1,2})$")  # operands are 1-2 digits


def _text_lines(img: Image.Image) -> list[tuple[int, int]]:
    """(top, bottom) row ranges that contain dark pixels, top to bottom."""
    width = img.width
    pixels = img.load()
    lines, top = [], None
    for y in range(img.height + 1):
        dark = y < img.height and any(pixels[x, y] < 128 for x in range(width))
        if dark and top is None:
            top = y
        elif not dark and top is not None:
            lines.append((top, y))
            top = None
    return lines


def _read_expression(line: Image.Image, height: int) -> str:
    scale = height / line.height
    img = line.resize((max(1, round(line.width * scale)), height), Image.LANCZOS)
    img = ImageOps.expand(img, 10, fill=255)  # tesseract needs a margin around the text
    text = pytesseract.image_to_string(img, config="--psm 7 -c tessedit_char_whitelist=0123456789+-")
    return re.sub(r"\s+", "", text)


def _ocr_captcha(png_b64: str) -> str:
    """OCR the arithmetic captcha PNG and return the numeric answer as a string.

    The image is dark text on a transparent background: "Resuelve la operación" above, e.g. "4 + 79"
    below. It is flattened onto white, the last text line (the expression) is cropped out and read
    at a few sizes; the answer is only returned when every reading agrees, otherwise
    `_UnreadableCaptcha` is raised so the caller can ask for a new captcha instead of guessing
    (a wrong answer is a failed login, and Skandia locks the account after 3).
    """
    import base64

    raw = base64.b64decode(png_b64)
    img = Image.open(io.BytesIO(raw)).convert("RGBA")
    img = Image.alpha_composite(Image.new("RGBA", img.size, "white"), img).convert("L")
    lines = _text_lines(img)
    if not lines:
        raise _UnreadableCaptcha("skandia: could not parse captcha: the image has no text")
    top, bottom = lines[-1]
    line = img.crop((0, top, img.width, bottom))

    readings = {_read_expression(line, height) for height in _LINE_HEIGHTS}
    m = _EXPRESSION_RE.match(next(iter(readings))) if len(readings) == 1 else None
    if not m:
        raise _UnreadableCaptcha(
            f"skandia: could not parse captcha from OCR output {sorted(readings)!r}; "
            "the captcha image format may have changed"
        )
    a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
    return str(a + b if op == "+" else a - b)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class Skandia(Provider):
    """Reads the Fondo de Ahorro balance from SkandiaNet via automated OCR login."""

    id = "skandia"
    name = "Skandia México"
    auth_fields = ("username", "password")

    def __init__(self, *, client: httpx.Client | None = None, **auth: str) -> None:
        super().__init__(**auth)
        _require_tesseract()
        self._client = client or httpx.Client(
            headers={"User-Agent": _UA},
            timeout=30,
            follow_redirects=True,
        )

    def fetch_balances(self) -> list[Balance]:
        part_id, plan_id = self._login()
        return self._load_balances(part_id, plan_id)

    # ------------------------------------------------------------------
    # Login steps (private)
    # ------------------------------------------------------------------

    def _login(self) -> tuple[str, str]:
        """Full login flow. Returns (part_id, plan_id)."""
        for attempt in range(CAPTCHA_TRIES):
            html = self._post_entry_form()  # each post renders a new captcha
            try:
                captcha_answer = _ocr_captcha(self._extract_captcha_b64(html))
                break
            except _UnreadableCaptcha:
                if attempt == CAPTCHA_TRIES - 1:
                    raise
        self._load_user()
        login_data = self._validate_login(captcha_answer)

        part_id = login_data.get("PartId", "")
        plan_id = login_data.get("PlanNum", "") or login_data.get("PlanName", "")
        if not part_id or not plan_id:
            raise LayoutChanged(
                f"skandia: ValidateLogin did not return PartId/PlanNum: {list(login_data)}"
            )

        self._post_partinfo_form(login_data)
        self._update_session(login_data.get("UserName", self.auth["username"]), plan_id)
        return part_id, plan_id

    def _post_entry_form(self) -> str:
        resp = self._client.post(
            ENTRY_URL,
            content=f"user={self.auth['username']}&redirect=0".encode(),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://skandiamx.com",
                "Referer": "https://skandiamx.com/",
            },
        )
        if resp.status_code != 200 or "pnlCaptcha" not in resp.text:
            raise LayoutChanged(
                f"skandia: Login1.aspx did not return the captcha page "
                f"(status {resp.status_code})"
            )
        return resp.text

    def _extract_captcha_b64(self, html: str) -> str:
        m = re.search(
            r'id="Captcha1_imgOperation"[^>]*src="data:image/png;base64,([^"]+)"', html
        )
        if not m:
            raise LayoutChanged("skandia: captcha image not found in Login1.aspx")
        return m.group(1).strip()

    def _load_user(self) -> None:
        resp = self._client.post(
            f"{BASE_URL}/OM.Mx.SecurityAccess/Login1.aspx/LoadUser",
            content=b"",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": ENTRY_URL,
            },
        )
        _unwrap(resp)

    def _validate_login(self, captcha_answer: str) -> dict:
        payload = json.dumps({"Password": self.auth["password"], "Captcha": captcha_answer})
        body = f"{{'request':'{payload}'}}"
        resp = self._client.post(
            f"{BASE_URL}/OM.Mx.SecurityAccess/Login1.aspx/ValidateLogin",
            content=body.encode(),
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": ENTRY_URL,
            },
        )
        data = _unwrap(resp)
        result = data.get("Result", {})
        id_msg = result.get("IdMessage", -1)

        if id_msg == 20:
            attempts = data.get("NumberOfAttemps", "?")
            raise AuthRequired(
                f"skandia: wrong password or captcha ({attempts} failed attempt(s)); "
                "check credentials or the OCR captcha solver"
            )
        if id_msg not in (0, 1, 2):
            msg = result.get("MessageError") or f"IdMessage={id_msg}"
            raise SessionExpired(f"skandia: login rejected — {msg}")

        return data

    def _post_partinfo_form(self, login_data: dict) -> None:
        fields = {
            "PlanNum": login_data.get("PlanNum", ""),
            "PartId": login_data.get("PartId", ""),
            "UserName": login_data.get("UserName", ""),
            "UserPass": login_data.get("UserPass", ""),
            "firstloginnws": login_data.get("firstloginnws", "0"),
            "UserGroupNum": login_data.get("UserGroupNum", ""),
            "PerfilId": login_data.get("PerfilId", ""),
            "Timeout": login_data.get("Timeout", "30"),
        }
        body = "&".join(f"{k}={v}" for k, v in fields.items())
        self._client.post(
            f"{BASE_URL}/partinfo.aspx",
            content=body.encode(),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": ENTRY_URL,
            },
        )

    def _update_session(self, username: str, plan_id: str) -> None:
        payload = json.dumps({"UserName": username, "PlanNum": plan_id})
        body = f"{{'req':'{payload}'}}"
        resp = self._client.post(
            f"{BASE_URL}/OM.Mx.SecurityAccess/UpdateSession.aspx/UpdateUserSession",
            content=body.encode(),
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{BASE_URL}/partinfo.aspx",
            },
        )
        _unwrap(resp, allow_empty=True)

    # ------------------------------------------------------------------
    # Balance read (private)
    # ------------------------------------------------------------------

    def _load_balances(self, part_id: str, plan_id: str) -> list[Balance]:
        body = f"{{'partid':'{part_id}', 'planid':'{plan_id}'}}"
        resp = self._client.post(
            f"{BASE_URL}/partinfo.aspx/LoadPage",
            content=body.encode(),
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{BASE_URL}/partinfo.aspx",
            },
        )
        data = _unwrap(resp)

        if "ReturPage" in data or "Result" in data:
            raise SessionExpired("skandia: LoadPage returned a login redirect; session lost")

        features = data.get("features")
        if not isinstance(features, dict):
            raise LayoutChanged("skandia: 'features' key missing or not a dict")

        products = features.get("Products")
        if not isinstance(products, dict):
            raise LayoutChanged("skandia: 'features.Products' missing or not a dict")

        detail = products.get("Detail")
        if not isinstance(detail, list) or not detail:
            raise LayoutChanged("skandia: 'features.Products.Detail' is empty or missing")

        balances: list[Balance] = []
        for row in detail:
            if not isinstance(row, list) or len(row) < 3:
                raise LayoutChanged(f"skandia: unexpected Products.Detail row shape: {row!r}")
            _, name, amount_str = row[:3]
            balances.append(
                Balance(
                    institution=self.id,
                    account=str(name).strip(),
                    kind=AccountKind.INVESTMENT,
                    amount=_parse_amount(str(amount_str)),
                    currency="MXN",
                )
            )
        return balances
