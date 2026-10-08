"""Registry of supported institutions. Add new providers to `PROVIDERS`."""
from __future__ import annotations

from ..core import Provider
from .bbva_mx import BbvaMx

PROVIDERS: dict[str, type[Provider]] = {provider.id: provider for provider in (BbvaMx,)}


def get_provider(institution: str) -> type[Provider]:
    try:
        return PROVIDERS[institution]
    except KeyError:
        known = ", ".join(sorted(PROVIDERS))
        raise KeyError(f"unknown institution {institution!r} (known: {known})") from None
