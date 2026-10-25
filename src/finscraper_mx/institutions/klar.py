"""Klar México (digital bank / SoFiPo), through the signed-in mobile app. Read-only.

Klar has no public API. Its mobile app authenticates against a Keycloak-backed gateway and gates every
data call with a per-request MFA challenge-response bound to a **trusted device**: the device holds an
RSA-2048 keypair, registers its public key at pairing, and answers each call's challenge by decrypting a
server-sent ciphertext with its private key. So a headless reader must *be* a registered trusted device.

The library can't pair itself (pairing is KYC-gated: face scan + SMS), and the device key never leaves the
phone's keychain, so the caller supplies the pieces captured from their own paired phone: the device RSA
`private_key_pem`, its `device_id` (the trusted-device UUID), and the account `username` + `password`.
Each `fetch_balances()` then performs the app's real flow end to end:

  1. Two-step login to `api.klar.mx/login/api/auth/v1/login` — step 1 with `mfaAccept: TRUSTED_DEVICE:<id>`
     returns a provisional `klar-client` token + an RSA-encrypted login challenge; the provider decrypts it
     with the private key (PKCS#1 v1.5) and step 2 upgrades to the RBAC-authorized `klar-client-mfa` token.
  2. Read `GET /balances2/api/v0/user`, which replies `307` with an `mfa-challenge` header; the provider
     decrypts it and retries with the `mfa-response`, then maps each account's `availableBalance` to MXN.

Klar enforces **one active session per account**: if the phone is signed in, login returns `423` and the
read raises `SessionExpired` — close the app (or let it idle) and retry. Logging the phone out does not
un-pair it, so the phone and this reader share the one trusted device, just not a session at the same time.

Klar's AWS firewall rejects datacenter IPs (a cloud server gets an HTML `403` on login). An optional
`proxy` auth value (e.g. `http://100.64.0.2:8899`) routes this provider's calls, and only these, through
a proxy on a residential connection, such as a home machine over Tailscale.

Reverse-engineered and unofficial: not affiliated with Klar, automated access may be against its terms, and
the key + credentials give full account access, so keep them private. See `docs/flows/klar.md` for how to
capture the device key and id from your phone.
"""
from __future__ import annotations

import base64
import uuid
from decimal import Decimal

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ..core import AccountKind, AuthRequired, Balance, LayoutChanged, Provider, SessionExpired

BASE_URL = "https://api.klar.mx"
GW_LOGOUT_URL = "https://gw.klar.mx/api/v2/auth/logout"
LOGIN_PATH = "/login/api/auth/v1/login"
BALANCES_PATH = "/balances2/api/v0/user"
# App constants (not credentials), overridable; mirror a recent signed-in app so the read looks normal.
USER_AGENT = "Dart/2.15 (dart:io) android 1.124.1 27209"
CLIENT_VERSION = "1.124.1 27209"
PLATFORM = "Android-HONOR-RMO-NX3-RMO-NX3-33:version-13"

# Klar account type -> how we report it. CREDIT (a credit line, not a holding) and DEBIT_PENDING
# (not yet settled) are not balances the user holds, so they are excluded — as the app's net worth is.
KIND_BY_TYPE = {
    "DEBIT": AccountKind.CHECKING,          # Cuenta
    "SAVINGS": AccountKind.SAVINGS,         # Ahorro / inversión flexible
    "SECURED_DEPOSIT": AccountKind.INVESTMENT,  # Depósito en garantía
}
EXCLUDED_TYPES = {"CREDIT", "DEBIT_PENDING"}
LABELS = {"DEBIT": "Cuenta", "SAVINGS": "Ahorro", "SECURED_DEPOSIT": "Depósito en garantía"}


