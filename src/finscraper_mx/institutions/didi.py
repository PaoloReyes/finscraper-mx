"""DiDi Cuenta (DiDi's SoFiPo wallet in Mexico), through the signed-in mobile app. Read-only.

DiDi has no public wallet API, so this reads what the app's SoFiPo home screen shows.
`POST https://wallet.didipay.com/sofipo/uchome/v0/main` returns the home layout as JSON; its
`data.content.cardList` has one card per balance group — `sellingPoint` carries the available savings
balance (and total yield), `termDeposit` carries fixed-term deposits — each already valued in MXN.

The app signs every request with a per-request `wsgsig`/`secdd` signature computed natively on the
device over the exact query string and body. That signature can't be reproduced or re-forged off-device,
and it is request-bound: a signature captured for one request only validates that same request. So the
provider never logs in and never rebuilds the request — it **replays the captured signed request
verbatim**. The caller captures their own signed-in session (e.g. a mitmproxy capture of the app) and
passes the pieces it yields: the session `token`, the exact captured query string (`signed_query`) and
the `wsgsig`, plus the `secdd_authentication`, `secdd_challenge`, `oid` and `rid` the app sent with it.
`sofi_hint`, `didi_hint` and `body` may be overridden if the capture differs from the defaults.

Because the signature is request-bound and short-lived, the capture works only until DiDi expires the
session or the signature; recapture when a read raises `SessionExpired`. Reverse-engineered and
unofficial: not affiliated with DiDi, automated access may be against its terms, and the token gives
access to the wallet, so keep it private.
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

import httpx

from ..core import AccountKind, Balance, LayoutChanged, Provider, SessionExpired

BASE_URL = "https://wallet.didipay.com"
PATH = "/sofipo/uchome/v0/main"
# App constants (not credentials), overridable; mirror a recent signed-in iOS app so the read looks normal.
USER_AGENT = "GlobalPax/7.7.0 (iPhone; iOS 27.0.1; Scale/3.00)"
APP_VERSION = "7.7.0"
PRODUCT_ID = "30008"
SOFI_APP_ID = 200062
DEFAULT_BODY = {"bioFlag": 0, "sofipo_sdk_version": APP_VERSION, "maptype": "wgs84", "verifySdkVersion": "1.7", "bulletinId": ""}
# cardType -> (kind, the card's amount field). Other card types (cnbv header, banners, faq) carry no balance.
CARD_KINDS = {
    "sellingPoint": (AccountKind.SAVINGS, "balance"),     # Saldo (MXN$): available savings
    "termDeposit": (AccountKind.INVESTMENT, "amount"),    # Mis Ahorros (MXN$): fixed-term deposits
}
# Captured per-request signing values, sent verbatim. The wallet generates these natively over the exact
# request; the library cannot, so the caller provides them from the same capture as the token.
SIGNING_HEADERS = {
    "wsgsig": "wsgsig",
    "secdd-authentication": "secdd_authentication",
    "secdd-challenge": "secdd_challenge",
    "oid": "oid",
    "didi-header-rid": "rid",
}


class Didi(Provider):
    id = "didi"
    name = "DiDi Cuenta"
    # token + the captured query and signature that the app signed it with; signatures are request-bound.
    auth_fields = ("token", "signed_query", "wsgsig")

    def __init__(
        self,
        *,
        user_agent: str = USER_AGENT,
        client: httpx.Client | None = None,
        base_url: str = BASE_URL,
        **auth: str,
    ) -> None:
        super().__init__(**auth)
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=30)
        self._headers = {
            "User-Agent": user_agent,
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Accept-Language": "es-MX;q=1",
            "wsgbizsource": "sofi",
            "tripcountry": "MX",
            # Captured verbatim when supplied; otherwise rebuilt compactly from the token.
            "sofi-header-hint-content": self.auth.get("sofi_hint")
            or json.dumps({"appid": SOFI_APP_ID, "location_country": "MX", "token": self.auth["token"]}, separators=(",", ":")),
            "didi-header-hint-content": self.auth.get("didi_hint")
            or json.dumps({"product_id": PRODUCT_ID, "appVersion": APP_VERSION, "lang": "es-MX", "locale": "es_MX",
                           "lCountry": "MX", "xregionkeyname": "deal_country", "xregionkeyvalue": "MX"}, separators=(",", ":")),
        }
        for header, key in SIGNING_HEADERS.items():
            if self.auth.get(key):
                self._headers[header] = self.auth[key]

    def fetch_balances(self) -> list[Balance]:
        data = self._home().get("data") or {}
        cards = (data.get("content") or {}).get("cardList") or []
        if not cards:
            # errno 0 but empty body: the signed request was not accepted (stale or mismatched signature).
            raise SessionExpired("didi: empty SoFiPo home — the captured request signature was not accepted; recapture the signed request")
        balances = []
        for card in cards:
            spec = CARD_KINDS.get(card.get("cardType"))
            if spec is None:
                continue  # header, banners, faq: no balance
            kind, field = spec
            if field not in card:
                continue
            balances.append(Balance(self.id, _label(card), kind, _amount(card[field], card.get("cardType")), "MXN"))
        if not balances:
            raise LayoutChanged("didi: no balance cards in the SoFiPo home; the app's layout changed")
        return balances

    def _home(self) -> dict:
        # Replay the captured request verbatim: the wsgsig signature is bound to this exact query and body.
        url = f"{PATH}?{self.auth['signed_query'].lstrip('?')}"
        body = self.auth.get("body")
        content = body.encode() if isinstance(body, str) else None
        response = (
            self._client.post(url, headers=self._headers, content=content)
            if content is not None
            else self._client.post(url, headers=self._headers, json=DEFAULT_BODY)
        )
        if response.status_code in (401, 403):
            raise SessionExpired("didi: token rejected; recapture a fresh signed request from your signed-in app")
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError:
            raise LayoutChanged("didi: home did not return JSON; the app's API changed") from None
        # DiDi returns HTTP 200 with a logical errno; a non-zero errno usually means the session lapsed.
        if payload.get("errno"):
            raise SessionExpired(f"didi: {payload.get('errmsg') or payload.get('errno')}; recapture a fresh signed request")
        return payload


def _label(card: dict) -> str:
    title = card.get("title")
    if isinstance(title, dict):
        title = title.get("text")
    text = str(title or card.get("cardType") or "?").strip()
    return text.replace(" (MXN$)", "").replace("(MXN$)", "").strip() or "?"


def _amount(raw: object, card_type: str | None) -> Decimal:
    # Amounts arrive as display strings, e.g. "10,058.37" or "+1,956.99".
    cleaned = str(raw).replace(",", "").replace("$", "").replace("+", "").replace("MXN", "").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        raise LayoutChanged(f"didi: unreadable amount for {card_type!r}; the app's API changed") from None
