"""Shared model and contract every institution implements."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar


class AccountKind(StrEnum):
    CHECKING = "checking"      # cuenta de débito / nómina
    SAVINGS = "savings"        # ahorro, cajitas, apartados
    INVESTMENT = "investment"  # fondos, CETES, pagarés, acciones
    CRYPTO = "crypto"
    CREDIT = "credit"          # tarjeta de crédito: amount is the balance owed, as a negative number
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Balance:
    """One account's balance as reported by the institution at `as_of`."""

    institution: str  # Provider.id, e.g. "mercado_pago"
    account: str      # institution's own label or masked number, e.g. "Cuenta *1234"
    kind: AccountKind
    amount: Decimal
    currency: str = "MXN"
    as_of: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "institution": self.institution,
            "account": self.account,
            "kind": self.kind.value,
            "amount": str(self.amount),
            "currency": self.currency,
            "as_of": self.as_of.isoformat(),
        }


class FinscraperError(Exception):
    """Base class for everything this library raises on purpose."""


class AuthRequired(FinscraperError):
    """A required credential or session value was not supplied."""


class SessionExpired(FinscraperError):
    """The institution rejected the session; the user must log in again."""


class LayoutChanged(FinscraperError):
    """The institution's response no longer looks the way this provider expects."""


class Provider(ABC):
    """Reads balances from one institution.

    Providers are read-only and never persist credentials or sessions: the caller supplies them
    (`auth_fields`) and decides where, if anywhere, they are stored. A provider may keep non-secret,
    best-effort cache state (e.g. Mercado Pago's settled-today date).
    """

    id: ClassVar[str]                     # stable snake_case key, e.g. "mercado_pago"
    name: ClassVar[str]                   # display name, e.g. "Mercado Pago"
    auth_fields: ClassVar[tuple[str, ...]] = ()

    def __init__(self, **auth: str) -> None:
        missing = [name for name in self.auth_fields if not auth.get(name)]
        if missing:
            raise AuthRequired(f"{self.id}: missing {', '.join(missing)}")
        self.auth = auth

    @abstractmethod
    def fetch_balances(self) -> list[Balance]:
        """Return the current balance of every account visible to this login."""
