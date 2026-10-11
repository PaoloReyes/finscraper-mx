"""Mercado Pago México, through the signed-in web app (www.mercadopago.com.mx). Read-only.

Mercado Pago's developer API only covers payments, so this reads what the web home page shows. The
home page is server-rendered: its `__NORDIC_RENDERING_CTX__` script carries the page state, and the
`BANKING` widget in it has one tab per balance (Saldo, Apartados, Criptomonedas), each already in MXN.

The login is guarded by reCAPTCHA and a browser check, so the provider never logs in. The caller passes
`cookies`: the `Cookie` request header of their own signed-in browser (DevTools, Network, the `home`
request). It lasts as long as that browser session; signing out ends it.

Reverse-engineered and unofficial: not affiliated with Mercado Pago, automated access may be against
its terms, and the cookie gives full access to the account, so keep it private.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal

import httpx

from ..core import AccountKind, Balance, FinscraperError, LayoutChanged, Provider, SessionExpired

BASE_URL = "https://www.mercadopago.com.mx"
# The session is tied to the browser it came from; send a browser user agent (override with user_agent=).
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/141.0.0.0 Safari/537.36"
)
STATE = re.compile(r'<script id="__NORDIC_RENDERING_CTX__"[^>]*>\s*_n\.ctx\.r=(.*?)</script>', re.S)
KINDS = {"balance": AccountKind.CHECKING, "pots": AccountKind.SAVINGS, "crypto": AccountKind.CRYPTO}


class MercadoPago(Provider):
    id = "mercado_pago"
    name = "Mercado Pago"
    auth_fields = ("cookies",)

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
            "Cookie": self.auth["cookies"],
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "es-MX,es;q=0.9",
        }

    def fetch_balances(self) -> list[Balance]:
        balances = []
        for tab in self._banking_tabs(self._home_state()):
            title = tab.get("title") or tab.get("id") or "?"
            if tab.get("type") != "success":
                raise FinscraperError(f"mercado_pago: Mercado Pago did not load the {title} balance; try again")
            rows = (tab.get("balances") or {}).get("balances") or []
            for i, row in enumerate(rows):
                label = title if len(rows) == 1 else f"{title} {i + 1}"
                kind = KINDS.get(tab.get("id"), AccountKind.OTHER)
                balances.append(Balance(self.id, label, kind, _amount(row.get("amount") or {}, title), "MXN"))
        return balances

    def _home_state(self) -> dict:
        response = self._client.get("/home", headers=self._headers, follow_redirects=False)
        if response.is_redirect or response.status_code in (401, 403):
            raise SessionExpired("mercado_pago: session cookie expired; copy a fresh one from your signed-in browser")
        response.raise_for_status()
        match = STATE.search(response.text)
        if not match:
            if "/lgz/login" in response.text or "/login" in str(response.url):
                raise SessionExpired("mercado_pago: not signed in; copy a fresh cookie from your signed-in browser")
            raise LayoutChanged("mercado_pago: home page has no rendering state; Mercado Pago's page changed")
        try:
            state, _ = json.JSONDecoder().raw_decode(match.group(1).strip())
        except json.JSONDecodeError:
            raise LayoutChanged("mercado_pago: home page state is not JSON; Mercado Pago's page changed") from None
        return state

    @staticmethod
    def _banking_tabs(state: dict) -> list[dict]:
        rows = ((state.get("appProps") or {}).get("pageProps") or {}).get("gridData") or []
        for row in rows:
            for section in row.get("sections") or []:
                if section.get("type") == "BANKING":
                    return (section.get("content") or {}).get("tabs") or []
        raise LayoutChanged("mercado_pago: no BANKING widget on the home page; Mercado Pago's page changed")


def _amount(amount: dict, title: str) -> Decimal:
    if amount.get("iso") != "MXN":
        raise FinscraperError(f"mercado_pago: {title} is in {amount.get('iso')!r}, expected MXN")
    value = amount.get("value") or {}
    fraction = re.sub(r"\D", "", str(value.get("fraction", "")))  # whole pesos; drop any separators
    cents = re.sub(r"\D", "", str(value.get("cents", "0"))) or "0"
    if not fraction or len(cents) > 2:
        raise LayoutChanged(f"mercado_pago: unreadable amount for {title}; Mercado Pago's page changed")
    return Decimal(f"{fraction}.{cents.zfill(2)}")
