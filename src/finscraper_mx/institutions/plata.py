"""Plata (dif.tech, "Plata Card"), through the Plata mobile app gRPC API.

Read-only. Plata's backend is gRPC-over-HTTP/2 on ``prime.platacard.mx`` behind
Cloudflare. A session token (header ``x-plata-session-token``) gates every read.
That token is minted by ``AuthFlowService/Auth``, which the app calls with an
opaque device-signed login blob. The blob cannot be reconstructed off-device, so
this provider **replays the captured Auth request verbatim** to mint a fresh
session token on each run, then reads the Cuenta and savings/investment pockets
from ``AccountService/GetAccounts`` and the Plata Acciones brokerage portfolio
from ``InvestWidgetsService/GetInvestPlatformMainWidgets``. Everything is reported
in MXN; the Acciones portfolio is natively in USD and is converted at Google
Finance's live USD/MXN rate (overridable via ``usd_mxn_rate``).

Auth fields
-----------
auth_request
    Base64 of the raw gRPC request body of the app's
    ``client.diftech.platform.auth.v2.AuthFlowService/Auth`` call, captured once
    from a mitmproxy session. It carries the device-signed login blob together
    with the user-id hash and device metadata, and is replayed unchanged to mint
    a session token. It is long-lived (observed still valid ~10 hours after
    capture; exact lifetime unknown) and is invalidated when Plata rotates the
    device key; when it lapses, reads raise
    :exc:`~finscraper_mx.core.SessionExpired` and you recapture it. The user-id hash
    and device id used on read calls are read back out of this blob, so they do
    not need to be supplied separately.
installation_id
    The ``x-plata-installation-id`` header value (a UUID) from the same capture.
    It is telemetry the app sends on every call; it is not inside ``auth_request``
    so it is supplied on its own.

How to capture with mitmproxy
-----------------------------
1. Run mitmproxy and proxy your iPhone through it (no cert pinning on Plata's
   own backend, so the flows are readable).
2. Open the Plata app and let the home screen load.
3. In the capture find the POST to
   ``prime.platacard.mx/.../AuthFlowService/Auth``:
   - base64-encode its **request body** (raw bytes) -> ``auth_request``.
   - copy the ``x-plata-installation-id`` request header -> ``installation_id``.

Not affiliated with Plata or dif.tech. Automated access may be against Plata's
terms of use; use only with your own account.
"""
from __future__ import annotations

import base64
import os
import re
import uuid
import warnings
from decimal import Decimal

import httpx

from ..core import (
    AccountKind,
    Balance,
    LayoutChanged,
    Provider,
    SessionExpired,
)

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

BASE_URL = "https://prime.platacard.mx"
_AUTH_PATH = "/client.diftech.platform.auth.v2.AuthFlowService/Auth"
_ACCOUNTS_PATH = (
    "/client.diftech.processing.papi.gateway.api.accounts.v1.AccountService/GetAccounts"
)
_INVEST_PATH = (
    "/client.diftech.invest.gateway.widgets.v1.InvestWidgetsService"
    "/GetInvestPlatformMainWidgets"
)

# App constants (same for every user — embedded in the Plata iOS app).
_APP_VERSION = "2.12.0"
_USER_AGENT = f"ios/{_APP_VERSION} grpc"
_DEVICE_MODEL = "iPhone 17 Air"  # cosmetic telemetry; some gateways want it present
_GRPC_TIMEOUT = "30000000u"      # 30s client deadline; the invest gateway requires it

# Plata Acciones is a USD brokerage; its balance is converted to MXN at read time
# using Google Finance's live USD/MXN rate (the caller can override the rate).
_GOOGLE_FX_URL = "https://www.google.com/finance/quote/USD-MXN"
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

CENT = Decimal("0.01")
_NANO = Decimal("1000000000")
_EMPTY_MSG = b"\x00\x00\x00\x00\x00"  # a gRPC-framed empty request message

# GetAccounts response field numbers (reverse-engineered; see docs/flows/plata.md).
# Each account envelope wraps its body under one field: f4 = Cuenta (debit),
# f6 = a named savings/investment pocket. (A primary Mastercard credit card is
# carried under f1; it is intentionally not reported — reads are investment +
# debit only — but the shape is documented in docs/flows/plata.md.)
#
# Both account kinds carry two money fields: the principal and the interest that
# has accrued but not yet been paid out. Plata's real spendable value is the two
# added together, so each reported balance is principal + accrued interest.
_WRAP_CUENTA = 4
_WRAP_INVESTMENT = 6
# (principal field, accrued-interest field) inside each account body
_CUENTA_PRINCIPAL, _CUENTA_INTEREST = 9, 10
_INVESTMENT_PRINCIPAL, _INVESTMENT_INTEREST = 7, 8


