"""Registry of supported institutions.

Each entry points at its module instead of importing it, so a provider is only loaded when used.
"""
from __future__ import annotations

from importlib import import_module

from ..core import Provider

# id -> (module, class)
PROVIDERS: dict[str, tuple[str, str]] = {
    "bitso": ("finscraper_mx.institutions.bitso", "Bitso"),
    "didi": ("finscraper_mx.institutions.didi", "Didi"),
    "gbm": ("finscraper_mx.institutions.gbm", "Gbm"),
    "klar": ("finscraper_mx.institutions.klar", "Klar"),
    "mercado_pago": ("finscraper_mx.institutions.mercado_pago", "MercadoPago"),
    "mifel": ("finscraper_mx.institutions.mifel", "Mifel"),
    "openbank": ("finscraper_mx.institutions.openbank", "Openbank"),
    "plata": ("finscraper_mx.institutions.plata", "Plata"),
    "skandia": ("finscraper_mx.institutions.skandia", "Skandia"),
}


def get_provider(institution: str) -> type[Provider]:
    try:
        module, cls = PROVIDERS[institution]
    except KeyError:
        known = ", ".join(sorted(PROVIDERS))
        raise KeyError(f"unknown institution {institution!r} (known: {known})") from None
    return getattr(import_module(module), cls)