class Klar(Provider):
    id = "klar"
    name = "Klar México"
    # device RSA key + its trusted-device id (captured from the paired phone) + account credentials.
    auth_fields = ("private_key_pem", "device_id", "username", "password")

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        base_url: str = BASE_URL,
        **auth: str,
    ) -> None:
        super().__init__(**auth)
        try:
            self._key = serialization.load_pem_private_key(_pem(self.auth["private_key_pem"]).encode(), password=None)
        except (ValueError, TypeError) as error:
            raise AuthRequired(f"klar: private_key_pem is not a valid PEM private key ({error})") from None
        if not isinstance(self._key, rsa.RSAPrivateKey):
            raise AuthRequired("klar: private_key_pem must be an RSA private key")
        self._device_id = self.auth["device_id"]
        self._session_id = str(uuid.uuid4())
        self._token: str | None = None
        # follow_redirects stays off: the MFA handshake is a 307 we must answer, not follow.
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"), timeout=30, follow_redirects=False, proxy=self.auth.get("proxy") or None)

    # ---- the one read path ----

    def fetch_balances(self) -> list[Balance]:
        self._login()
        try:
            body = self._mfa_get(BALANCES_PATH)
        finally:
            self._logout()  # release our session so the next read (and the phone) isn't locked out by 423
        accounts = body.get("balances")
        if not isinstance(accounts, list):
            raise LayoutChanged("klar: balances response has no 'balances' list; the app's API changed")
        held = []
        for account in accounts:
            kind_type = account.get("type")
            if kind_type in EXCLUDED_TYPES:
                continue
            money = account.get("availableBalance") or {}
            if "units" not in money and "nanos" not in money:
                continue
            held.append((account, money))
        labels = _unique_labels([account for account, _ in held])
        balances = [
            Balance(self.id, label, KIND_BY_TYPE.get(account.get("type"), AccountKind.OTHER), _amount(money),
                    money.get("currencyCode") or "MXN")
            for label, (account, money) in zip(labels, held)
        ]
        if not balances:
            raise LayoutChanged("klar: no (non-credit) accounts in the balances response; the app's layout changed")
        return balances

    def _logout(self) -> None:
        """End our Keycloak session. Klar allows one session per account and it lingers after the token
        expires, so leaving it open would 423 the next read (and the phone). Best-effort: never raises."""
        if not self._token:
            return
        try:
            self._client.post(GW_LOGOUT_URL, headers=self._headers(), json={})
        except httpx.HTTPError:
            pass
        finally:
            self._token = None

    # ---- auth: two-step login (klar-client -> klar-client-mfa) ----

    def _login(self) -> None:
        creds = {"username": self.auth["username"], "password": self.auth["password"]}
        step1 = self._post_login({"credentials": {**creds, "mfaAccept": f"TRUSTED_DEVICE:{self._device_id}"}})
        mfa = step1.get("mfa") or {}
        challenge_id, challenge = mfa.get("mfaChallengeId"), mfa.get("mfaChallenge")
        if not (challenge_id and challenge):
            raise SessionExpired(
                "klar: login did not return an MFA challenge — the device_id/key is not this account's "
                "trusted device anymore; recapture them from your paired phone"
            )
        step2 = self._post_login({"credentials": {**creds, "mfa": {"mfaChallengeId": challenge_id,
                                                                    "mfaResponse": self._solve(challenge)}}})
        token = (step2.get("token") or {}).get("accessToken")
        if not token:
            raise SessionExpired("klar: login did not reach the authorized (klar-client-mfa) token; retry")
        self._token = token

    def _post_login(self, body: dict) -> dict:
        body = {**body, "additionalData": {"notificationToken": "", "clientVersion": CLIENT_VERSION, "platform": PLATFORM}}
        response = self._client.post(LOGIN_PATH, headers=self._headers(auth=False), json=body)
        if response.status_code == 423:
            raise SessionExpired(
                "klar: a session is already active for this account — Klar allows one at a time. "
                "Close the Klar app (or let it idle until its session lapses), then retry"
            )
        if response.status_code == 401:
            raise SessionExpired("klar: wrong username or password") from None
        if response.status_code == 412:
            raise SessionExpired(_NOT_PAIRED)
        payload = _json(response)
        if (payload.get("mfa") or {}).get("status") == "MFA_PAIRING_REQUIRED":
            raise SessionExpired(_NOT_PAIRED)
        return payload

    # ---- per-request MFA challenge-response ----

    def _mfa_get(self, path: str) -> dict:
        request_id = str(uuid.uuid4())
        first = self._client.get(path, headers=self._headers({
            "mfa-accept": f"TRUSTED_DEVICE:{self._device_id}", "mfa-request-id": request_id}))
        if first.status_code == 200:
            return _json(first)
        challenge = first.headers.get("mfa-challenge")
        challenge_id = first.headers.get("mfa-challenge-id") or first.headers.get("secure-action-challenge-id")
        if not challenge:
            if first.status_code in (401, 403):
                raise SessionExpired("klar: token rejected reading balances; retry")
            raise LayoutChanged(f"klar: expected an MFA challenge on {path}, got HTTP {first.status_code}")
        second = self._client.get(path, headers=self._headers({
            "mfa-accept": f"TRUSTED_DEVICE:{self._device_id}", "mfa-response": self._solve(challenge),
            "mfa-challenge-id": challenge_id or "", "mfa-request-id": request_id}))
        if second.status_code in (401, 403):
            raise SessionExpired("klar: MFA response rejected; recapture the device key/id from your phone")
        return _json(second)

    def _solve(self, challenge_b64: str) -> str:
        """Decrypt the server's RSA challenge with the device private key (PKCS#1 v1.5), as the app does."""
        try:
            plaintext = self._key.decrypt(base64.b64decode(challenge_b64), padding.PKCS1v15())
        except (ValueError, TypeError) as error:
            raise SessionExpired(f"klar: could not decrypt the MFA challenge with this key ({error})") from None
        return plaintext.decode("utf-8", "replace").strip()

    def _headers(self, extra: dict[str, str] | None = None, *, auth: bool = True) -> dict[str, str]:
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "klarmfa": "Enabled",
            "session-id": self._session_id,
            "mfa-interrupt-status": "307",
            "baggage": f"klar_request_id={uuid.uuid4()}",
        }
        if auth and self._token:
            headers["authorization"] = f"Bearer {self._token}"
            headers["x-auth-token"] = self._token
        if extra:
            headers.update(extra)
        return headers