# ---------------------------------------------------------------------------
# Minimal protobuf / gRPC codec (just enough for the two calls above)
# ---------------------------------------------------------------------------


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, i
        shift += 7


def _parse_message(buf: bytes) -> dict[int, list]:
    """Decode a protobuf message into {field_number: [values]}.

    Varints become ``int``; length-delimited fields become ``bytes`` (the caller
    re-parses nested messages as needed). Other wire types are skipped.
    """
    fields: dict[int, list] = {}
    i = 0
    n = len(buf)
    while i < n:
        tag, i = _read_varint(buf, i)
        field_no, wire = tag >> 3, tag & 7
        if wire == 0:
            val, i = _read_varint(buf, i)
        elif wire == 2:
            length, i = _read_varint(buf, i)
            val = buf[i : i + length]
            i += length
        elif wire == 1:
            val = buf[i : i + 8]
            i += 8
        elif wire == 5:
            val = buf[i : i + 4]
            i += 4
        else:  # unknown wire type — give up, caller raises LayoutChanged
            break
        fields.setdefault(field_no, []).append(val)
    return fields


def _grpc_frame(payload: bytes):
    """Yield one gRPC request frame as a stream.

    Passing this as httpx ``content`` sends the body as HTTP/2 DATA frames with
    **no** ``content-length`` header — the way a real gRPC client does. Plata's
    invest gateway rejects a request that carries ``content-length`` (it resets
    its upstream "get accounts" call with PROTOCOL_ERROR), so every gRPC call
    goes out this way. ``payload`` is already gRPC-framed (flag + length + body).
    """
    yield payload


def _grpc_unframe(body: bytes, what: str = "response") -> bytes:
    """Return the first gRPC message's payload from a framed response body.

    An empty body means the server answered with only gRPC trailers (e.g. an
    ``UNAUTHENTICATED`` status), i.e. it rejected the call.
    """
    if len(body) < 5:
        raise SessionExpired(f"plata: empty {what} — the server rejected the call")
    length = int.from_bytes(body[1:5], "big")
    return body[5 : 5 + length]


def _money(raw: bytes) -> Decimal:
    """Decode a Google-Money-style {f1: currency, f2: units, f3: nanos}."""
    m = _parse_message(raw)
    units = m.get(2, [0])[0]
    nanos = m.get(3, [0])[0]
    return (Decimal(units) + Decimal(nanos) / _NANO).quantize(CENT)


def _balance_with_interest(
    body: dict[int, list], principal_field: int, interest_field: int
) -> Decimal:
    """principal + accrued-but-unpaid interest (interest is optional)."""
    if principal_field not in body:
        raise LayoutChanged(f"plata: balance field f{principal_field} missing")
    total = _money(body[principal_field][0])
    if interest_field in body:
        total += _money(body[interest_field][0])
    return total


