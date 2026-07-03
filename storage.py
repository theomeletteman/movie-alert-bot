"""
JSON-backed storage layer.

Everything is stored as plain JSON files as required by the spec (no SQL).
All writes are atomic (write to temp file, then rename) so a crash mid-write
never corrupts the data files, which matters a lot for a bot that's run
unattended by a GitHub Actions cron job.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import SEEN_FILE, SUBSCRIPTIONS_FILE, USERS_FILE

logger = logging.getLogger(__name__)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as fh:
            content = fh.read().strip()
            if not content:
                return default
            return json.loads(content)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read %s (%s); using default value", path, exc)
        return default


def _write_json(path: Path, data: Any) -> None:
    """Atomic write: write to a temp file in the same directory, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


@dataclass
class Subscription:
    """One user's subscription to a movie+theatre combination."""

    id: str  # unique subscription id (uuid4 hex)
    user_id: int  # Telegram user id
    chat_id: int  # Telegram chat id to notify
    provider: str  # "bookmyshow" | "district" | ...
    city_id: str
    city_name: str
    movie_id: str
    movie_title: str
    theatre_id: str
    theatre_name: str
    date: str  # ISO date the user subscribed to
    created_at: float = field(default_factory=time.time)
    active: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Subscription":
        return Subscription(**data)


class SubscriptionStore:
    """CRUD operations over subscriptions.json."""

    def __init__(self, path: Path = SUBSCRIPTIONS_FILE) -> None:
        self.path = path

    def _load(self) -> List[Dict[str, Any]]:
        return _read_json(self.path, [])

    def _save(self, items: List[Dict[str, Any]]) -> None:
        _write_json(self.path, items)

    def all(self) -> List[Subscription]:
        return [Subscription.from_dict(item) for item in self._load()]

    def active(self) -> List[Subscription]:
        return [s for s in self.all() if s.active]

    def for_user(self, user_id: int) -> List[Subscription]:
        return [s for s in self.active() if s.user_id == user_id]

    def add(self, subscription: Subscription) -> None:
        items = self._load()
        items.append(subscription.to_dict())
        self._save(items)
        logger.info("Added subscription %s for user %s", subscription.id, subscription.user_id)

    def remove(self, subscription_id: str, user_id: int) -> bool:
        items = self._load()
        new_items = [
            item for item in items
            if not (item["id"] == subscription_id and item["user_id"] == user_id)
        ]
        if len(new_items) == len(items):
            return False
        self._save(new_items)
        logger.info("Removed subscription %s for user %s", subscription_id, user_id)
        return True

    def count_for_user(self, user_id: int) -> int:
        return len(self.for_user(user_id))


class SeenStore:
    """
    Tracks which show ids we've already notified about, per subscription.

    Structure on disk:
        {
          "<subscription_id>": ["show_id_1", "show_id_2", ...],
          ...
        }
    """

    def __init__(self, path: Path = SEEN_FILE) -> None:
        self.path = path

    def _load(self) -> Dict[str, List[str]]:
        return _read_json(self.path, {})

    def _save(self, data: Dict[str, List[str]]) -> None:
        _write_json(self.path, data)

    def get_seen(self, subscription_id: str) -> set:
        return set(self._load().get(subscription_id, []))

    def mark_seen(self, subscription_id: str, show_ids: List[str]) -> None:
        data = self._load()
        existing = set(data.get(subscription_id, []))
        existing.update(show_ids)
        data[subscription_id] = sorted(existing)
        self._save(data)

    def prune(self, valid_subscription_ids: List[str]) -> None:
        """Remove seen-state for subscriptions that no longer exist."""
        data = self._load()
        pruned = {k: v for k, v in data.items() if k in valid_subscription_ids}
        if pruned != data:
            self._save(pruned)


class UserStore:
    """Tracks known Telegram users (for /list, admin visibility, etc.)."""

    def __init__(self, path: Path = USERS_FILE) -> None:
        self.path = path

    def _load(self) -> Dict[str, Any]:
        return _read_json(self.path, {})

    def _save(self, data: Dict[str, Any]) -> None:
        _write_json(self.path, data)

    def upsert(self, user_id: int, username: Optional[str], first_name: Optional[str]) -> None:
        data = self._load()
        data[str(user_id)] = {
            "user_id": user_id,
            "username": username,
            "first_name": first_name,
            "last_seen": time.time(),
        }
        self._save(data)
