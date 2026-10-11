"""Registry of supported institutions.

Each entry points at its module instead of importing it, so a provider is only loaded when used.
"""
from __future__ import annotations

from importlib import import_module

from ..core import Provider

# id -> (module, class)
PROVIDERS: dict[str, tuple[str, str]] = {
    "bitso": ("finscraper_mx.institutions.bitso", "Bitso"),
    "gbm": ("finscraper_mx.institutions.gbm", "Gbm"),
    "mercado_pago": ("finscraper_mx.institutions.mercado_pago", "MercadoPago"),
}


def get_provider(institution: str) -> type[Provider]:
    try:
        module, cls = PROVIDERS[institution]
    except KeyError:
        known = ", ".join(sorted(PROVIDERS))
        raise KeyError(f"unknown institution {institution!r} (known: {known})") from None
    return getattr(import_module(module), cls)
