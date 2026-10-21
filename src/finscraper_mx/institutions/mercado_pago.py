"""Mercado Pago México, through the signed-in mobile app's home hub. Read-only.

Mercado Pago's developer API only covers payments, so this reads what the app's home screen shows.
`GET https://mobile.mercadopago.com/mp-home-hub/home` returns the home layout as JSON; its
`banking_andes` section has one tab per balance group (Saldo, Apartados, Criptomonedas), and each tab's
`banking_balance_andes` item carries `money_amount_items` already valued in MXN. The first read of
the day can be a stale snapshot, so the day's first `fetch_balances()` re-reads until two consecutive reads
agree; later calls that day read once (the settled date is cached in `~/.cache/finscraper-mx/`).

Signing in on the app needs a device-attestation token and a `face_validation` challenge that cannot be
reproduced off-device, so the provider never logs in: the caller captures their own signed-in session
(e.g. a mitmproxy capture of the app) and passes the credentials it yields. The required ones are the
`Authorization: Bearer APP_USR-…` `token`, the MeLi `session_id` and the `device_id`. The token is
per-login and comparatively short-lived; the session and device ids are stable for the install. If a
read is rejected, pass the optional `client_info`, `d2id`, `device_fsid` and `frontend_session_id`
captured alongside them.

Reverse-engineered and unofficial: not affiliated with Mercado Pago, automated access may be against its
terms, and the token gives full access to the account, so keep it private.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

from ..core import AccountKind, Balance, FinscraperError, LayoutChanged, Provider, SessionExpired

BASE_URL = "https://mobile.mercadopago.com"
# App constants (not credentials), overridable; mirror a recent signed-in iOS app so the read looks normal.
USER_AGENT = "MercadoPago-iOS/2.457.1 (iPhone; iOS 27.0.1)"
APP_VERSION = "2.457.1"
MELI_APPS = "ML-10.580.3"
# The banking section's tabs, by their Spanish title.
KINDS = {
    "Saldo": AccountKind.CHECKING,
    "Apartados": AccountKind.SAVINGS,
    "Criptomonedas": AccountKind.CRYPTO,
}
# The home hub's first read of the day answers from a stale snapshot (the app shows it, then refreshes),
# so that read is only trusted once two consecutive reads agree. Up to MAX_READS reads, SETTLE_SECONDS apart.
MAX_READS = 4
SETTLE_SECONDS = 1.5
MEXICO_CITY = timezone(timedelta(hours=-6))  # no DST since 2022; avoids needing tzdata
# The Mexico City date each session last settled on is kept in a small cache file, so later runs that day
# read once. It holds only a hash of the session id and a date (nothing secret); if it can't be read or
# written, every call just reconciles.
STATE_FILE = Path("finscraper-mx") / "mercado_pago.json"
# Per-user ids the risk gateway may want in addition to the token; sent only when supplied.
OPTIONAL_HEADERS = {
    "x-client-info": "client_info",
    "x-d2id": "d2id",
    "x-device-fsid": "device_fsid",
    "x-frontend-session-id": "frontend_session_id",
}


class MercadoPago(Provider):
    id = "mercado_pago"
    name = "Mercado Pago"
    auth_fields = ("token", "session_id", "device_id")

    def __init__(
        self,
        *,
        user_agent: str = USER_AGENT,
        app_version: str = APP_VERSION,
        meli_apps: str = MELI_APPS,
        settle_seconds: float = SETTLE_SECONDS,
        state_path: str | Path | None = None,
        client: httpx.Client | None = None,
        base_url: str = BASE_URL,
        **auth: str,
    ) -> None:
        super().__init__(**auth)
        self._settle_seconds = settle_seconds
        self._state_path = Path(state_path) if state_path else _default_state_path()
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=30)
        self._headers = {
            "Authorization": f"Bearer {self.auth['token']}",
            "x-meli-session-id": self.auth["session_id"],
            "x-device-id": self.auth["device_id"],
            "User-Agent": user_agent,
            "x-platform": "ios",
            "x-app-version": app_version,
            "x-meli-apps": meli_apps,
            "x-page-id": "mp3_home",
            "request-target": "get /mp-home-hub/home",
            "Accept": "*/*",
            "Accept-Language": "es-MX",
        }
        for header, key in OPTIONAL_HEADERS.items():
            if self.auth.get(key):
                self._headers[header] = self.auth[key]

    def fetch_balances(self) -> list[Balance]:
        today = datetime.now(MEXICO_CITY).date()
        key = hashlib.sha256(self.auth["session_id"].encode()).hexdigest()[:16]
        if _load_state(self._state_path).get(key) == today.isoformat():
            return self._read()  # already reconciled today: one read is enough
        balances = self._settled_read()
        _save_state(self._state_path, key, today)
        return balances

    def _settled_read(self) -> list[Balance]:
        # Re-read until two consecutive reads match.
        previous = self._read()
        for _ in range(MAX_READS - 1):
            time.sleep(self._settle_seconds)
            current = self._read()
            if _amounts(current) == _amounts(previous):
                return current
            previous = current
        return previous  # never settled: the latest read is the freshest

    def _read(self) -> list[Balance]:
        balances = []
        for tab in self._banking_tabs(self._home()):
            title = tab.get("title") or "?"
            item = next((it for it in (tab.get("items") or []) if it.get("type") == "banking_balance_andes"), None)
            if item is None:
                continue  # a tab with only promos/banners and no balance
            amounts = (item.get("params") or {}).get("money_amount_items") or []
            if not amounts:
                raise LayoutChanged(f"mercado_pago: no amount for {title}; the app's layout changed")
            # The first amount is the balance; any others (e.g. Saldo's 12-month yield) are informational.
            kind = KINDS.get(title, AccountKind.OTHER)
            balances.append(Balance(self.id, title, kind, _amount(amounts[0], title), "MXN"))
        if not balances:
            raise LayoutChanged("mercado_pago: no balances in the home hub; the app's layout changed")
        return balances

    def _home(self) -> dict:
        response = self._client.get(
            "/mp-home-hub/home",
            params={"has_companion": "true", "is_in_call": "false"},
            headers=self._headers,
        )
        if response.status_code in (401, 403):
            raise SessionExpired("mercado_pago: token rejected; capture a fresh Bearer token from your signed-in app")
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            raise LayoutChanged("mercado_pago: home hub did not return JSON; the app's API changed") from None

    @staticmethod
    def _banking_tabs(home: dict) -> list[dict]:
        for section in home.get("sections") or []:
            if section.get("type") == "banking_andes":
                return (section.get("content") or {}).get("tabs") or []
        raise LayoutChanged("mercado_pago: no banking section in the home hub; the app's layout changed")


def _default_state_path() -> Path:
    cache = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(cache) / STATE_FILE


def _load_state(path: Path) -> dict[str, str]:
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _save_state(path: Path, key: str, day: date) -> None:
    state = _load_state(path)
    state[key] = day.isoformat()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state))
    except OSError:
        pass  # can't cache: the next call reconciles again, which is still correct


def _amounts(balances: list[Balance]) -> list[tuple[str, Decimal]]:
    return [(b.account, b.amount) for b in balances]  # as_of differs between reads, so compare the rest


def _amount(item: dict, title: str) -> Decimal:
    currency = item.get("currency")
    if currency != "MXN":
        raise FinscraperError(f"mercado_pago: {title} is in {currency!r}, expected MXN")
    cleaned = str(item.get("amount", "")).replace(",", "").replace("$", "").strip()  # amounts arrive as plain decimals
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        raise LayoutChanged(f"mercado_pago: unreadable amount for {title}; the app's API changed") from None
