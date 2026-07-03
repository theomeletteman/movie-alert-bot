"""Shared utilities: logging, retries, and a small Playwright helper."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import re
from typing import Any, Awaitable, Callable, Optional, TypeVar

T = TypeVar("T")


def setup_logging() -> logging.Logger:
    """Configure root logging once. Safe to call multiple times."""
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Quiet down noisy third-party loggers.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)
    return logging.getLogger("movie-alert-bot")


def async_retry(
    attempts: int = 3,
    base_delay: float = 2.0,
    exceptions: tuple = (Exception,),
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """
    Retry an async function with exponential backoff.

    Used around every network / browser call to a provider so a single
    flaky request doesn't crash the whole check run.
    """

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            logger = logging.getLogger(fn.__module__)
            last_exc: Optional[BaseException] = None
            for attempt in range(1, attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except exceptions as exc:  # noqa: BLE001
                    last_exc = exc
                    if attempt == attempts:
                        logger.error(
                            "%s failed after %d attempts: %s", fn.__qualname__, attempts, exc
                        )
                        raise
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "%s attempt %d/%d failed (%s); retrying in %.1fs",
                        fn.__qualname__,
                        attempt,
                        attempts,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)
            # Should be unreachable, but keeps type checkers happy.
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator


def extract_json_blob(html: str, marker_id: str) -> Optional[dict]:
    """
    Extract a JSON payload embedded in a <script id="{marker_id}" ...>...</script>
    tag, which is how most Next.js / Nuxt-style sites (including BMS and
    District) ship server-rendered data to the client.

    Returns None if the marker isn't found or the content isn't valid JSON.
    This is intentionally generic since the exact marker id/shape can change
    without notice -- see providers/bookmyshow.py and providers/district.py
    for where this is used and what fallback is applied when it fails.
    """
    pattern = re.compile(
        rf'<script[^>]+id=["\']{re.escape(marker_id)}["\'][^>]*>(.*?)</script>',
        re.DOTALL,
    )
    match = pattern.search(html)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def find_first_json_ld(html: str) -> Optional[dict]:
    """Fallback: extract the first application/ld+json block, if present."""
    pattern = re.compile(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        re.DOTALL,
    )
    for match in pattern.finditer(html):
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
    return None
