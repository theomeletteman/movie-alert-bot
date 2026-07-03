"""Central configuration, loaded from environment variables / GitHub Secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent

USERS_FILE = BASE_DIR / "users.json"
SUBSCRIPTIONS_FILE = BASE_DIR / "subscriptions.json"
SEEN_FILE = BASE_DIR / "seen.json"
CONFIG_FILE = BASE_DIR / "config.json"


@dataclass(frozen=True)
class Settings:
    bot_token: str
    log_level: str
    # Optional cookies some providers may need (comma-free JSON string), kept
    # generic so any provider can read what it needs without new secrets
    # being required for every addition.
    bookmyshow_cookies: str
    district_cookies: str
    # Playwright launch options.
    headless: bool
    navigation_timeout_ms: int
    max_subscriptions_per_user: int

    @staticmethod
    def load() -> "Settings":
        token = os.environ.get("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "BOT_TOKEN environment variable is not set. "
                "Set it as a GitHub Secret or in your local .env file."
            )
        return Settings(
            bot_token=token,
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            bookmyshow_cookies=os.environ.get("BOOKMYSHOW_COOKIES", ""),
            district_cookies=os.environ.get("DISTRICT_COOKIES", ""),
            headless=os.environ.get("PLAYWRIGHT_HEADLESS", "true").lower() != "false",
            navigation_timeout_ms=int(os.environ.get("NAVIGATION_TIMEOUT_MS", "30000")),
            max_subscriptions_per_user=int(os.environ.get("MAX_SUBSCRIPTIONS_PER_USER", "20")),
        )


_settings: "Settings | None" = None


def get_settings() -> Settings:
    """
    Lazily load and cache settings. Lazy on purpose: modules like the
    provider classes can be imported (e.g. for tests) without requiring
    BOT_TOKEN to be set; only code paths that actually need the token
    (bot.py, checker.py) trigger the load.
    """
    global _settings
    if _settings is None:
        _settings = Settings.load()
    return _settings
