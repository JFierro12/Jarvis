"""The single source of truth for "where is the user".

Before this module there was no location handling anywhere in the project, so
there is no legacy path to reconcile — but there is a strong temptation to
build the wrong thing, because three signals are lying around that *look* like
location and are not:

* the backend machine's timezone,
* the machine's locale/region,
* the IP address the provider sees.

None of them is the user's location. A laptop on a VPN reports the exit node's
country; a machine that never left the factory timezone reports Redmond; a
traveller's timezone changes while their saved home does not. Every one of
those produces a *confident, specific, wrong* answer, which is worse than no
answer. So this module will return `None` and ask rather than guess, and
`resolve()` physically has no access to any of those three signals — the guard
is structural, not a comment asking future code to behave.

What it does use, in strict priority order:

1. a destination named in the request itself ("weather in Denver"),
2. a device-provided fix, but only when a permission flow granted one and it
   is still fresh,
3. the user's saved default location,
4. nothing — in which case the caller must ask.

The distinction between (1) and (3) matters beyond politeness: asking for
Denver's weather from Seattle must not overwrite the saved home, and tomorrow's
"what's the weather" must not silently answer for Denver.
"""

from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import List, Optional, Protocol


class LocationSource(str, Enum):
    """Where a location came from. Carried all the way to the user-visible
    answer, because "it's 54F in Seattle" means different things when Seattle
    is a GPS fix versus a default someone typed once in a config file."""

    EXPLICIT_REQUEST = "explicit_request"
    DEVICE_FIX = "device_fix"
    SAVED_DEFAULT = "saved_default"
    UNKNOWN = "unknown"


class LocationPrecision(str, Enum):
    COORDINATE = "coordinate"  # a real fix, metres-to-hundreds-of-metres
    POSTCODE = "postcode"
    CITY = "city"


class Units(str, Enum):
    US = "us"          # Fahrenheit, mph, inches
    METRIC = "metric"  # Celsius, km/h, mm


# A device fix older than this is stale: the user may have moved. Stale does
# not mean unusable -- it means it must be labelled, and must lose to a
# fresher signal. Chosen to be long enough that a phone that reported its
# position when the app opened is still trusted an hour later.
DEVICE_FIX_TTL = timedelta(minutes=60)


class LocationUnavailable(Exception):
    """Raised when no location can be established and the caller must ask.

    Deliberately an exception rather than a `None` return in the paths that
    cannot continue without one, so that forgetting to handle it is a loud
    failure instead of a fabricated default.
    """


