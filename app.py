"""
Combined entrypoint: runs the interactive Telegram bot AND the periodic
availability check in a single process.

Why this exists: BookMyShow blocks requests from cloud/datacenter IPs
(confirmed on Oracle Cloud, and GitHub Actions runners are datacenter IPs
too, so that path was never going to work reliably either). Running
everything from a home PC's ordinary residential connection avoids that.
And since there's no longer a reason to split "checking" onto a separate
scheduler, one process is simpler than bot.py + checker.py + a GitHub
Actions workflow -- fewer moving parts to debug.

Run with:
    python app.py

Leave the terminal window open. Ctrl+C stops everything -- the bot and
the periodic checks both live in this one process. bot.py and checker.py
still exist and still work standalone if you ever want them (e.g. running
checker.py once by hand to test), but for day-to-day use on your PC, this
is the one script to run.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot import build_application
from checker import run_check
from utils import setup_logging

logger = setup_logging()

# Same 5-minute cadence the GitHub Actions cron used to run at.
CHECK_INTERVAL_SECONDS = 5 * 60
# Wait a little after startup before the first check, so the bot has time
# to finish connecting to Telegram first.
FIRST_CHECK_DELAY_SECONDS = 15


async def scheduled_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called on a repeating timer by the bot's own job queue."""
    logger.info("Running scheduled availability check...")
    try:
        await run_check()
    except Exception:  # noqa: BLE001
        # A single bad check (site hiccup, transient network issue) should
        # never take down the whole process -- just log it and try again
        # on the next cycle.
        logger.exception("Scheduled check failed; will retry on the next cycle.")


def main() -> None:
    application = build_application()

    if application.job_queue is None:
        raise RuntimeError(
            "JobQueue isn't available. Install it with:\n"
            '    pip install "python-telegram-bot[job-queue]"\n'
            "(this is already in requirements.txt -- run pip install -r requirements.txt again)"
        )

    application.job_queue.run_repeating(
        scheduled_check,
        interval=CHECK_INTERVAL_SECONDS,
        first=FIRST_CHECK_DELAY_SECONDS,
    )

    logger.info(
        "Starting bot + scheduled checker in one process (checking every %d seconds)...",
        CHECK_INTERVAL_SECONDS,
    )
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
