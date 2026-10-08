"""BBVA México (bbva.mx) personal online banking.

Status: not implemented yet. Planned approach, following the session-reuse pattern:
  1. The user logs in to bbva.mx in a normal browser.
  2. They copy the session values the web app sends with its own requests (cookie and XSRF token).
  3. This provider calls the same internal JSON endpoint the web app uses for the accounts overview.
The user's password is never handled here; when the session expires, `SessionExpired` is raised.
"""
from __future__ import annotations

from ..core import Balance, Provider


class BbvaMx(Provider):
    id = "bbva_mx"
    name = "BBVA México"
    auth_fields = ("cookie", "xsrf_token")  # provisional until the endpoint is mapped

    def fetch_balances(self) -> list[Balance]:
        raise NotImplementedError("BBVA México is not implemented yet")