@dataclass(frozen=True)
class ResolvedLocation:
    """A location plus everything needed to describe it honestly."""

    label: str                      # "Seattle, Washington, United States"
    latitude: float
    longitude: float
    timezone: str                   # IANA name, e.g. "America/Los_Angeles"
    source: LocationSource
    precision: LocationPrecision
    units: Units
    country_code: str = ""
    # When the underlying position was determined. For a saved default this is
    # when it was configured, not "now" -- a saved default does not become
    # fresh by being read again.
    as_of: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not (-90.0 <= self.latitude <= 90.0):
            raise ValueError(f"latitude out of range: {self.latitude}")
        if not (-180.0 <= self.longitude <= 180.0):
            raise ValueError(f"longitude out of range: {self.longitude}")
        if math.isnan(self.latitude) or math.isnan(self.longitude):
            raise ValueError("latitude/longitude must be numbers")

    @property
    def is_stale(self) -> bool:
        """True when this is a device fix old enough that the user may have
        moved. Only device fixes can go stale; a saved default is not stale,
        it is simply a default."""
        if self.source is not LocationSource.DEVICE_FIX or self.as_of is None:
            return False
        return (_utcnow() - self.as_of) > DEVICE_FIX_TTL

    def provenance_note(self) -> str:
        """One human-readable clause explaining where this location came from.

        Included in spoken and written answers so the user can catch a wrong
        default immediately rather than after acting on a forecast for a city
        they left.
        """
        if self.source is LocationSource.EXPLICIT_REQUEST:
            return f"for {self.label}, as you asked"
        if self.source is LocationSource.DEVICE_FIX:
            if self.is_stale:
                mins = int((_utcnow() - self.as_of).total_seconds() // 60)
                return f"for {self.label}, from a device location fix {mins} minutes old"
            return f"for {self.label}, from your device's current location"
        if self.source is LocationSource.SAVED_DEFAULT:
            return f"for {self.label}, your saved default location"
        return f"for {self.label}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Geocoding: name -> coordinates. Injected, never called directly by callers.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GeocodeResult:
    name: str
    latitude: float
    longitude: float
    timezone: str
    country: str = ""
    country_code: str = ""
    admin1: str = ""
    population: int = 0

    @property
    def label(self) -> str:
        parts = [p for p in (self.name, self.admin1, self.country) if p]
        # "Seattle, Washington, United States" -- the admin1 is what separates
        # Seattle WA from the Seattle in Jalisco, Mexico, which a live query
        # really does return as the second result.
        return ", ".join(parts)


class Geocoder(Protocol):
    def search(self, query: str, limit: int = 5) -> List[GeocodeResult]: ...


class GeocodingUnavailable(Exception):
    pass


class MockGeocoder:
    """Deterministic geocoder for tests and offline/demo mode.

    Includes the genuinely ambiguous Seattle pair on purpose, so the
    disambiguation path is exercised by the default test fixture rather than
    only by a hand-written edge case.
    """

    _TABLE = {
        "seattle": [
            GeocodeResult("Seattle", 47.60621, -122.33207, "America/Los_Angeles",
                          "United States", "US", "Washington", 780995),
            GeocodeResult("Seattle", 20.71951, -103.37311, "America/Mexico_City",
                          "Mexico", "MX", "Jalisco", 0),
        ],
        "denver": [
            GeocodeResult("Denver", 39.73915, -104.9847, "America/Denver",
                          "United States", "US", "Colorado", 715522),
        ],
        "london": [
            GeocodeResult("London", 51.50853, -0.12574, "Europe/London",
                          "United Kingdom", "GB", "England", 8961989),
            GeocodeResult("London", 42.98339, -81.23304, "America/Toronto",
                          "Canada", "CA", "Ontario", 346765),
        ],
        "98101": [
            GeocodeResult("Seattle", 47.61067, -122.33438, "America/Los_Angeles",
                          "United States", "US", "Washington", 780995),
        ],
    }

    def search(self, query: str, limit: int = 5) -> List[GeocodeResult]:
        return list(self._TABLE.get(query.strip().lower(), []))[:limit]


class OpenMeteoGeocoder:
    """Open-Meteo's geocoding endpoint.

    Verified against the live service: `?name=Seattle` returns the Washington
    city first and a second Seattle in Jalisco, Mexico. Field names below were
    read off a real response, not inferred from documentation.

    Location data is from GeoNames; the service requires no API key for
    non-commercial use. It does receive the place name being looked up, which
    is a real disclosure and is documented in docs/OVERNIGHT_LOG.md.
    """

    ENDPOINT = "https://geocoding-api.open-meteo.com/v1/search"

    def __init__(self, http_client=None, timeout: float = 10.0) -> None:
        self._client = http_client
        self._timeout = timeout

    def _get(self, params: dict) -> dict:
        if self._client is not None:
            return self._client.get_json(self.ENDPOINT, params, self._timeout)
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise GeocodingUnavailable("httpx is not installed") from exc
        try:
            response = httpx.get(self.ENDPOINT, params=params, timeout=self._timeout)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            raise GeocodingUnavailable(f"geocoding request failed: {exc}") from exc

    def search(self, query: str, limit: int = 5) -> List[GeocodeResult]:
        query = query.strip()
        # The service documents that 0- and 1-character searches match nothing;
        # spending a network round trip to be told so is pointless.
        if len(query) < 2:
            return []
        payload = self._get({"name": query, "count": max(1, min(limit, 20)),
                             "language": "en", "format": "json"})
        # Documented error shape, and the no-match shape is NOT documented --
        # a missing `results` key and an empty list both have to work.
        if isinstance(payload, dict) and payload.get("error"):
            raise GeocodingUnavailable(str(payload.get("reason", "geocoding error")))
        results = []
        for row in (payload or {}).get("results") or []:
            try:
                results.append(
                    GeocodeResult(
                        name=str(row["name"]),
                        latitude=float(row["latitude"]),
                        longitude=float(row["longitude"]),
                        timezone=str(row.get("timezone") or "UTC"),
                        country=str(row.get("country") or ""),
                        country_code=str(row.get("country_code") or ""),
                        admin1=str(row.get("admin1") or ""),
                        population=int(row.get("population") or 0),
                    )
                )
            except (KeyError, TypeError, ValueError):
                # One malformed row must not lose the other candidates.
                continue
        return results


# --------------------------------------------------------------------------
# Stored preferences
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SavedLocation:
    """The user's configured default. Set explicitly, never inferred."""

    label: str
    latitude: float
    longitude: float
    timezone: str
    units: Units = Units.US
    country_code: str = ""
    precision: LocationPrecision = LocationPrecision.CITY
    configured_at: Optional[datetime] = None


@dataclass(frozen=True)
class DeviceFix:
    """A position reported by a device that holds a location permission.

    `permission_granted` is required and is not a formality: a fix that
    arrives without one is discarded rather than used, so a client cannot
    acquire location simply by posting coordinates.
    """

    latitude: float
    longitude: float
    timezone: str
    label: str = ""
    accuracy_metres: Optional[float] = None
    reported_at: Optional[datetime] = None
    permission_granted: bool = False


class LocationPreferenceStore:
    """In-process holder for the saved default and the latest device fix.

    Deliberately not a database table. The saved default belongs to the
    installation's configuration (it is set from settings, below) and the
    device fix is ephemeral by nature -- persisting a location history is
    exactly what the project's privacy rules say not to do by default.
    """

    def __init__(self, saved: Optional[SavedLocation] = None) -> None:
        self._saved = saved
        self._fix: Optional[DeviceFix] = None

    # -- saved default --
    def saved(self) -> Optional[SavedLocation]:
        return self._saved

    def set_saved(self, saved: Optional[SavedLocation]) -> None:
        self._saved = saved

    # -- device fix --
    def device_fix(self) -> Optional[DeviceFix]:
        return self._fix

    def set_device_fix(self, fix: Optional[DeviceFix]) -> None:
        if fix is not None and not fix.permission_granted:
            # Refused rather than ignored: silently dropping it would let a
            # caller believe location sharing was working.
            raise PermissionError("device location requires a granted permission")
        self._fix = fix

    def clear_device_fix(self) -> None:
        self._fix = None


def saved_location_from_settings(settings) -> Optional[SavedLocation]:
    """Build the saved default from configuration, or return None.

    Returns None rather than a guess when the configuration is absent or
    half-filled. A latitude without a longitude is a misconfiguration, not an
    invitation to improvise.
    """
    label = (getattr(settings, "home_location_label", "") or "").strip()
    lat = getattr(settings, "home_latitude", None)
    lon = getattr(settings, "home_longitude", None)
    tz = (getattr(settings, "home_timezone", "") or "").strip()
    units_raw = (getattr(settings, "preferred_units", "") or "us").strip().lower()
    units = Units.METRIC if units_raw in ("metric", "si", "celsius") else Units.US

    if lat is None or lon is None or lat == "" or lon == "":
        return None
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat_f <= 90.0) or not (-180.0 <= lon_f <= 180.0):
        return None

    return SavedLocation(
        label=label or f"{lat_f:.4f}, {lon_f:.4f}",
        latitude=lat_f,
        longitude=lon_f,
        timezone=tz or "UTC",
        units=units,
        country_code=(getattr(settings, "home_country_code", "") or "").strip().upper(),
    )


