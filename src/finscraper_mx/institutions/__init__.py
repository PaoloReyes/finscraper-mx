"""Registry of supported institutions.

Each entry points at its module instead of importing it, so an institution's dependencies (its extra)
are only needed when that institution is actually used.
"""
from __future__ import annotations

from importlib import import_module

from ..core import Provider

# id -> (module, class, extra that installs its dependencies)
PROVIDERS: dict[str, tuple[str, str, str]] = {
    "bitso": ("finscraper_mx.institutions.bitso", "Bitso", "bitso"),
}


def get_provider(institution: str) -> type[Provider]:
    try:
        module, cls, extra = PROVIDERS[institution]
    except KeyError:
        known = ", ".join(sorted(PROVIDERS))
        raise KeyError(f"unknown institution {institution!r} (known: {known})") from None
    try:
        return getattr(import_module(module), cls)
    except ImportError as error:
        raise ImportError(f'{institution} needs extra dependencies: pip install "finscraper-mx[{extra}]"') from error