def _pem(value: str) -> str:
    """Accept the PEM as written, or on one line with literal \\n escapes (how .env files usually carry it)."""
    return value.strip().replace("\\r\\n", "\n").replace("\\n", "\n")


_NOT_PAIRED = ("klar: this device is not registered as a trusted device (MFA_PAIRING_REQUIRED); "
               "recapture private_key_pem and device_id from your paired phone")


def _json(response: httpx.Response) -> dict:
    try:
        return response.json()
    except ValueError:
        # name the call and status (an error page, a WAF block, an empty body) so the failure is diagnosable
        snippet = " ".join(response.text[:120].split())
        raise LayoutChanged(
            f"klar: expected JSON from {response.request.method} {response.request.url.path}, got HTTP "
            f"{response.status_code} {response.headers.get('content-type', '')!r}: {snippet!r}"
        ) from None


def _label(account: dict) -> str:
    name = account.get("alias") or account.get("name") or account.get("productName")
    if name:
        return str(name).strip()
    return LABELS.get(account.get("type"), str(account.get("type") or "?"))


def _unique_labels(accounts: list[dict]) -> list[str]:
    """One name per account. Several accounts of one type (e.g. two "Ahorro") would share a name, so a caller
    couldn't pick one of them; those get the end of their account id, which is stable across reads
    ("Ahorro ·3f9a"), or their position when Klar sends no id."""
    labels = [_label(account) for account in accounts]
    out = []
    for i, (label, account) in enumerate(zip(labels, accounts)):
        if labels.count(label) > 1:
            tail = str(account.get("id") or "")[-4:]
            label = f"{label} ·{tail}" if tail else f"{label} {labels[:i + 1].count(label)}"
        out.append(label)
    return out


def _amount(money: dict) -> Decimal:
    """Klar money is {units, nanos, currencyCode}; combine to a 2-decimal MXN Decimal (nanos may be negative)."""
    try:
        units = Decimal(str(money.get("units") or 0))
        nanos = Decimal(str(money.get("nanos") or 0)) / Decimal(1_000_000_000)
    except (ValueError, ArithmeticError):
        raise LayoutChanged("klar: unreadable balance amount; the app's API changed") from None
    return (units + nanos).quantize(Decimal("0.01"))