# --------------------------------------------------------------------------
# Request parsing: did the user name a place?
# --------------------------------------------------------------------------

# Matches "in Denver", "for Denver", "in Denver Colorado". Intentionally
# conservative: a miss falls back to the saved default, which is recoverable,
# whereas a false positive sends a forecast for a city the user never named.
_IN_PLACE = re.compile(
    r"\b(?:in|for|at|near|around)\s+(?P<place>[A-Za-z][A-Za-z .'\-]{1,48}?)"
    r"(?=\s*(?:\?|$|,|\.|!|\bright now\b|\btoday\b|\btonight\b|\btomorrow\b|"
    r"\bthis\b|\bnext\b|\bweather\b|\bforecast\b|\bnews\b))",
    re.IGNORECASE,
)

_POSTCODE = re.compile(r"\b(?P<zip>\d{5})(?:-\d{4})?\b")

# Words that follow "in"/"for" but are not places. Without this, "what's the
# weather in the morning" geocodes "the morning".
_NOT_A_PLACE = {
    "the", "a", "an", "my", "your", "our", "this", "that", "these", "those",
    "morning", "afternoon", "evening", "night", "tonight", "today", "tomorrow",
    "celsius", "fahrenheit", "metric", "imperial", "here", "there", "it",
    "general", "detail", "short", "brief", "summary", "fact", "degrees",
    "advance", "case", "fall", "spring", "summer", "winter", "town", "area",
}


