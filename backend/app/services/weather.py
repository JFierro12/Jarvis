"""Weather, behind an injected provider protocol.

Three provider implementations, with very different evidentiary status, and
the difference is recorded here rather than flattened into "we support
weather":

* `MockWeatherProvider` -- deterministic, no network. The default.
* `NwsWeatherProvider` -- the US National Weather Service. **Verified
  end-to-end against the live service from this machine**: `/points`, the grid
  forecast, the 301 to the canonical stations path, and a real observation.
* `OpenMeteoWeatherProvider` -- written against documentation only. The
  forecast host could not be reached from this machine (TLS handshake failure
  under two independent TLS stacks, 3/3 attempts, while the same domain's
  geocoding host worked fine). It is **not** verified and says so when it
  fails.

The single nastiest detail, learned from a real response rather than the docs:
**NWS observations are always metric and self-describing**, with
`temperature.unitCode == "wmoUnit:degC"`, wind in `km_h-1` and pressure in
`Pa` -- *regardless* of the `units=us` query parameter, which only affects the
forecast endpoint. A client that assumes `units=us` applies to observations
reports 12°C as 12°F and sounds completely confident doing it. Every number
that comes back from NWS is therefore converted from its declared `unitCode`
and never from an assumption; `_convert` raises on an unrecognised unit rather
than passing the number through.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Protocol, Tuple

from app.services.location import ResolvedLocation, Units


class WeatherUnavailable(Exception):
    """The forecast could not be retrieved. Never substituted with a guess."""


@dataclass(frozen=True)
class CurrentConditions:
    temperature: Optional[float]
    condition: str
    units: Units
    observed_at: Optional[datetime] = None
    feels_like: Optional[float] = None
    humidity_percent: Optional[float] = None
    wind_speed: Optional[float] = None
    station: str = ""
    # True when served from cache rather than fetched now. Surfaced to the
    # user, because "it's 54 degrees" from a 40-minute-old cache is a
    # different statement from the same words about a live reading.
    from_cache: bool = False
    cache_age_seconds: float = 0.0

    @property
    def temperature_unit(self) -> str:
        return "F" if self.units is Units.US else "C"

    @property
    def wind_unit(self) -> str:
        return "mph" if self.units is Units.US else "km/h"


@dataclass(frozen=True)
class ForecastPeriod:
    name: str                      # "Tonight", "Friday"
    starts_at: Optional[datetime]
    temperature: Optional[float]
    is_daytime: bool
    condition: str
    units: Units
    precipitation_probability: Optional[float] = None
    wind: str = ""


@dataclass(frozen=True)
class WeatherReport:
    location: ResolvedLocation
    current: Optional[CurrentConditions]
    periods: List[ForecastPeriod] = field(default_factory=list)
    provider: str = ""
    retrieved_at: Optional[datetime] = None
    from_cache: bool = False
    cache_age_seconds: float = 0.0
    # Populated when part of the report could not be fetched. The report is
    # still returned -- a forecast without current conditions is useful -- but
    # the gap is never silently presented as complete.
    partial_reason: str = ""


class WeatherProvider(Protocol):
    def report(self, location: ResolvedLocation, days: int = 3) -> WeatherReport: ...


# ---------------------------------------------------------------------------
# Unit handling
# ---------------------------------------------------------------------------

_C_UNITS = {"wmounit:degc", "degc", "c", "celsius"}
_F_UNITS = {"wmounit:degf", "degf", "f", "fahrenheit"}
_KMH_UNITS = {"wmounit:km_h-1", "km_h-1", "kmh", "km/h"}
_MS_UNITS = {"wmounit:m_s-1", "m_s-1", "m/s"}
_MPH_UNITS = {"wmounit:mi_h-1", "mi_h-1", "mph"}
_PCT_UNITS = {"wmounit:percent", "percent", "%"}


class UnknownUnit(Exception):
    """An unrecognised unitCode. Raised rather than guessed, because the whole
    point of reading unitCode is to not assume."""


def _convert(value: Optional[float], unit_code: str, want: Units, kind: str) -> Optional[float]:
    """Convert a self-describing measurement into the requested unit system."""
    if value is None:
        return None
    code = (unit_code or "").strip().lower()

    if kind == "temperature":
        if code in _C_UNITS:
            return value * 9.0 / 5.0 + 32.0 if want is Units.US else value
        if code in _F_UNITS:
            return value if want is Units.US else (value - 32.0) * 5.0 / 9.0
        raise UnknownUnit(f"unrecognised temperature unit {unit_code!r}")

    if kind == "speed":
        if code in _KMH_UNITS:
            return value * 0.621371 if want is Units.US else value
        if code in _MS_UNITS:
            return value * 2.236936 if want is Units.US else value * 3.6
        if code in _MPH_UNITS:
            return value if want is Units.US else value / 0.621371
        raise UnknownUnit(f"unrecognised speed unit {unit_code!r}")

    if kind == "percent":
        if code in _PCT_UNITS or code == "":
            return value
        raise UnknownUnit(f"unrecognised percentage unit {unit_code!r}")

    raise UnknownUnit(f"unrecognised measurement kind {kind!r}")


# WMO code -> description, for providers that report a numeric code instead of
# text. Taken from the published WMO interpretation table.
WMO_CODES: Dict[int, str] = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snowfall", 73: "Moderate snowfall", 75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail",
    97: "Heavy thunderstorm", 99: "Thunderstorm with heavy hail",
}


def describe_wmo(code: Optional[int]) -> str:
    if code is None:
        return "Unknown conditions"
    return WMO_CODES.get(int(code), f"Unknown conditions (WMO code {int(code)})")


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class WeatherCache:
    """Small TTL cache keyed on rounded coordinates and unit system.

    Coordinates are rounded to ~1km before being used as a key so that two
    slightly different fixes for the same place share an entry. Entries past
    their TTL are returned only when explicitly asked for as a fallback, and
    always carry their age so the caller can label them.
    """

    def __init__(self, ttl_seconds: float = 600.0) -> None:
        self._ttl = ttl_seconds
        self._entries: Dict[Tuple, Tuple[float, WeatherReport]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(location: ResolvedLocation, days: int) -> Tuple:
        return (round(location.latitude, 2), round(location.longitude, 2),
                location.units.value, days)

    def get(self, key: Tuple, *, allow_stale: bool = False):
        with self._lock:
            entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, report = entry
        age = time.monotonic() - stored_at
        if age > self._ttl and not allow_stale:
            return None
        from dataclasses import replace
        return replace(report, from_cache=True, cache_age_seconds=age)

    def put(self, key: Tuple, report: WeatherReport) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic(), report)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


class MockWeatherProvider:
    """Deterministic weather for tests, offline dev and demo mode.

    Varies with latitude so that two different locations give two different
    answers -- a fixed response would let a location bug pass unnoticed.
    """

    name = "mock"

    def report(self, location: ResolvedLocation, days: int = 3) -> WeatherReport:
        base_c = 20.0 - abs(location.latitude) * 0.25
        temp = base_c * 9.0 / 5.0 + 32.0 if location.units is Units.US else base_c
        now = datetime.now(timezone.utc)
        periods = []
        for i in range(max(1, min(days, 7))):
            periods.append(
                ForecastPeriod(
                    name=("Today" if i == 0 else f"Day {i + 1}"),
                    starts_at=now,
                    temperature=round(temp + i, 1),
                    is_daytime=True,
                    condition="Partly cloudy",
                    units=location.units,
                    precipitation_probability=10.0 * i,
                )
            )
        return WeatherReport(
            location=location,
            current=CurrentConditions(
                temperature=round(temp, 1),
                condition="Partly cloudy",
                units=location.units,
                observed_at=now,
                humidity_percent=55.0,
                wind_speed=5.0,
                station="MOCK",
            ),
            periods=periods,
            provider=self.name,
            retrieved_at=now,
        )


class _HttpMixin:
    def _get_json(self, url: str, params: Optional[dict] = None,
                  headers: Optional[dict] = None) -> dict:
        if self._client is not None:
            return self._client.get_json(url, params or {}, self._timeout, headers or {})
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise WeatherUnavailable("httpx is not installed") from exc
        try:
            response = httpx.get(url, params=params, headers=headers,
                                 timeout=self._timeout, follow_redirects=True)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            raise WeatherUnavailable(f"weather request failed: {exc}") from exc


class NwsWeatherProvider(_HttpMixin):
    """US National Weather Service. No API key; US coverage only.

    Verified end-to-end against the live service. `follow_redirects` is not
    optional: `/points/{lat},{lon}/stations` answers 301 to the canonical
    gridpoint path, and without it the station lookup silently yields a
    problem document instead of a station list.
    """

    name = "nws"
    BASE = "https://api.weather.gov"

    def __init__(self, user_agent: str, http_client=None, timeout: float = 12.0) -> None:
        # Generic by construction. A personal email address here would be
        # transmitted to a third party on every single weather request.
        self._headers = {"User-Agent": user_agent, "Accept": "application/geo+json"}
        self._client = http_client
        self._timeout = timeout

    def report(self, location: ResolvedLocation, days: int = 3) -> WeatherReport:
        if location.units is Units.US:
            want = Units.US
        else:
            want = Units.METRIC

        points = self._get_json(
            f"{self.BASE}/points/{location.latitude:.4f},{location.longitude:.4f}",
            headers=self._headers,
        )
        props = (points or {}).get("properties") or {}
        forecast_url = props.get("forecast")
        if not forecast_url:
            raise WeatherUnavailable(
                "The National Weather Service has no forecast grid for that location "
                "(it covers the United States only)."
            )

        periods = self._forecast_periods(forecast_url, want, days)

        # Current conditions are a best effort on top of the forecast: a
        # missing observation degrades the report, it does not fail it.
        current, partial = None, ""
        try:
            current = self._latest_observation(props, want)
        except (WeatherUnavailable, UnknownUnit) as exc:
            partial = f"current conditions unavailable ({exc})"

        return WeatherReport(
            location=location,
            current=current,
            periods=periods,
            provider=self.name,
            retrieved_at=datetime.now(timezone.utc),
            partial_reason=partial,
        )

    def _forecast_periods(self, url: str, want: Units, days: int) -> List[ForecastPeriod]:
        payload = self._get_json(url, params={"units": "us" if want is Units.US else "si"},
                                 headers=self._headers)
        raw = ((payload or {}).get("properties") or {}).get("periods") or []
        out: List[ForecastPeriod] = []
        for p in raw[: max(1, days * 2)]:
            unit_letter = str(p.get("temperatureUnit") or "").strip()
            try:
                temp = _convert(_as_float(p.get("temperature")), unit_letter, want, "temperature")
            except UnknownUnit:
                temp = None
            prob = ((p.get("probabilityOfPrecipitation") or {}).get("value"))
            out.append(
                ForecastPeriod(
                    name=str(p.get("name") or ""),
                    starts_at=_parse_iso(p.get("startTime")),
                    temperature=_round1(temp),
                    is_daytime=bool(p.get("isDaytime")),
                    condition=str(p.get("shortForecast") or "").strip(),
                    units=want,
                    precipitation_probability=_as_float(prob),
                    wind=" ".join(x for x in (str(p.get("windSpeed") or ""),
                                              str(p.get("windDirection") or "")) if x).strip(),
                )
            )
        return out

    def _latest_observation(self, points_props: dict, want: Units) -> CurrentConditions:
        grid_id = points_props.get("gridId")
        gx, gy = points_props.get("gridX"), points_props.get("gridY")
        if not (grid_id and gx is not None and gy is not None):
            raise WeatherUnavailable("no observation grid for that location")

        stations = self._get_json(
            f"{self.BASE}/gridpoints/{grid_id}/{gx},{gy}/stations", headers=self._headers
        )
        features = (stations or {}).get("features") or []
        if not features:
            raise WeatherUnavailable("no nearby observation station")
        station_id = ((features[0].get("properties") or {}).get("stationIdentifier") or "")
        if not station_id:
            raise WeatherUnavailable("nearby station has no identifier")

        obs = self._get_json(
            f"{self.BASE}/stations/{station_id}/observations/latest", headers=self._headers
        )
        p = (obs or {}).get("properties") or {}

        # Every value is converted from its own declared unitCode. This is the
        # trap: `units=us` above does NOT make these Fahrenheit.
        temp = _measure(p.get("temperature"), want, "temperature")
        return CurrentConditions(
            temperature=_round1(temp),
            condition=str(p.get("textDescription") or "").strip() or "Unknown conditions",
            units=want,
            observed_at=_parse_iso(p.get("timestamp")),
            humidity_percent=_round1(_measure(p.get("relativeHumidity"), want, "percent")),
            wind_speed=_round1(_measure(p.get("windSpeed"), want, "speed")),
            station=station_id,
        )


class OpenMeteoWeatherProvider(_HttpMixin):
    """Open-Meteo forecast API.

    **Unverified.** Written from documentation; the forecast host was
    unreachable from the machine this was developed on, so no live response
    ever exercised this parser. Two specific risks are therefore live: the
    documentation does not show a `current`-only example (the variable names
    are inferred from the hourly table), and it documents no `current_units`
    key at all. This code consequently never depends on `current_units`
    existing and treats a missing `current` block as a partial report rather
    than an error.

    Free tier is non-commercial use only; no API key. Data is CC-BY 4.0.
    """

    name = "open_meteo"
    ENDPOINT = "https://api.open-meteo.com/v1/forecast"

    CURRENT_VARS = ("temperature_2m,apparent_temperature,relative_humidity_2m,"
                    "precipitation,weather_code,wind_speed_10m,is_day")
    DAILY_VARS = ("weather_code,temperature_2m_max,temperature_2m_min,"
                  "precipitation_sum,precipitation_probability_max")

    def __init__(self, http_client=None, timeout: float = 12.0) -> None:
        self._client = http_client
        self._timeout = timeout

    def report(self, location: ResolvedLocation, days: int = 3) -> WeatherReport:
        us = location.units is Units.US
        payload = self._get_json(self.ENDPOINT, params={
            "latitude": f"{location.latitude:.4f}",
            "longitude": f"{location.longitude:.4f}",
            "current": self.CURRENT_VARS,
            "daily": self.DAILY_VARS,
            # Documented as required whenever `daily` is requested.
            "timezone": location.timezone or "auto",
            "forecast_days": max(1, min(days, 16)),
            "temperature_unit": "fahrenheit" if us else "celsius",
            "wind_speed_unit": "mph" if us else "kmh",
            "precipitation_unit": "inch" if us else "mm",
        })
        if isinstance(payload, dict) and payload.get("error"):
            raise WeatherUnavailable(str(payload.get("reason", "forecast error")))

        cur = (payload or {}).get("current") or {}
        current = None
        partial = ""
        if cur:
            current = CurrentConditions(
                temperature=_round1(_as_float(cur.get("temperature_2m"))),
                condition=describe_wmo(_as_int(cur.get("weather_code"))),
                units=location.units,
                observed_at=_parse_iso(cur.get("time")),
                feels_like=_round1(_as_float(cur.get("apparent_temperature"))),
                humidity_percent=_round1(_as_float(cur.get("relative_humidity_2m"))),
                wind_speed=_round1(_as_float(cur.get("wind_speed_10m"))),
            )
        else:
            partial = "current conditions were not present in the response"

        daily = (payload or {}).get("daily") or {}
        times = daily.get("time") or []
        periods: List[ForecastPeriod] = []
        for i, day in enumerate(times[:days]):
            periods.append(
                ForecastPeriod(
                    name=str(day),
                    starts_at=_parse_iso(day),
                    temperature=_round1(_index(daily.get("temperature_2m_max"), i)),
                    is_daytime=True,
                    condition=describe_wmo(_as_int(_index(daily.get("weather_code"), i))),
                    units=location.units,
                    precipitation_probability=_as_float(
                        _index(daily.get("precipitation_probability_max"), i)
                    ),
                )
            )

        return WeatherReport(
            location=location, current=current, periods=periods, provider=self.name,
            retrieved_at=datetime.now(timezone.utc), partial_reason=partial,
        )


# ---------------------------------------------------------------------------
# Service wrapper: caching + honest degradation
# ---------------------------------------------------------------------------


class WeatherService:
    """Caches, and falls back to stale data only with a label.

    When the provider fails and a stale entry exists, the stale entry is
    returned *marked as stale* rather than an error -- but it is never
    presented as current, and when nothing is cached the failure propagates
    instead of becoming an invented forecast.
    """

    def __init__(self, provider: WeatherProvider, cache: Optional[WeatherCache] = None) -> None:
        self._provider = provider
        self._cache = cache if cache is not None else WeatherCache()

    def report(self, location: ResolvedLocation, days: int = 3) -> WeatherReport:
        key = WeatherCache.key(location, days)
        fresh = self._cache.get(key)
        if fresh is not None:
            return fresh
        try:
            report = self._provider.report(location, days)
        except (WeatherUnavailable, UnknownUnit) as exc:
            stale = self._cache.get(key, allow_stale=True)
            if stale is not None:
                from dataclasses import replace
                return replace(
                    stale,
                    partial_reason=f"live update failed ({exc}); showing cached data",
                )
            raise
        self._cache.put(key, report)
        return report


_service: Optional["WeatherService"] = None
_service_lock = threading.Lock()


def get_weather_service(settings) -> "WeatherService":
    """Process-wide service, so the cache survives between requests.

    A per-request cache would be no cache at all, and the rate limits of a
    free weather API are a real constraint rather than a formality.
    """
    global _service
    with _service_lock:
        if _service is None:
            _service = WeatherService(
                get_weather_provider(settings),
                WeatherCache(float(getattr(settings, "weather_cache_seconds", 600.0))),
            )
        return _service


def reset_weather_service_for_tests() -> None:
    global _service
    with _service_lock:
        _service = None


def get_weather_provider(settings) -> WeatherProvider:
    name = (getattr(settings, "weather_provider", "mock") or "mock").strip().lower()
    if name == "nws":
        return NwsWeatherProvider(
            user_agent=getattr(settings, "weather_user_agent", "JarvisAssistant/0.1"),
            timeout=float(getattr(settings, "weather_timeout_seconds", 12.0)),
        )
    if name in ("open_meteo", "openmeteo", "open-meteo"):
        return OpenMeteoWeatherProvider(
            timeout=float(getattr(settings, "weather_timeout_seconds", 12.0))
        )
    return MockWeatherProvider()


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def summarize(report: WeatherReport, *, max_periods: int = 2) -> str:
    """A short spoken-style summary that always says where and how fresh.

    The location clause is not decoration. "It's 54 and raining" is unfalsifiable
    by the listener; "It's 54 and raining in Seattle, your saved default
    location" lets them notice immediately that JARVIS is answering for the
    wrong city.
    """
    bits: List[str] = []
    loc = report.location.provenance_note()

    if report.current is not None:
        c = report.current
        head = f"Right now {loc}: "
        if c.temperature is not None:
            head += f"{c.temperature:.0f}°{c.temperature_unit}"
            if c.condition:
                head += f", {c.condition.lower()}"
        elif c.condition:
            head += c.condition
        else:
            head += "conditions unknown"
        if c.wind_speed is not None and c.wind_speed > 0:
            head += f", wind {c.wind_speed:.0f} {c.wind_unit}"
        bits.append(head + ".")
    else:
        bits.append(f"I don't have current conditions {loc}.")

    for p in report.periods[:max_periods]:
        line = f"{p.name}: {p.condition.lower()}" if p.condition else f"{p.name}:"
        if p.temperature is not None:
            line += f", around {p.temperature:.0f}°{'F' if p.units is Units.US else 'C'}"
        if p.precipitation_probability:
            line += f", {p.precipitation_probability:.0f}% chance of precipitation"
        bits.append(line + ".")

    if report.from_cache and report.cache_age_seconds > 60:
        bits.append(f"(Cached {int(report.cache_age_seconds // 60)} minutes ago.)")
    if report.partial_reason:
        bits.append(f"({report.partial_reason}.)")
    if report.location.is_stale:
        bits.append("(Your device location fix is over an hour old.)")

    return " ".join(bits)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _as_float(value) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value) -> Optional[int]:
    f = _as_float(value)
    return None if f is None else int(f)


def _index(seq, i):
    if not seq or i >= len(seq):
        return None
    return seq[i]


def _round1(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 1)


def _measure(block, want: Units, kind: str) -> Optional[float]:
    """Convert one NWS measurement object `{value, unitCode, qualityControl}`.

    Readings the service itself flags as bad are dropped rather than reported:
    NWS marks these, and passing along a value it has disowned is exactly the
    confidently-wrong failure this module exists to avoid.
    """
    if not isinstance(block, dict):
        return None
    if str(block.get("qualityControl") or "").upper() in {"Z", "X"}:
        return None
    return _convert(_as_float(block.get("value")), str(block.get("unitCode") or ""), want, kind)


def _parse_iso(value) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
