"""
Main Telegram bot entrypoint.

Run locally with:  python bot.py
This process is the interactive side (handles /start, /watch, /list,
/remove, /help). It does NOT do the periodic availability checking — that's
checker.py, which runs on a schedule via GitHub Actions (see
.github/workflows/check.yml) so no VPS is needed for polling.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from config import get_settings
from conversation import build_watch_conversation_handler
from providers import provider_display_names
from storage import SubscriptionStore, UserStore
from utils import setup_logging

logger = setup_logging()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    UserStore().upsert(user.id, user.username, user.first_name)
    await update.message.reply_text(
        "🎬 *Movie Ticket Alert Bot*\n\n"
        "I watch BookMyShow and District for you and message you the moment "
        "a show you care about becomes bookable. I never book anything myself.\n\n"
        "*Commands*\n"
        "/watch — set up a new alert\n"
        "/list — see your active alerts\n"
        "/remove — delete an alert\n"
        "/help — show this again",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    names = provider_display_names()
    supported = ", ".join(names.values())
    await update.message.reply_text(
        "*How it works*\n"
        "1. /watch — pick a platform, city, movie, theatre and date from live lists.\n"
        "2. A background check (every 5 minutes) compares current availability against "
        "what you've already been told about.\n"
        "3. The moment something new becomes bookable, I message you here with a link.\n\n"
        f"*Supported platforms:* {supported}\n\n"
        "*Other commands*\n"
        "/list — see your active alerts\n"
        "/remove — delete an alert",
        parse_mode="Markdown",
    )


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    subs = SubscriptionStore().for_user(user.id)
    if not subs:
        await update.message.reply_text("You don't have any active alerts yet. Use /watch to create one.")
        return
    names = provider_display_names()
    lines = ["*Your active alerts:*\n"]
    for s in subs:
        lines.append(
            f"• [{names.get(s.provider, s.provider)}] {s.movie_title} — "
            f"{s.theatre_name}, {s.city_name} — {s.date}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    subs = SubscriptionStore().for_user(user.id)
    if not subs:
        await update.message.reply_text("You don't have any active alerts to remove.")
        return
    names = provider_display_names()
    buttons = [
        [
            InlineKeyboardButton(
                f"{s.movie_title} — {s.theatre_name} ({s.date})",
                callback_data=f"remove:{s.id}",
            )
        ]
        for s in subs
    ]
    await update.message.reply_text(
        "Which alert do you want to remove?", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def remove_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    sub_id = query.data.split(":", 1)[1]
    user = update.effective_user
    removed = SubscriptionStore().remove(sub_id, user.id)
    if removed:
        await query.edit_message_text("🗑️ Alert removed.")
    else:
        await query.edit_message_text("Couldn't find that alert (it may already be removed).")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled exception while processing update %s", update, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Something went wrong on my end. Please try again in a moment."
            )
        except Exception:  # noqa: BLE001
            pass


def build_application() -> Application:
    settings = get_settings()
    application = Application.builder().token(settings.bot_token).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("remove", remove_command))
    application.add_handler(CallbackQueryHandler(remove_callback, pattern="^remove:"))
    application.add_handler(build_watch_conversation_handler())
    application.add_error_handler(on_error)

    return application


def main() -> None:
    application = build_application()
    logger.info("Bot starting (polling mode)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
