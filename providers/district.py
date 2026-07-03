"""
District provider (district.in — Zomato's ticketing/events platform).

Same situation as BookMyShow: no official public API for showtimes. This
provider uses the same Playwright-driven strategy — read the embedded
server-rendered JSON where possible, fall back to DOM scraping otherwise.

Read providers/bookmyshow.py's module docstring first — the same caveats
apply here: verify `_JSON_MARKER_ID` and the DOM fallback selectors against
a live page dump (see README / scripts/inspect_provider.py) before relying
on this in production, and never attempt to defeat CAPTCHA/anti-bot
challenges if District starts blocking requests — back off instead.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from providers.base_provider import City, Movie, ProviderError, Show, ShowDate, Theatre, build_show_id
from providers.playwright_provider import PlaywrightProvider
from utils import async_retry, extract_json_blob

logger = logging.getLogger(__name__)

BASE_URL = "https://www.district.in"
_JSON_MARKER_ID = "__NEXT_DATA__"


class DistrictProvider(PlaywrightProvider):
    name = "district"
    display_name = "District"

    @property
    def cookie_domain(self) -> str:
        return ".district.in"

    # ------------------------------------------------------------------
    # Cities
    # ------------------------------------------------------------------
    @async_retry(attempts=3, exceptions=(ProviderError,))
    async def get_cities(self) -> List[City]:
        html = await self.fetch_html(f"{BASE_URL}/movies", wait_selector="body")
        blob = extract_json_blob(html, _JSON_MARKER_ID)

        cities: List[City] = []
        if blob:
            cities = self._parse_cities_from_json(blob)
        if not cities:
            cities = self._parse_cities_from_dom(html)

        if not cities:
            raise ProviderError(
                "district: could not discover any cities. Page structure "
                "likely changed — re-check _JSON_MARKER_ID / _parse_cities_from_dom."
            )
        logger.info("district: discovered %d cities", len(cities))
        return cities

    @staticmethod
    def _parse_cities_from_json(blob: Dict[str, Any]) -> List[City]:
        cities: List[City] = []
        try:
            candidates = blob.get("props", {}).get("pageProps", {}).get("cities", [])
            for c in candidates:
                cid = str(c.get("id") or c.get("cityId") or c.get("slug") or "")
                cname = c.get("name") or c.get("cityName")
                if cid and cname:
                    cities.append(City(id=cid, name=cname))
        except AttributeError:
            pass
        return cities

    @staticmethod
    def _parse_cities_from_dom(html: str) -> List[City]:
        soup = BeautifulSoup(html, "html.parser")
        cities: List[City] = []
        for link in soup.select('a[href*="/city/"], [data-city-id]'):
            cid = link.get("data-city-id") or ""
            if not cid:
                href = link.get("href", "")
                match = re.search(r"/city/([a-z0-9\-]+)", href)
                if match:
                    cid = match.group(1)
            name = link.get_text(strip=True)
            if cid and name:
                cities.append(City(id=cid, name=name))
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
        url = f"{BASE_URL}/movies?city={city.id}"
        html = await self.fetch_html(url, wait_selector="body")
        blob = extract_json_blob(html, _JSON_MARKER_ID)

        movies: List[Movie] = []
        if blob:
            movies = self._parse_movies_from_json(blob)
        if not movies:
            movies = self._parse_movies_from_dom(html)

        if not movies:
            raise ProviderError(
                f"district: no movies found for city={city.id}. "
                "Either nothing is currently showing, or selectors need updating."
            )
        logger.info("district: found %d movies for %s", len(movies), city.name)
        return movies

    @staticmethod
    def _parse_movies_from_json(blob: Dict[str, Any]) -> List[Movie]:
        movies: List[Movie] = []
        try:
            candidates = blob.get("props", {}).get("pageProps", {}).get("movies", [])
            for m in candidates:
                mid = str(m.get("id") or m.get("movieId") or m.get("slug") or "")
                title = m.get("title") or m.get("name")
                if mid and title:
                    movies.append(Movie(id=mid, title=title, extra={"slug": m.get("slug")}))
        except AttributeError:
            pass
        return movies

    @staticmethod
    def _parse_movies_from_dom(html: str) -> List[Movie]:
        soup = BeautifulSoup(html, "html.parser")
        movies: List[Movie] = []
        for link in soup.select('a[href*="/movies/"]'):
            href = link.get("href", "")
            match = re.search(r"/movies/([a-z0-9\-]+)", href)
            if not match:
                continue
            slug = match.group(1)
            title_el = link.select_one("img")
            title = (title_el.get("alt") if title_el else None) or slug.replace("-", " ").title()
            movies.append(Movie(id=slug, title=title, extra={"slug": slug}))
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
    def _showtimes_url(self, city: City, movie: Movie, date: Optional[str] = None) -> str:
        slug = movie.extra.get("slug") or movie.id
        url = f"{BASE_URL}/movies/{slug}/buytickets?city={city.id}"
        if date:
            url += f"&date={date}"
        return url

    @async_retry(attempts=3, exceptions=(ProviderError,))
    async def get_theatres(self, city: City, movie: Movie) -> List[Theatre]:
        url = self._showtimes_url(city, movie)
        html = await self.fetch_html(url, wait_selector="body")
        blob = extract_json_blob(html, _JSON_MARKER_ID)

        theatres: List[Theatre] = []
        if blob:
            theatres = self._parse_theatres_from_json(blob)
        if not theatres:
            theatres = self._parse_theatres_from_dom(html)

        if not theatres:
            logger.warning(
                "district: no theatres found for movie=%s city=%s "
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
                vid = str(v.get("id") or v.get("venueId") or "")
                vname = v.get("name") or v.get("venueName")
                if vid and vname:
                    theatres.append(Theatre(id=vid, name=vname))
        except AttributeError:
            pass
        return theatres

    @staticmethod
    def _parse_theatres_from_dom(html: str) -> List[Theatre]:
        soup = BeautifulSoup(html, "html.parser")
        theatres: List[Theatre] = []
        for block in soup.select("[data-venue-id]"):
            vid = block.get("data-venue-id", "")
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
        url = self._showtimes_url(city, movie)
        html = await self.fetch_html(url, wait_selector="body")
        blob = extract_json_blob(html, _JSON_MARKER_ID)

        dates: List[ShowDate] = []
        if blob:
            try:
                raw_dates = blob.get("props", {}).get("pageProps", {}).get("dates", [])
                for d in raw_dates:
                    date_code = d.get("date") or d.get("dateCode")
                    label = d.get("label") or d.get("display") or date_code
                    if date_code:
                        dates.append(ShowDate(date=date_code, label=label))
            except AttributeError:
                pass

        if not dates:
            soup = BeautifulSoup(html, "html.parser")
            for el in soup.select("[data-date]"):
                date_code = el.get("data-date", "")
                label = el.get_text(strip=True) or date_code
                if date_code:
                    dates.append(ShowDate(date=date_code, label=label))

        if not dates:
            raise ProviderError(
                f"district: no selectable dates found for movie={movie.title} "
                f"theatre={theatre.name}. Selectors likely need updating."
            )
        return dates

    # ------------------------------------------------------------------
    # Shows
    # ------------------------------------------------------------------
    @async_retry(attempts=3, exceptions=(ProviderError,))
    async def get_shows(self, city: City, movie: Movie, theatre: Theatre, date: str) -> List[Show]:
        url = self._showtimes_url(city, movie, date=date)
        html = await self.fetch_html(url, wait_selector="body")
        blob = extract_json_blob(html, _JSON_MARKER_ID)

        shows: List[Show] = []
        if blob:
            shows = self._parse_shows_from_json(blob, city, movie, theatre, date)
        if not shows:
            shows = self._parse_shows_from_dom(html, movie, theatre, date)

        logger.info(
            "district: %d shows for movie=%s theatre=%s date=%s",
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
                if str(v.get("id")) != theatre.id:
                    continue
                for showtime in v.get("showtimes", []):
                    time_str = showtime.get("time")
                    screen = showtime.get("screenName") or showtime.get("screen")
                    status = (showtime.get("status") or "").lower()
                    bookable = status not in ("soldout", "sold_out", "unavailable", "full")
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
                            booking_url=self._showtimes_url(city, movie, date=date),
                            bookable=bookable,
                        )
                    )
        except AttributeError:
            pass
        return shows

    def _parse_shows_from_dom(self, html: str, movie: Movie, theatre: Theatre, date: str) -> List[Show]:
        soup = BeautifulSoup(html, "html.parser")
        shows: List[Show] = []
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
