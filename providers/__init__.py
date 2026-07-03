"""
Provider registry.

To add a new provider:
  1. Create providers/<name>.py implementing BaseProvider (see base_provider.py).
  2. Import it and add it to PROVIDER_CLASSES below.

Nothing else in the bot needs to change — conversation.py and checker.py
both iterate PROVIDER_CLASSES / use get_provider() generically.
"""

from __future__ import annotations

from typing import Dict, Type

from providers.base_provider import BaseProvider
from providers.bookmyshow import BookMyShowProvider
from providers.district import DistrictProvider

PROVIDER_CLASSES: Dict[str, Type[BaseProvider]] = {
    BookMyShowProvider.name: BookMyShowProvider,
    DistrictProvider.name: DistrictProvider,
}


def get_provider(name: str, cookies: str = "", headless: bool = True, navigation_timeout_ms: int = 30000) -> BaseProvider:
    """Instantiate a provider by its registry name."""
    try:
        cls = PROVIDER_CLASSES[name]
    except KeyError as exc:
        raise ValueError(f"Unknown provider '{name}'. Known providers: {list(PROVIDER_CLASSES)}") from exc
    return cls(cookies=cookies, headless=headless, navigation_timeout_ms=navigation_timeout_ms)


def provider_display_names() -> Dict[str, str]:
    return {name: cls.display_name for name, cls in PROVIDER_CLASSES.items()}
