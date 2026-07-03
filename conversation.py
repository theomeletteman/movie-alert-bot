"""
/watch guided conversation flow.

Every choice (platform, city, movie, theatre, date) is presented as inline
buttons built from live provider data — the user never types a movie or
theatre name. Provider-specific logic never leaks in here; this module only
talks to the BaseProvider interface.
"""

from __future__ import annotations

import logging
import uuid
from typing import Dict, List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    CommandHandler,
)

from config import get_settings
from providers import provider_display_names, get_provider
from providers.base_provider import BaseProvider, City, Movie, ProviderError, Theatre
from storage import Subscription, SubscriptionStore

logger = logging.getLogger(__name__)

(CHOOSE_PLATFORM, CHOOSE_CITY, CHOOSE_MOVIE, CHOOSE_THEATRE, CHOOSE_DATE) = range(5)

# Telegram callback_data has a 64-byte limit, so we index choices instead of
# embedding provider ids/names directly, and keep the actual objects in
# context.user_data for the duration of the conversation.
CB_PREFIX = "w"


def _kb(options: List[str], row_width: int = 1) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(label, callback_data=f"{CB_PREFIX}:{i}") for i, label in enumerate(options)]
    rows = [buttons[i : i + row_width] for i in range(0, len(buttons), row_width)]
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data=f"{CB_PREFIX}:cancel")])
    return InlineKeyboardMarkup(rows)


def _cookies_for(provider_name: str) -> str:
    settings = get_settings()
    return {
        "bookmyshow": settings.bookmyshow_cookies,
        "district": settings.district_cookies,
    }.get(provider_name, "")


async def _get_live_provider(provider_name: str) -> BaseProvider:
    settings = get_settings()
    return get_provider(
        provider_name,
        cookies=_cookies_for(provider_name),
        headless=settings.headless,
        navigation_timeout_ms=settings.navigation_timeout_ms,
    )


async def watch_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    names = provider_display_names()
    context.user_data["_platform_options"] = list(names.items())  # [(key, display), ...]
    labels = [display for _, display in context.user_data["_platform_options"]]
    await update.message.reply_text(
        "Let's set up a new alert. Which platform do you want to watch?",
        reply_markup=_kb(labels),
    )
    return CHOOSE_PLATFORM