def extract_requested_place(text: str) -> Optional[str]:
    """Pull an explicitly named place out of a request, or return None.

    Returns None far more readily than it guesses. "What's the weather" has no
    place in it and must resolve to the saved default; "what's the weather in
    the morning" likewise, which is why the stopword set exists.
    """
    if not text:
        return None

    postcode = _POSTCODE.search(text)
    if postcode:
        return postcode.group("zip")

    for match in _IN_PLACE.finditer(text):
        place = " ".join(match.group("place").split()).strip(" .,'-")
        if not place:
            continue
        head = place.split()[0].lower()
        if head in _NOT_A_PLACE:
            continue
        if all(word.lower() in _NOT_A_PLACE for word in place.split()):
            continue
        return place
    return None


# --------------------------------------------------------------------------
# The resolver
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Ambiguous:
    """Several real places match the name. Not an error and not a guess --
    the caller is expected to ask which one."""

    query: str
    candidates: List[GeocodeResult]


class LocationResolver:
    """Turns a request into a `ResolvedLocation`, or declines to.

    Note what is *not* injected here: no IP geolocation client, no system
    timezone reader, no locale. The resolver cannot consult them because it
    was never given them, which is a stronger guarantee than a rule saying it
    shouldn't.
    """

    def __init__(self, store: LocationPreferenceStore, geocoder: Geocoder) -> None:
        self._store = store
        self._geocoder = geocoder

    # -- the main entry point --
    def resolve(self, request_text: str = "", *, allow_ambiguous: bool = False):
        """Resolve in priority order. Returns `ResolvedLocation`, or
        `Ambiguous` when a named place matches several real places and
        `allow_ambiguous` is set.

        Raises `LocationUnavailable` when nothing is configured, which is the
        honest outcome for a fresh install and must surface as a question to
        the user rather than as a default city.
        """
        place = extract_requested_place(request_text)
        if place:
            resolved = self._resolve_named(place, allow_ambiguous=allow_ambiguous)
            if resolved is not None:
                return resolved
            # A named place that geocodes to nothing is a dead end. Falling
            # back to the saved default here would answer for the wrong city
            # while appearing to honour the request, so it is refused instead.
            raise LocationUnavailable(
                f"I couldn't find a place called {place!r}. Could you give me a city or postcode?"
            )

        fix = self._store.device_fix()
        if fix is not None and fix.permission_granted:
            candidate = ResolvedLocation(
                label=fix.label or f"{fix.latitude:.4f}, {fix.longitude:.4f}",
                latitude=fix.latitude,
                longitude=fix.longitude,
                timezone=fix.timezone or "UTC",
                source=LocationSource.DEVICE_FIX,
                precision=LocationPrecision.COORDINATE,
                units=self._units(),
                as_of=fix.reported_at or _utcnow(),
            )
            # A stale fix still beats a saved default only if there is no
            # saved default; otherwise the explicitly configured home wins.
            if not candidate.is_stale or self._store.saved() is None:
                return candidate

        saved = self._store.saved()
        if saved is not None:
            return ResolvedLocation(
                label=saved.label,
                latitude=saved.latitude,
                longitude=saved.longitude,
                timezone=saved.timezone,
                source=LocationSource.SAVED_DEFAULT,
                precision=saved.precision,
                units=saved.units,
                country_code=saved.country_code,
                as_of=saved.configured_at,
            )

        raise LocationUnavailable(
            "I don't have a location set yet. Which city should I use?"
        )

    def _units(self) -> Units:
        saved = self._store.saved()
        return saved.units if saved is not None else Units.US

    def _resolve_named(self, place: str, *, allow_ambiguous: bool):
        try:
            candidates = self._geocoder.search(place, limit=5)
        except GeocodingUnavailable:
            # Deliberately not swallowed into a default: if the lookup service
            # is down, the truthful answer is that it is down.
            raise
        if not candidates:
            return None

        distinct = _distinct_places(candidates)
        if len(distinct) > 1 and allow_ambiguous:
            return Ambiguous(query=place, candidates=distinct)

        best = distinct[0]
        return ResolvedLocation(
            label=best.label,
            latitude=best.latitude,
            longitude=best.longitude,
            timezone=best.timezone,
            source=LocationSource.EXPLICIT_REQUEST,
            precision=(LocationPrecision.POSTCODE if place.isdigit()
                       else LocationPrecision.CITY),
            units=self._units(),
            country_code=best.country_code,
            as_of=_utcnow(),
        )

    # -- explicit destination, kept separate from the saved default --
    def for_destination(self, place: str) -> ResolvedLocation:
        """Resolve a named destination without touching the saved default.

        Exists so that "what's the weather in Denver" cannot become "my
        location is Denver". Nothing in this method writes to the store.
        """
        resolved = self._resolve_named(place, allow_ambiguous=False)
        if resolved is None:
            raise LocationUnavailable(f"I couldn't find a place called {place!r}.")
        return resolved

    def set_saved_from_place(self, place: str) -> SavedLocation:
        """Explicitly change the saved default. Only ever called from a user
        action, never as a side effect of answering a question."""
        resolved = self._resolve_named(place, allow_ambiguous=False)
        if resolved is None:
            raise LocationUnavailable(f"I couldn't find a place called {place!r}.")
        saved = SavedLocation(
            label=resolved.label,
            latitude=resolved.latitude,
            longitude=resolved.longitude,
            timezone=resolved.timezone,
            units=self._units(),
            country_code=resolved.country_code,
            precision=resolved.precision,
            configured_at=_utcnow(),
        )
        self._store.set_saved(saved)
        return saved


