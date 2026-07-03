"""
BookMyShow provider.

BookMyShow has no official public API for showtimes/availability. This
provider drives a real (headless) browser with Playwright, loads the same
pages a human visitor would see, and extracts data either from the
server-rendered JSON payload embedded in the page or, if that marker isn't
present, from the visible DOM as a fallback.

IMPORTANT — read before relying on this in production:
The exact JSON marker id and CSS selectors below are BMS's *current*
structure as commonly documented, but BMS changes its front-end without
notice and runs bot-detection that can occasionally block automated
traffic entirely. Two things follow from that:

  1. Before your first real run, use `scripts/inspect_provider.py` (see
     README) to dump a live page's HTML/JSON and confirm the selectors
     below still match. Update `_JSON_MARKER_ID` / the CSS selectors in
     `_parse_movies_from_dom` etc. if they don't.
  2. If you start seeing ProviderError("blocked") or empty results across
     the board, that almost certainly means BMS is challenging the
     request. Do NOT try to defeat a CAPTCHA or fingerprinting challenge —
     back off (increase CHECK interval, reduce subscription count) instead.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from bs4 import BeautifulSoup

from providers.base_provider import City, Movie, ProviderError, Show, ShowDate, Theatre, build_show_id
from providers.playwright_provider import PlaywrightProvider
from utils import async_retry, extract_json_blob

logger = logging.getLogger(__name__)

BASE_URL = "https://in.bookmyshow.com"

# Server-rendered JSON marker BMS's app shell commonly uses. Verify this
# against a live page dump before depending on it (see module docstring).
_JSON_MARKER_ID = "__NEXT_DATA__"


class BookMyShowProvider(PlaywrightProvider):
    name = "bookmyshow"
    display_name = "BookMyShow"

    @property
    def cookie_domain(self) -> str:
        return ".bookmyshow.com"

    # ------------------------------------------------------------------
    # Cities
    # ------------------------------------------------------------------
    @async_retry(attempts=3, exceptions=(ProviderError,))
    async def get_cities(self) -> List[City]:
        html = await self.fetch_html(f"{BASE_URL}/explore/home", wait_selector="body")
        blob = extract_json_blob(html, _JSON_MARKER_ID)
        cities: List[City] = []

        if blob:
            cities = self._parse_cities_from_json(blob)

        if not cities:
            cities = self._parse_cities_from_dom(html)

        if not cities:
            raise ProviderError(
                "bookmyshow: could not discover any cities. The page structure "
                "likely changed — re-check _JSON_MARKER_ID / _parse_cities_from_dom."
            )
        logger.info("bookmyshow: discovered %d cities", len(cities))
        return cities

    @staticmethod
    def _parse_cities_from_json(blob: Dict[str, Any]) -> List[City]:
        """Best-effort walk of the embedded JSON looking for a city list."""
        cities: List[City] = []
        try:
            candidates = blob.get("props", {}).get("pageProps", {}).get("cities", [])
            for c in candidates:
                cid = str(c.get("id") or c.get("code") or c.get("slug") or "")
                cname = c.get("name") or c.get("title")
                if cid and cname:
                    cities.append(City(id=cid, name=cname))
        except AttributeError:
            pass
        return cities

    @staticmethod
    def _parse_cities_from_dom(html: str) -> List[City]:
        soup = BeautifulSoup(html, "html.parser")
        cities: List[City] = []
        # Fallback: BMS historically lists popular cities as links like
        # /explore/movies-mumbai. Adjust this selector after inspecting a
        # live page — this is a reasonable starting guess, not a guarantee.
        for link in soup.select('a[href*="/explore/movies-"]'):
            href = link.get("href", "")
            match = re.search(r"/explore/movies-([a-z0-9\-]+)", href)
            if not match:
                continue
            slug = match.group(1)
            name = link.get_text(strip=True) or slug.replace("-", " ").title()
            cities.append(City(id=slug, name=name))
        # De-duplicate while preserving order.
        seen = set()
        deduped = []
        for c in cities:
            if c.id not in seen:
                seen.add(c.id)
                deduped.append(c)
        return deduped

    # ------------------------------------------------------------------
    # Movies
    # ------------------------------------------------------------------
    @async_retry(attempts=3, exceptions=(ProviderError,))
    async def get_movies(self, city: City) -> List[Movie]:
        url = f"{BASE_URL}/explore/movies-{city.id}"
        html = await self.fetch_html(url, wait_selector="body")
        blob = extract_json_blob(html, _JSON_MARKER_ID)

        movies: List[Movie] = []
        if blob:
            movies = self._parse_movies_from_json(blob)
        if not movies:
            movies = self._parse_movies_from_dom(html)

        if not movies:
            raise ProviderError(
                f"bookmyshow: no movies found for city={city.id}. "
                "Either nothing is currently showing, or selectors need updating."
            )
        logger.info("bookmyshow: found %d movies for %s", len(movies), city.name)
        return movies

    @staticmethod
    def _parse_movies_from_json(blob: Dict[str, Any]) -> List[Movie]:
        movies: List[Movie] = []
        try:
            candidates = blob.get("props", {}).get("pageProps", {}).get("movies", [])
            for m in candidates:
                mid = str(m.get("EventCode") or m.get("id") or m.get("code") or "")
                title = m.get("EventTitle") or m.get("title") or m.get("name")
                if mid and title:
                    movies.append(
                        Movie(
                            id=mid,
                            title=title,
                            extra={"slug": m.get("slug") or m.get("EventSlug")},
                        )
                    )
        except AttributeError:
            pass
        return movies

    @staticmethod
    def _parse_movies_from_dom(html: str) -> List[Movie]:
        soup = BeautifulSoup(html, "html.parser")
        movies: List[Movie] = []
        # Fallback selector guess: movie cards linking to /movies/<city>/<slug>/<code>
        for link in soup.select('a[href*="/movies/"]'):
            href = link.get("href", "")
            match = re.search(r"/movies/[a-z0-9\-]+/([a-z0-9\-]+)/([A-Za-z0-9]+)", href)
            if not match:
                continue
            slug, code = match.group(1), match.group(2)
            title_el = link.select_one("img")
            title = (title_el.get("alt") if title_el else None) or slug.replace("-", " ").title()
            movies.append(Movie(id=code, title=title, extra={"slug": slug}))
        seen = set()
        deduped = []
        for m in movies:
            if m.id not in seen:
                seen.add(m.id)
                deduped.append(m)
        return deduped

    # ------------------------------------------------------------------
    # Theatres
    # ------------------------------------------------------------------
    def _buytickets_url(self, city: City, movie: Movie, date: Optional[str] = None) -> str:
        slug = movie.extra.get("slug") or self._slugify(movie.title)
        url = f"{BASE_URL}/buytickets/{slug}-{city.id}/movie-{city.id}-{movie.id}-MT"
        if date:
            url += f"?dateCode={quote(date)}"
        return url

    @staticmethod
    def _slugify(title: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")

    @async_retry(attempts=3, exceptions=(ProviderError,))
    async def get_theatres(self, city: City, movie: Movie) -> List[Theatre]:
        url = self._buytickets_url(city, movie)
        html = await self.fetch_html(url, wait_selector="body")
        blob = extract_json_blob(html, _JSON_MARKER_ID)

        theatres: List[Theatre] = []
        if blob:
            theatres = self._parse_theatres_from_json(blob)
        if not theatres:
            theatres = self._parse_theatres_from_dom(html)

        if not theatres:
            logger.warning(
                "bookmyshow: no theatres found for movie=%s city=%s "
                "(could mean nothing is currently bookable, or selectors need updating)",
                movie.title,
                city.name,
            )
        return theatres

    @staticmethod
    def _parse_theatres_from_json(blob: Dict[str, Any]) -> List[Theatre]:
        theatres: List[Theatre] = []
        try:
            venues = blob.get("props", {}).get("pageProps", {}).get("venues", [])
            for v in venues:
                vid = str(v.get("VenueCode") or v.get("id") or "")
                vname = v.get("VenueName") or v.get("name")
                if vid and vname:
                    theatres.append(Theatre(id=vid, name=vname))
        except AttributeError:
            pass
        return theatres

    @staticmethod
    def _parse_theatres_from_dom(html: str) -> List[Theatre]:
        soup = BeautifulSoup(html, "html.parser")
        theatres: List[Theatre] = []
        # Fallback selector guess: venue blocks commonly carry a data-venue-code
        # attribute. Verify and adjust against a live page dump.
        for block in soup.select("[data-venue-code]"):
            vid = block.get("data-venue-code", "")
            name_el = block.select_one(".venue-name, [class*=venueName]")
            name = name_el.get_text(strip=True) if name_el else vid
            if vid:
                theatres.append(Theatre(id=vid, name=name))
        return theatres

    # ------------------------------------------------------------------
    # Dates
    # ------------------------------------------------------------------
    @async_retry(attempts=3, exceptions=(ProviderError,))
    async def get_available_dates(self, city: City, movie: Movie, theatre: Theatre) -> List[ShowDate]:
        url = self._buytickets_url(city, movie)
        html = await self.fetch_html(url, wait_selector="body")
        blob = extract_json_blob(html, _JSON_MARKER_ID)

        dates: List[ShowDate] = []
        if blob:
            try:
                raw_dates = blob.get("props", {}).get("pageProps", {}).get("dates", [])
                for d in raw_dates:
                    date_code = d.get("DateCode") or d.get("date")
                    label = d.get("Display") or d.get("label") or date_code
                    if date_code:
                        dates.append(ShowDate(date=date_code, label=label))
            except AttributeError:
                pass

        if not dates:
            soup = BeautifulSoup(html, "html.parser")
            for el in soup.select("[data-date-code]"):
                date_code = el.get("data-date-code", "")
                label = el.get_text(strip=True) or date_code
                if date_code:
                    dates.append(ShowDate(date=date_code, label=label))

        if not dates:
            raise ProviderError(
                f"bookmyshow: no selectable dates found for movie={movie.title} "
                f"theatre={theatre.name}. Selectors likely need updating."
            )
        return dates

    # ------------------------------------------------------------------
    # Shows
    # ------------------------------------------------------------------
    @async_retry(attempts=3, exceptions=(ProviderError,))
    async def get_shows(self, city: City, movie: Movie, theatre: Theatre, date: str) -> List[Show]:
        url = self._buytickets_url(city, movie, date=date)
        html = await self.fetch_html(url, wait_selector="body")
        blob = extract_json_blob(html, _JSON_MARKER_ID)

        shows: List[Show] = []
        if blob:
            shows = self._parse_shows_from_json(blob, city, movie, theatre, date)
        if not shows:
            shows = self._parse_shows_from_dom(html, movie, theatre, date)

        logger.info(
            "bookmyshow: %d shows for movie=%s theatre=%s date=%s",
            len(shows),
            movie.title,
            theatre.name,
            date,
        )
        return shows

    def _parse_shows_from_json(
        self, blob: Dict[str, Any], city: City, movie: Movie, theatre: Theatre, date: str
    ) -> List[Show]:
        shows: List[Show] = []
        try:
            venues = blob.get("props", {}).get("pageProps", {}).get("venues", [])
            for v in venues:
                if str(v.get("VenueCode")) != theatre.id:
                    continue
                for showtime in v.get("ShowTimes", []):
                    time_str = showtime.get("ShowTime") or showtime.get("time")
                    screen = showtime.get("Attributes") or showtime.get("screen")
                    status = (showtime.get("Status") or "").lower()
                    bookable = status not in ("soldout", "sold_out", "unavailable")
                    if not time_str:
                        continue
                    show_id = build_show_id(self.name, movie.id, theatre.id, date, time_str, screen)
                    shows.append(
                        Show(
                            show_id=show_id,
                            movie_id=movie.id,
                            theatre_id=theatre.id,
                            theatre_name=theatre.name,
                            date=date,
                            time=time_str,
                            screen=screen,
                            booking_url=self._buytickets_url(city, movie, date=date),
                            bookable=bookable,
                        )
                    )
        except AttributeError:
            pass
        return shows

    def _parse_shows_from_dom(self, html: str, movie: Movie, theatre: Theatre, date: str) -> List[Show]:
        soup = BeautifulSoup(html, "html.parser")
        shows: List[Show] = []
        # Fallback selector guess: showtime chips/buttons with a time label
        # and a sold-out class toggle. Verify against a live page dump.
        for el in soup.select("[data-showtime]"):
            time_str = el.get("data-showtime", "") or el.get_text(strip=True)
            classes = " ".join(el.get("class", []))
            bookable = "sold" not in classes.lower() and "disabled" not in classes.lower()
            if not time_str:
                continue
            show_id = build_show_id(self.name, movie.id, theatre.id, date, time_str)
            shows.append(
                Show(
                    show_id=show_id,
                    movie_id=movie.id,
                    theatre_id=theatre.id,
                    theatre_name=theatre.name,
                    date=date,
                    time=time_str,
                    bookable=bookable,
                )
            )
        return shows

    def get_booking_url(self, show: Show) -> str:
        return show.booking_url or f"{BASE_URL}/movies"
