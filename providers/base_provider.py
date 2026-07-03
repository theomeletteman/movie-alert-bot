"""
Base provider interface.

Every ticketing provider (BookMyShow, District, and anything added later)
must implement this exact interface. The bot and checker code talk ONLY to
this interface and never contain provider-specific logic.

All discovery methods must hit the live site/API at call time. Nothing here
should ever be hardcoded (cities, movies, theatres, etc.).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class City:
    id: str
    name: str


@dataclass(frozen=True)
class Movie:
    id: str
    title: str
    # Free-form extra data a provider might need later to build URLs
    # (e.g. BookMyShow event codes, language/format info).
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Theatre:
    id: str
    name: str
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ShowDate:
    """A selectable date for a movie+theatre combination."""
    date: str  # ISO format YYYY-MM-DD
    label: str  # human friendly label, e.g. "Today, 3 Jul"


@dataclass(frozen=True)
class Show:
    """A single bookable show (one screen, one time, one date)."""
    show_id: str  # stable unique id for de-duplication (see build_show_id)
    movie_id: str
    theatre_id: str
    theatre_name: str
    date: str
    time: str
    screen: Optional[str] = None
    booking_url: Optional[str] = None
    bookable: bool = True
    extra: Dict[str, Any] = field(default_factory=dict)


def build_show_id(provider: str, movie_id: str, theatre_id: str, date: str, time: str, screen: Optional[str] = None) -> str:
    """
    Deterministic, stable identifier for a show. Used as the key in seen.json
    so we never notify twice for the same show and can detect "new" shows by
    simple set difference.
    """
    parts = [provider, movie_id, theatre_id, date, time, screen or ""]
    return "|".join(str(p) for p in parts)


class ProviderError(Exception):
    """Raised when a provider fails to fetch data (network, parsing, blocked, etc.)."""


class BaseProvider(abc.ABC):
    """Abstract interface every provider must implement identically."""

    #: Short machine-readable name, e.g. "bookmyshow" / "district".
    name: str = "base"

    #: Human readable display name, e.g. "BookMyShow".
    display_name: str = "Base Provider"

    @abc.abstractmethod
    async def get_cities(self) -> List[City]:
        """Return all cities the provider currently supports."""
        raise NotImplementedError

    @abc.abstractmethod
    async def get_movies(self, city: City) -> List[Movie]:
        """Return all movies currently listed for a given city."""
        raise NotImplementedError

    @abc.abstractmethod
    async def get_theatres(self, city: City, movie: Movie) -> List[Theatre]:
        """Return all theatres currently showing this movie in this city."""
        raise NotImplementedError

    @abc.abstractmethod
    async def get_available_dates(self, city: City, movie: Movie, theatre: Theatre) -> List[ShowDate]:
        """Return the dates for which showtimes can currently be queried."""
        raise NotImplementedError

    @abc.abstractmethod
    async def get_shows(self, city: City, movie: Movie, theatre: Theatre, date: str) -> List[Show]:
        """Return all currently bookable shows for movie+theatre+date."""
        raise NotImplementedError

    @abc.abstractmethod
    def get_booking_url(self, show: Show) -> str:
        """Return the URL a user should open to book this show."""
        raise NotImplementedError

    async def close(self) -> None:
        """Optional cleanup hook (closing browser contexts, HTTP sessions, etc.)."""
        return None