def _distinct_places(candidates: List[GeocodeResult]) -> List[GeocodeResult]:
    """Collapse duplicates, then rank by population.

    Two results count as the same place when they share a name, country and
    admin1 -- the live service returns near-duplicate rows for large cities.
    Ranking by population is a heuristic, not a truth: it puts Seattle,
    Washington ahead of Seattle, Jalisco, which is usually right and is why
    the ambiguity is still reported upward rather than hidden.
    """
    seen = {}
    for c in candidates:
        key = (c.name.lower(), c.country_code.lower(), c.admin1.lower())
        if key not in seen or c.population > seen[key].population:
            seen[key] = c
    return sorted(seen.values(), key=lambda c: -c.population)


def with_units(location: ResolvedLocation, units: Units) -> ResolvedLocation:
    return replace(location, units=units)


# --------------------------------------------------------------------------
# Process-wide instances
# --------------------------------------------------------------------------
#
# The store is a singleton because a device fix has to outlive the request
# that reported it -- the phone posts its position once and later turns
# consult it. The saved default is rebuilt from settings on first use so that
# configuration remains the source of truth for it.

_store: Optional[LocationPreferenceStore] = None
_store_lock = threading.Lock()


def get_location_store(settings) -> LocationPreferenceStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = LocationPreferenceStore(saved_location_from_settings(settings))
        return _store


def get_location_resolver(settings) -> LocationResolver:
    """Build a resolver over the shared store.

    The geocoder is chosen by the same setting that chooses the weather
    provider: a deployment running entirely on mocks should not quietly make
    live geocoding requests.
    """
    provider = (getattr(settings, "weather_provider", "mock") or "mock").strip().lower()
    geocoder: Geocoder
    if provider == "mock":
        geocoder = MockGeocoder()
    else:
        geocoder = OpenMeteoGeocoder(
            timeout=float(getattr(settings, "weather_timeout_seconds", 10.0))
        )
    return LocationResolver(get_location_store(settings), geocoder)


def reset_location_store_for_tests() -> None:
    global _store
    with _store_lock:
        _store = None