async def choose_platform(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    idx = query.data.split(":", 1)[1]
    if idx == "cancel":
        return await _cancel(update, context)

    key, display = context.user_data["_platform_options"][int(idx)]
    context.user_data["provider"] = key
    context.user_data["provider_display"] = display

    await query.edit_message_text(f"Platform: {display}\n\nFetching cities…")
    try:
        provider = await _get_live_provider(key)
        cities = await provider.get_cities()
        await provider.close()
    except ProviderError as exc:
        logger.error("get_cities failed: %s", exc)
        await query.edit_message_text(
            f"Couldn't load cities from {display} right now ({exc}).\n"
            "This can happen if the site changed or is temporarily blocking requests. "
            "Try again in a bit with /watch."
        )
        return ConversationHandler.END

    if not cities:
        await query.edit_message_text(f"No cities returned by {display}. Try again later with /watch.")
        return ConversationHandler.END

    context.user_data["_city_options"] = cities
    labels = [c.name for c in cities]
    await query.edit_message_text(f"Platform: {display}\n\nChoose your city:")
    await query.message.reply_text("Cities:", reply_markup=_kb(labels, row_width=2))
    return CHOOSE_CITY


async def choose_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    idx = query.data.split(":", 1)[1]
    if idx == "cancel":
        return await _cancel(update, context)

    city: City = context.user_data["_city_options"][int(idx)]
    context.user_data["city"] = city

    await query.edit_message_text(f"City: {city.name}\n\nFetching movies…")
    try:
        provider = await _get_live_provider(context.user_data["provider"])
        movies = await provider.get_movies(city)
        await provider.close()
    except ProviderError as exc:
        logger.error("get_movies failed: %s", exc)
        await query.edit_message_text(f"Couldn't load movies for {city.name} ({exc}). Try /watch again later.")
        return ConversationHandler.END

    if not movies:
        await query.edit_message_text(f"No movies currently listed for {city.name}. Try /watch again later.")
        return ConversationHandler.END

    context.user_data["_movie_options"] = movies
    labels = [m.title for m in movies]
    await query.message.reply_text("Movies:", reply_markup=_kb(labels, row_width=1))
    return CHOOSE_MOVIE


async def choose_movie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    idx = query.data.split(":", 1)[1]
    if idx == "cancel":
        return await _cancel(update, context)

    movie: Movie = context.user_data["_movie_options"][int(idx)]
    context.user_data["movie"] = movie
    city: City = context.user_data["city"]

    await query.edit_message_text(f"Movie: {movie.title}\n\nFetching theatres…")
    try:
        provider = await _get_live_provider(context.user_data["provider"])
        theatres = await provider.get_theatres(city, movie)
        await provider.close()
    except ProviderError as exc:
        logger.error("get_theatres failed: %s", exc)
        await query.edit_message_text(f"Couldn't load theatres for {movie.title} ({exc}). Try /watch again later.")
        return ConversationHandler.END

    if not theatres:
        await query.edit_message_text(
            f"No theatres currently showing {movie.title} in {city.name}. "
            "You can still watch this and get notified once one shows up? "
            "Not yet supported — try again later with /watch."
        )
        return ConversationHandler.END

    context.user_data["_theatre_options"] = theatres
    labels = [t.name for t in theatres]
    await query.message.reply_text("Theatres:", reply_markup=_kb(labels, row_width=1))
    return CHOOSE_THEATRE


async def choose_theatre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    idx = query.data.split(":", 1)[1]
    if idx == "cancel":
        return await _cancel(update, context)

    theatre: Theatre = context.user_data["_theatre_options"][int(idx)]
    context.user_data["theatre"] = theatre
    city: City = context.user_data["city"]
    movie: Movie = context.user_data["movie"]

    await query.edit_message_text(f"Theatre: {theatre.name}\n\nFetching available dates…")
    try:
        provider = await _get_live_provider(context.user_data["provider"])
        dates = await provider.get_available_dates(city, movie, theatre)
        await provider.close()
    except ProviderError as exc:
        logger.error("get_available_dates failed: %s", exc)
        await query.edit_message_text(f"Couldn't load dates for {theatre.name} ({exc}). Try /watch again later.")
        return ConversationHandler.END

    if not dates:
        await query.edit_message_text(f"No selectable dates for {theatre.name} right now. Try /watch again later.")
        return ConversationHandler.END

    context.user_data["_date_options"] = dates
    labels = [d.label for d in dates]
    await query.message.reply_text("Dates:", reply_markup=_kb(labels, row_width=2))
    return CHOOSE_DATE


async def choose_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    idx = query.data.split(":", 1)[1]
    if idx == "cancel":
        return await _cancel(update, context)

    date_option = context.user_data["_date_options"][int(idx)]
    city: City = context.user_data["city"]
    movie: Movie = context.user_data["movie"]
    theatre: Theatre = context.user_data["theatre"]
    provider_key = context.user_data["provider"]

    user = update.effective_user
    chat_id = update.effective_chat.id

    store = SubscriptionStore()
    if store.count_for_user(user.id) >= get_settings().max_subscriptions_per_user:
        await query.edit_message_text(
            "You've hit the maximum number of active alerts. Remove one with /remove first."
        )
        return ConversationHandler.END

    subscription = Subscription(
        id=uuid.uuid4().hex,
        user_id=user.id,
        chat_id=chat_id,
        provider=provider_key,
        city_id=city.id,
        city_name=city.name,
        movie_id=movie.id,
        movie_title=movie.title,
        theatre_id=theatre.id,
        theatre_name=theatre.name,
        date=date_option.date,
    )
    store.add(subscription)

    await query.edit_message_text(
        "✅ Alert saved!\n\n"
        f"Platform: {context.user_data['provider_display']}\n"
        f"Movie: {movie.title}\n"
        f"Theatre: {theatre.name}, {city.name}\n"
        f"Date: {date_option.label}\n\n"
        "You'll get a Telegram message here as soon as a new bookable show shows up. "
        "Use /list to see all your alerts or /remove to delete one."
    )
    context.user_data.clear()
    return ConversationHandler.END


async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    query = update.callback_query
    if query:
        await query.edit_message_text("Cancelled. Run /watch to start again.")
    else:
        await update.message.reply_text("Cancelled. Run /watch to start again.")
    return ConversationHandler.END


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _cancel(update, context)


def build_watch_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("watch", watch_start)],
        states={
            CHOOSE_PLATFORM: [CallbackQueryHandler(choose_platform, pattern=f"^{CB_PREFIX}:")],
            CHOOSE_CITY: [CallbackQueryHandler(choose_city, pattern=f"^{CB_PREFIX}:")],
            CHOOSE_MOVIE: [CallbackQueryHandler(choose_movie, pattern=f"^{CB_PREFIX}:")],
            CHOOSE_THEATRE: [CallbackQueryHandler(choose_theatre, pattern=f"^{CB_PREFIX}:")],
            CHOOSE_DATE: [CallbackQueryHandler(choose_date, pattern=f"^{CB_PREFIX}:")],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        name="watch_conversation",
        persistent=False,
    )
