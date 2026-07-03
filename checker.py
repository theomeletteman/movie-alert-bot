"""
Periodic availability checker.

This is what GitHub Actions runs every 5 minutes (see
.github/workflows/check.yml). It does NOT run the interactive bot — it just:

  1. Loads all active subscriptions.
  2. For each one, asks the relevant provider for currently bookable shows.
  3. Diffs those against what we've already notified about (seen.json).
  4. Sends a Telegram message for anything new, then records it as seen.

Subscriptions are grouped by provider so each provider's browser is only
launched once per run, not once per subscription.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Dict, List

from telegram import Bot
from telegram.error import TelegramError

from config import get_settings
from providers import get_provider
from providers.base_provider import BaseProvider, City, Movie, ProviderError, Show, Theatre
from storage import SeenStore, Subscription, SubscriptionStore
from utils import setup_logging

logger = setup_logging()

NOTIFICATION_TEMPLATE = (
    "🎬 *Ticket Available*\n\n"
    "*Platform:* {platform}\n"
    "*Movie:* {movie}\n"
    "*Theatre:* {theatre}\n"
    "*Date:* {date}\n"
    "*Time:* {time}\n\n"
    "*Book Here:* {url}"
)


def _cookies_for(provider_name: str) -> str:
    settings = get_settings()
    return {
        "bookmyshow": settings.bookmyshow_cookies,
        "district": settings.district_cookies,
    }.get(provider_name, "")


async def _check_subscription(
    provider: BaseProvider, subscription: Subscription, seen_store: SeenStore
) -> List[Show]:
    """Fetch current shows for one subscription and return the NEW ones."""
    city = City(id=subscription.city_id, name=subscription.city_name)
    movie = Movie(id=subscription.movie_id, title=subscription.movie_title)
    theatre = Theatre(id=subscription.theatre_id, name=subscription.theatre_name)

    shows = await provider.get_shows(city, movie, theatre, subscription.date)
    bookable_shows = [s for s in shows if s.bookable]

    already_seen = seen_store.get_seen(subscription.id)
    new_shows = [s for s in bookable_shows if s.show_id not in already_seen]
    return new_shows


async def _notify(bot: Bot, subscription: Subscription, show: Show, platform_display: str) -> bool:
    text = NOTIFICATION_TEMPLATE.format(
        platform=platform_display,
        movie=subscription.movie_title,
        theatre=subscription.theatre_name,
        date=show.date,
        time=show.time,
        url=show.booking_url or "",
    )
    try:
        await bot.send_message(chat_id=subscription.chat_id, text=text, parse_mode="Markdown")
        return True
    except TelegramError as exc:
        logger.error(
            "Failed to notify user %s for subscription %s: %s",
            subscription.user_id,
            subscription.id,
            exc,
        )
        return False


async def run_check() -> None:
    settings = get_settings()
    bot = Bot(token=settings.bot_token)

    sub_store = SubscriptionStore()
    seen_store = SeenStore()
    subscriptions = sub_store.active()

    if not subscriptions:
        logger.info("No active subscriptions. Nothing to check.")
        seen_store.prune([])
        return

    seen_store.prune([s.id for s in subscriptions])

    by_provider: Dict[str, List[Subscription]] = defaultdict(list)
    for sub in subscriptions:
        by_provider[sub.provider].append(sub)

    from providers import provider_display_names

    display_names = provider_display_names()
    total_notified = 0
    total_errors = 0

    for provider_name, subs in by_provider.items():
        logger.info("Checking %d subscription(s) for provider=%s", len(subs), provider_name)
        try:
            provider = get_provider(
                provider_name,
                cookies=_cookies_for(provider_name),
                headless=settings.headless,
                navigation_timeout_ms=settings.navigation_timeout_ms,
            )
        except ValueError as exc:
            logger.error("Skipping unknown provider %s: %s", provider_name, exc)
            continue

        try:
            for sub in subs:
                try:
                    new_shows = await _check_subscription(provider, sub, seen_store)
                except ProviderError as exc:
                    logger.warning(
                        "Provider error checking subscription %s (%s / %s): %s",
                        sub.id,
                        sub.movie_title,
                        sub.theatre_name,
                        exc,
                    )
                    total_errors += 1
                    continue
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "Unexpected error checking subscription %s: %s", sub.id, exc
                    )
                    total_errors += 1
                    continue

                if not new_shows:
                    continue

                logger.info(
                    "Found %d new bookable show(s) for subscription %s (%s)",
                    len(new_shows),
                    sub.id,
                    sub.movie_title,
                )
                notified_ids = []
                for show in new_shows:
                    ok = await _notify(bot, sub, show, display_names.get(provider_name, provider_name))
                    if ok:
                        notified_ids.append(show.show_id)
                        total_notified += 1
                if notified_ids:
                    seen_store.mark_seen(sub.id, notified_ids)
        finally:
            await provider.close()

    logger.info("Check complete. Notifications sent: %d. Errors: %d.", total_notified, total_errors)


def main() -> None:
    asyncio.run(run_check())


if __name__ == "__main__":
    main()
