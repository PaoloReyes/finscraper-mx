"""DiDi Cuenta (SOFIPO DiDi), through the DiDi Dinero mobile app's home screen. Read-only.

DiDi has no public balance API, so this reads what the app's home screen shows:
`POST https://wallet.didipay.com/sofipo/uchome/v0/main` returns the home layout as JSON, and the
`sellingPoint` card in `data.content.cardList` carries the Cuenta balance as a string like `"10,029.18"`.

The provider never logs in: the caller captures their own signed-in app session (e.g. a mitmproxy capture
of the iOS app) and passes the values it yields:

- `token` — the SOFIPO session ticket: `ticket` in the `signByPassword` response, or `token` inside the
  `sofi-header-hint-content` header of any later call to `wallet.didipay.com`.
- `wsgsig` — the request-integrity header DiDi's security SDK adds, copied from a captured
  `uchome/v0/main` request. The provider sends the same query and body as the app, so the captured value
  replays.
- `device_id` — the `deviceId` query parameter of that request.

The token lasts as long as the app session; when a read is rejected the provider raises `SessionExpired`
and a fresh capture is needed.

Reverse-engineered and unofficial: not affiliated with DiDi, automated access may be against its terms,
and the token gives access to the account, so keep it private.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal, InvalidOperation

import httpx

from ..core import AccountKind, Balance, LayoutChanged, Provider, SessionExpired

BASE_URL = "https://wallet.didipay.com"
MAIN_PATH = "/sofipo/uchome/v0/main"
# App constants (not credentials); mirror a recent iOS DiDi Dinero build so the read looks normal.
APP_VERSION = "0.4.1"
APPID_SOFIPO = 200062
CITY_ID = 52151600
COUNTRY = "MX"
SOFIPO_SDK_VERSION = "7.6.96"
VERIFY_SDK_VERSION = "1.7"
OS_VERSION = "27.0"
USER_AGENT = f"DiDiDinero/{APP_VERSION} (iPhone; iOS {OS_VERSION}; Scale/3.00)"
SECDD_CHALLENGE = "2,com.dididinero.app|1.0.0||||||"
# Mexico City; the app sends a location with every call. Pass the captured one if reads are rejected.
DEFAULT_LOCATION = (19.4326, -99.1332)
CENT = Decimal("0.01")


class DiDi(Provider):
    id = "didi"
    name = "DiDi Cuenta"
    auth_fields = ("token", "wsgsig", "device_id")

    def __init__(
        self,
        *,
        location: tuple[float, float] = DEFAULT_LOCATION,
        client: httpx.Client | None = None,
        base_url: str = BASE_URL,
        **auth: str,
    ) -> None:
        super().__init__(**auth)
        self._lat, self._lon = location
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=30)

    def fetch_balances(self) -> list[Balance]:
        data = self._home()["data"]
        cards = (data.get("content") or {}).get("cardList") or []
        raw = next((card.get("balance") for card in cards if card.get("cardType") == "sellingPoint"), None)
        if raw is None:  # the analytics block repeats the balance; use it if the card moved
            raw = (data.get("logData") or {}).get("deposit_amount")
        if raw is None:
            types = [card.get("cardType") for card in cards]
            raise LayoutChanged(f"didi: no balance card in the home screen (card types: {types}); the app's layout changed")
        return [Balance(self.id, "DiDi Cuenta", AccountKind.SAVINGS, _amount(raw), "MXN")]

    def _home(self) -> dict:
        device_id = self.auth["device_id"]
        params = {
            "appversion": APP_VERSION,
            "bioFlag": "0",
            "city_id": str(CITY_ID),
            "countryCode": COUNTRY,
            "deviceId": device_id,
            "fromType": "0",
            "imei": device_id,
            "ipAddr": "192.168.1.1",
            "lang": "es-MX",
            "latitude": str(self._lat),
            "location_cityid": str(CITY_ID),
            "location_country": COUNTRY,
            "longitude": str(self._lon),
            "model": "iPhone",
            "networkType": "WIFI",
            "origin_id": "1",
            "sofipo_sdk_version": SOFIPO_SDK_VERSION,
            "systemVersion": OS_VERSION,
            "terminal_id": "300324",
            "utc_offset": "-360",
            "verifySdkVersion": VERIFY_SDK_VERSION,
        }
        body = {"bioFlag": 0, "sofipo_sdk_version": SOFIPO_SDK_VERSION, "verifySdkVersion": VERIFY_SDK_VERSION, "bulletinId": ""}
        sofi_hint = {"appid": APPID_SOFIPO, "location_country": COUNTRY, "token": self.auth["token"]}
        didi_hint = {
            "locale": "es_MX",
            "lang": "es-MX",
            "location_cityid": str(CITY_ID),
            "app_timeout_ms": 30000,
            "lng": str(self._lon),
            "lat": str(self._lat),
            "utc_offset": "-360",
        }
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "es-MX;q=1",
            "wsgbizsource": "sofi",
            "sofi-header-hint-content": json.dumps(sofi_hint, separators=(",", ":")),
            "didi-header-hint-content": json.dumps(didi_hint, separators=(",", ":")),
            "netlib": "OKNet,AF3,0.1.1",
            "secdd-challenge": SECDD_CHALLENGE,
            "wsgsig": self.auth["wsgsig"],
            "didi-header-rid": os.urandom(16).hex(),
            "priority": "u=3, i",
        }
        response = self._client.post(MAIN_PATH, params=params, json=body, headers=headers)
        if response.status_code in (401, 403):
            raise SessionExpired("didi: token rejected; capture a fresh session from your signed-in app")
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            raise LayoutChanged("didi: home screen did not return JSON; the app's API changed") from None
        if data.get("errno", -1) != 0 or data.get("data") is None:
            # DiDi answers 200 with a non-zero errno when the ticket or wsgsig is no longer accepted.
            raise SessionExpired(f"didi: home screen refused (errno {data.get('errno')}: {data.get('errmsg')}); recapture")
        return data


def _amount(raw: str) -> Decimal:
    try:
        return Decimal(str(raw).replace(",", "").replace("$", "").strip()).quantize(CENT)
    except InvalidOperation:
        raise LayoutChanged(f"didi: unreadable balance {raw!r}; the app's layout changed") from None