def _label(body: dict[int, list], number: bytes) -> str:
    """Pick the account's human label, falling back to the masked number."""
    for values in body.values():
        for v in values:
            if not isinstance(v, bytes):
                continue
            try:
                text = v.decode("ascii")
            except UnicodeDecodeError:
                continue
            if (
                2 <= len(text) <= 24
                and text.isprintable()
                and not text.isdigit()
                and not text.startswith("http")
                and "-" not in text  # skip UUIDs
            ):
                return text
    tail = number.decode("ascii", "ignore")[-4:] if number else ""
    return f"*{tail}" if tail else "Plata"


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class Plata(Provider):
    """Reads Plata account balances via the Plata app's gRPC API.

    See the module docstring for how to capture ``auth_request`` and
    ``installation_id`` from a mitmproxy session.
    """

    id = "plata"
    name = "Plata"
    auth_fields = ("auth_request", "installation_id")

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        usd_mxn_rate: Decimal | str | None = None,
        **auth: str,
    ) -> None:
        super().__init__(**auth)
        self._client = client or httpx.Client(
            base_url=BASE_URL, http2=True, timeout=30
        )
        # Optional override for the USD->MXN rate; when None it is fetched live.
        self._usd_mxn_rate = Decimal(usd_mxn_rate) if usd_mxn_rate is not None else None
        # Trace/session context the app sends; the invest gateway propagates it to
        # its upstream, which fails without it. One session id per provider.
        self._mobile_session_id = str(uuid.uuid4()).upper()
        self._auth_body = base64.b64decode(self.auth["auth_request"])
        self._user_id_hash, self._device_id = self._identity_from_auth()

    # ------------------------------------------------------------------
    # Public read path
    # ------------------------------------------------------------------

    def fetch_balances(self) -> list[Balance]:
        """Mint a session token and return every account's balance.

        Returns the debit Cuenta and the savings/investment pockets (principal +
        accrued interest) plus, when reachable, the Plata Acciones brokerage
        portfolio (its USD value converted to MXN at Google's live rate). All
        amounts are in MXN.

        The Plata Acciones read is wrapped **best-effort**: the brokerage widget
        is a separate gateway, and a hiccup there must not block the core
        balances, so if it fails the account is omitted and a
        :class:`UserWarning` is emitted rather than raising.

        Raises :exc:`SessionExpired` when the captured ``auth_request`` is no
        longer accepted (device key rotated or expired), and :exc:`LayoutChanged`
        when the accounts response no longer matches the expected shape.
        """
        token = self._mint_session_token()
        balances = self._read_balances(token)
        try:
            balances += self._read_investment_portfolio(token)
        except (SessionExpired, LayoutChanged, httpx.HTTPError) as exc:
            warnings.warn(f"plata: Plata Acciones skipped ({exc})", stacklevel=2)
        return balances

    # ------------------------------------------------------------------
    # Auth (private)
    # ------------------------------------------------------------------

    def _identity_from_auth(self) -> tuple[str, str]:
        """Read the user-id hash and device id back out of the Auth blob."""
        try:
            msg = _parse_message(_grpc_unframe(self._auth_body))
            user_id_hash = msg[1][0].decode("ascii")
            device_meta = _parse_message(msg[3][0])
            device_id = device_meta[1][0].decode("ascii")
        except (KeyError, IndexError, UnicodeDecodeError) as exc:
            raise LayoutChanged(
                "plata: auth_request does not look like a Plata Auth request"
            ) from exc
        return user_id_hash, device_id

    def _headers(self, token: str | None) -> dict[str, str]:
        headers = {
            "content-type": "application/grpc",
            "te": "trailers",
            "grpc-timeout": _GRPC_TIMEOUT,
            "baggage": f"mobile_session_id={self._mobile_session_id}",
            "traceparent": f"00-{os.urandom(16).hex()}-{os.urandom(8).hex()}-01",
            "user-agent": _USER_AGENT,
            "x-plata-app-version": _APP_VERSION,
            "x-plata-device-os": "iOS",
            "x-plata-device-brand": "Apple",
            "x-plata-device-model": _DEVICE_MODEL,
            "x-plata-locale": "en",
            "x-plata-timezone": "America/Mexico_City",
            "x-plata-user-id-hash": self._user_id_hash,
            "x-plata-device-id": self._device_id,
            "x-plata-installation-id": self.auth["installation_id"],
        }
        if token is not None:
            headers["x-plata-session-token"] = token
        return headers

    def _mint_session_token(self) -> str:
        resp = self._client.post(
            _AUTH_PATH, headers=self._headers(None), content=_grpc_frame(self._auth_body)
        )
        if resp.status_code in (401, 403):
            raise SessionExpired("plata: Auth rejected (401/403) — recapture auth_request")
        resp.raise_for_status()
        if len(resp.content) < 5:
            raise SessionExpired(
                "plata: Auth returned an empty response — the captured auth_request "
                "was rejected (it has likely expired); recapture it and regenerate "
                "auth_plata.json"
            )
        try:
            msg = _parse_message(_grpc_unframe(resp.content, "Auth response"))
            token = _parse_message(msg[1][0])[1][0].decode("ascii")
        except (KeyError, IndexError, UnicodeDecodeError) as exc:
            raise SessionExpired(
                "plata: Auth returned no session token — recapture auth_request"
            ) from exc
        if not token:
            raise SessionExpired("plata: Auth returned an empty session token")
        return token

    # ------------------------------------------------------------------
    # Balance read (private)
    # ------------------------------------------------------------------

    def _read_balances(self, token: str) -> list[Balance]:
        resp = self._client.post(
            _ACCOUNTS_PATH, headers=self._headers(token), content=_grpc_frame(_EMPTY_MSG)
        )
        if resp.status_code in (401, 403):
            raise SessionExpired("plata: session token rejected (401/403)")
        resp.raise_for_status()

        payload = _grpc_unframe(resp.content, "GetAccounts response")
        try:
            wrapper = _parse_message(_parse_message(payload)[1][0])
        except (KeyError, IndexError) as exc:
            raise LayoutChanged("plata: unexpected GetAccounts response shape") from exc

        balances: list[Balance] = []
        for envelope in wrapper.get(1, []):  # repeated account envelope
            balances.extend(self._account_balances(envelope))
        if not balances:
            raise LayoutChanged("plata: no accounts found in GetAccounts response")
        return balances

    def _read_investment_portfolio(self, token: str) -> list[Balance]:
        """Read the Plata Acciones brokerage portfolio value, in USD."""
        resp = self._client.post(
            _INVEST_PATH, headers=self._headers(token), content=_grpc_frame(_EMPTY_MSG)
        )
        if resp.status_code in (401, 403):
            raise SessionExpired("plata: session token rejected (401/403)")
        resp.raise_for_status()

        if len(resp.content) < 5:
            # gRPC error: for a trailers-only response the status rides in the
            # response headers, so surface the server's actual reason.
            status = resp.headers.get("grpc-status", "?")
            message = resp.headers.get("grpc-message", "(no message)")
            raise LayoutChanged(
                f"plata: invest widget rejected (grpc-status={status}: {message})"
            )

        payload = _grpc_unframe(resp.content, "GetInvestPlatformMainWidget response")
        try:
            # widget -> f1 -> f1 -> f1 -> f3 = portfolio value (Money, USD)
            summary = _parse_message(_parse_message(_parse_message(
                _parse_message(payload)[1][0])[1][0])[1][0])
            value_raw = summary[3][0]
        except (KeyError, IndexError) as exc:
            raise LayoutChanged(
                "plata: unexpected GetInvestPlatformMainWidget response shape"
            ) from exc
        currency = _parse_message(value_raw).get(1, [b"USD"])[0].decode("ascii", "ignore")
        amount = _money(value_raw)
        if currency == "USD":  # convert to MXN at Google's live rate
            amount = (amount * self._usd_to_mxn()).quantize(CENT)
            currency = "MXN"
        return [
            Balance(
                institution=self.id,
                account="Plata Acciones",
                kind=AccountKind.INVESTMENT,
                amount=amount,
                currency=currency,
            )
        ]

    def _usd_to_mxn(self) -> Decimal:
        """Current USD->MXN rate from Google Finance (or the caller's override)."""
        if self._usd_mxn_rate is not None:
            return self._usd_mxn_rate
        resp = httpx.get(
            _GOOGLE_FX_URL,
            headers={"User-Agent": _BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=15,
            follow_redirects=True,
        )
        resp.raise_for_status()
        # the live quote is the first FX-magnitude number after the pair title
        match = re.search(r'"USD / MXN".{0,600}?,([1-9]\d\.\d{2,}),', resp.text, re.S)
        if not match:
            raise LayoutChanged("plata: could not read USD->MXN rate from Google Finance")
        rate = Decimal(match.group(1))
        if not (Decimal("1") < rate < Decimal("1000")):
            raise LayoutChanged(f"plata: implausible USD->MXN rate {rate}")
        return rate

    def _account_balances(self, envelope: bytes) -> list[Balance]:
        env = _parse_message(envelope)

        if _WRAP_CUENTA in env:
            body = _parse_message(env[_WRAP_CUENTA][0])
            return [
                Balance(
                    institution=self.id,
                    account=_label(body, body.get(2, [b""])[0]),
                    kind=AccountKind.CHECKING,
                    amount=_balance_with_interest(
                        body, _CUENTA_PRINCIPAL, _CUENTA_INTEREST
                    ),
                    currency="MXN",
                )
            ]

        if _WRAP_INVESTMENT in env:
            body = _parse_message(env[_WRAP_INVESTMENT][0])
            return [
                Balance(
                    institution=self.id,
                    account=_label(body, body.get(2, [b""])[0]),
                    kind=AccountKind.INVESTMENT,
                    amount=_balance_with_interest(
                        body, _INVESTMENT_PRINCIPAL, _INVESTMENT_INTEREST
                    ),
                    currency="MXN",
                )
            ]

        # Anything else (a primary credit card under f1, the summary block, …)
        # is intentionally not reported.
        return []
