"""Tests for the weather providers.

The NWS tests run against responses captured from the live service
(`tests/fixtures/nws/`), not against hand-written JSON. That matters: the bug
these tests exist to catch is an assumption about the response shape, and
hand-written fixtures encode exactly the assumptions under test.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services.location import (
    LocationPrecision,
    LocationSource,
    ResolvedLocation,
    Units,
)
from app.services.weather import (
    MockWeatherProvider,
    NwsWeatherProvider,
    OpenMeteoWeatherProvider,
    UnknownUnit,
    WeatherCache,
    WeatherReport,
    WeatherService,
    WeatherUnavailable,
    _convert,
    describe_wmo,
    summarize,
)

FIXTURES = Path(__file__).parent / "fixtures" / "nws"


def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def seattle(units=Units.US, source=LocationSource.SAVED_DEFAULT):
    return ResolvedLocation(
        label="Seattle, Washington, United States",
        latitude=47.60621, longitude=-122.33207,
        timezone="America/Los_Angeles", source=source,
        precision=LocationPrecision.CITY, units=units, country_code="US",
    )


class FakeNwsHttp:
    """Routes by URL shape to the captured real responses."""

    def __init__(self, fail_observation=False):
        self.fail_observation = fail_observation
        self.calls = []

    def get_json(self, url, params, timeout, headers):
        self.calls.append(url)
        if "/points/" in url:
            return load("points")
        if url.endswith("/stations"):
            return load("stations")
        if "/observations/latest" in url:
            if self.fail_observation:
                raise WeatherUnavailable("station offline")
            return load("observation")
        if "/forecast" in url:
            return load("forecast")
        raise AssertionError(f"unexpected URL {url}")


# ---------------------------------------------------------------------------
# The unit trap
# ---------------------------------------------------------------------------


class TestUnitConversion:
    def test_nws_observation_celsius_is_converted_not_assumed_fahrenheit(self):
        """The headline trap.

        NWS observations are metric with a `unitCode`, *regardless* of the
        `units=us` parameter, which only affects the forecast endpoint. A
        client that trusted `units=us` would read the fixture's degC number
        straight out and report it as Fahrenheit -- a ~20-degree error
        delivered with complete confidence.

        Expected values are derived from the fixture rather than hardcoded:
        the first draft of this test hardcoded a reading captured nine minutes
        earlier and failed when the station reported new weather.
        """
        raw = load("observation")["properties"]["temperature"]
        assert raw["unitCode"] == "wmoUnit:degC", "fixture is no longer metric; retest the assumption"
        celsius = raw["value"]

        provider = NwsWeatherProvider("test-agent", http_client=FakeNwsHttp())
        report = provider.report(seattle(Units.US))
        assert report.current.temperature == pytest.approx(celsius * 9 / 5 + 32, abs=0.05)
        assert report.current.temperature_unit == "F"
        # And the naive bug is genuinely excluded, not merely unlikely.
        assert report.current.temperature != pytest.approx(celsius, abs=0.05)

    def test_same_observation_in_metric_stays_celsius(self):
        celsius = load("observation")["properties"]["temperature"]["value"]
        provider = NwsWeatherProvider("test-agent", http_client=FakeNwsHttp())
        report = provider.report(seattle(Units.METRIC))
        assert report.current.temperature == pytest.approx(celsius, abs=0.05)

    def test_wind_converted_from_kmh_to_mph(self):
        raw = load("observation")["properties"]["windSpeed"]
        assert raw["unitCode"] == "wmoUnit:km_h-1"
        provider = NwsWeatherProvider("test-agent", http_client=FakeNwsHttp())
        report = provider.report(seattle(Units.US))
        assert report.current.wind_speed == pytest.approx(raw["value"] * 0.621371, abs=0.1)

    def test_unrecognised_unit_raises_rather_than_passing_the_number_through(self):
        """Silently passing an unknown unit through is how a pressure in
        pascals becomes a temperature."""
        with pytest.raises(UnknownUnit):
            _convert(42.0, "wmoUnit:furlongs", Units.US, "temperature")

    @pytest.mark.parametrize("value,code,want,expected", [
        (0.0, "wmoUnit:degC", Units.US, 32.0),
        (100.0, "wmoUnit:degC", Units.US, 212.0),
        (-40.0, "wmoUnit:degC", Units.US, -40.0),
        (32.0, "wmoUnit:degF", Units.METRIC, 0.0),
    ])
    def test_temperature_conversion_table(self, value, code, want, expected):
        assert _convert(value, code, want, "temperature") == pytest.approx(expected, abs=0.01)

    def test_quality_controlled_out_reading_is_dropped_not_reported(self):
        from app.services.weather import _measure
        bad = {"value": 999.0, "unitCode": "wmoUnit:degC", "qualityControl": "X"}
        assert _measure(bad, Units.US, "temperature") is None


# ---------------------------------------------------------------------------
# Parsing real responses
# ---------------------------------------------------------------------------


class TestNwsAgainstCapturedResponses:
    def test_forecast_periods_parsed(self):
        provider = NwsWeatherProvider("test-agent", http_client=FakeNwsHttp())
        report = provider.report(seattle(), days=3)
        assert report.periods, "no forecast periods parsed from a real response"
        first = report.periods[0]
        assert first.name
        assert first.temperature is not None
        assert first.condition
        assert isinstance(first.is_daytime, bool)

    def test_observation_carries_station_and_timestamp(self):
        provider = NwsWeatherProvider("test-agent", http_client=FakeNwsHttp())
        report = provider.report(seattle())
        assert report.current.station == "KBFI"
        assert isinstance(report.current.observed_at, datetime)
        assert report.current.condition

    def test_client_must_follow_redirects(self):
        """`/points/{lat},{lon}/stations` answers 301 to the gridpoint path.

        The provider sidesteps it by using the canonical gridpoint URL built
        from the points response, so the station lookup never relies on a
        redirect being followed.
        """
        http = FakeNwsHttp()
        NwsWeatherProvider("test-agent", http_client=http).report(seattle())
        station_calls = [c for c in http.calls if c.endswith("/stations")]
        assert station_calls and "/gridpoints/" in station_calls[0]

    def test_missing_observation_degrades_to_partial_not_failure(self):
        """A forecast without current conditions is still worth having."""
        provider = NwsWeatherProvider("test-agent", http_client=FakeNwsHttp(fail_observation=True))
        report = provider.report(seattle())
        assert report.current is None
        assert report.periods
        assert "current conditions unavailable" in report.partial_reason

    def test_location_outside_nws_coverage_says_so(self):
        class NoGrid:
            def get_json(self, url, params, timeout, headers):
                return {"properties": {}}

        provider = NwsWeatherProvider("test-agent", http_client=NoGrid())
        with pytest.raises(WeatherUnavailable) as exc:
            provider.report(seattle())
        assert "United States" in str(exc.value)

    def test_user_agent_carries_no_personal_information(self):
        provider = NwsWeatherProvider("JarvisAssistant/0.1 (personal assistant)",
                                      http_client=FakeNwsHttp())
        agent = provider._headers["User-Agent"]
        assert "@" not in agent, "a contact email must never be sent to a third party"


# ---------------------------------------------------------------------------
# Open-Meteo (unverified provider)
# ---------------------------------------------------------------------------


class TestOpenMeteo:
    def test_absent_current_units_key_is_not_required(self):
        """The docs list no `current_units` key, so depending on one would
        break against the real service we could not reach."""
        class Http:
            def get_json(self, url, params, timeout, headers):
                return {
                    "current": {"time": "2026-11-13T07:00", "temperature_2m": 53.6,
                                "weather_code": 3, "wind_speed_10m": 7.0,
                                "relative_humidity_2m": 82},
                    "daily": {"time": ["2026-11-13"], "weather_code": [61],
                              "temperature_2m_max": [59.0],
                              "precipitation_probability_max": [80]},
                }

        report = OpenMeteoWeatherProvider(http_client=Http()).report(seattle())
        assert report.current.temperature == pytest.approx(53.6)
        assert report.current.condition == "Overcast"
        assert report.periods[0].condition == "Slight rain"

    def test_missing_current_block_is_partial_not_fatal(self):
        class Http:
            def get_json(self, url, params, timeout, headers):
                return {"daily": {"time": ["2026-11-13"], "weather_code": [0],
                                  "temperature_2m_max": [60.0]}}

        report = OpenMeteoWeatherProvider(http_client=Http()).report(seattle())
        assert report.current is None
        assert report.partial_reason
        assert report.periods

    def test_documented_error_shape_raises(self):
        class Http:
            def get_json(self, url, params, timeout, headers):
                return {"error": True, "reason": "Invalid latitude"}

        with pytest.raises(WeatherUnavailable):
            OpenMeteoWeatherProvider(http_client=Http()).report(seattle())

    def test_timezone_is_sent_because_daily_requires_it(self):
        captured = {}

        class Http:
            def get_json(self, url, params, timeout, headers):
                captured.update(params)
                return {"daily": {"time": []}}

        OpenMeteoWeatherProvider(http_client=Http()).report(seattle())
        assert captured.get("timezone") == "America/Los_Angeles"

    @pytest.mark.parametrize("code,expected", [
        (0, "Clear sky"), (61, "Slight rain"), (95, "Thunderstorm"),
        (None, "Unknown conditions"),
    ])
    def test_wmo_table(self, code, expected):
        assert describe_wmo(code) == expected

    def test_unknown_wmo_code_says_unknown_rather_than_inventing(self):
        assert "7777" in describe_wmo(7777)


# ---------------------------------------------------------------------------
# Caching and degradation
# ---------------------------------------------------------------------------


class TestCaching:
    def test_second_call_is_served_from_cache_and_labelled(self):
        calls = []

        class Counting(MockWeatherProvider):
            def report(self, location, days=3):
                calls.append(1)
                return super().report(location, days)

        service = WeatherService(Counting(), WeatherCache(ttl_seconds=60))
        first = service.report(seattle())
        second = service.report(seattle())
        assert len(calls) == 1
        assert not first.from_cache
        assert second.from_cache

    def test_different_units_are_different_cache_entries(self):
        calls = []

        class Counting(MockWeatherProvider):
            def report(self, location, days=3):
                calls.append(1)
                return super().report(location, days)

        service = WeatherService(Counting(), WeatherCache(ttl_seconds=60))
        service.report(seattle(Units.US))
        service.report(seattle(Units.METRIC))
        assert len(calls) == 2, "unit system must not be served from another unit's cache"

    def test_provider_failure_with_no_cache_propagates(self):
        """No cached data and a dead provider means no answer -- not a
        plausible-sounding invented one."""
        class Dead:
            def report(self, location, days=3):
                raise WeatherUnavailable("network down")

        with pytest.raises(WeatherUnavailable):
            WeatherService(Dead()).report(seattle())

    def test_provider_failure_falls_back_to_stale_cache_with_a_label(self):
        class Flaky:
            def __init__(self):
                self.fail = False

            def report(self, location, days=3):
                if self.fail:
                    raise WeatherUnavailable("network down")
                return MockWeatherProvider().report(location, days)

        flaky = Flaky()
        cache = WeatherCache(ttl_seconds=0.0)  # everything is immediately stale
        service = WeatherService(flaky, cache)
        service.report(seattle())
        flaky.fail = True
        stale = service.report(seattle())
        assert "live update failed" in stale.partial_reason
        assert "cached" in stale.partial_reason

    def test_nearby_coordinates_share_a_cache_entry(self):
        a = seattle()
        from dataclasses import replace
        b = replace(a, latitude=a.latitude + 0.0001)
        assert WeatherCache.key(a, 3) == WeatherCache.key(b, 3)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


class TestSummary:
    def test_summary_always_names_the_location_and_its_source(self):
        report = MockWeatherProvider().report(seattle())
        text = summarize(report)
        assert "Seattle" in text
        assert "saved default" in text

    def test_explicitly_requested_location_is_marked_as_such(self):
        report = MockWeatherProvider().report(seattle(source=LocationSource.EXPLICIT_REQUEST))
        assert "as you asked" in summarize(report)

    def test_cached_answer_is_disclosed(self):
        report = MockWeatherProvider().report(seattle())
        from dataclasses import replace
        cached = replace(report, from_cache=True, cache_age_seconds=1800)
        assert "Cached 30 minutes ago" in summarize(cached)

    def test_stale_device_fix_is_disclosed_in_the_answer(self):
        old = datetime.now(timezone.utc) - timedelta(hours=3)
        loc = ResolvedLocation(
            label="Denver", latitude=39.7, longitude=-104.9, timezone="America/Denver",
            source=LocationSource.DEVICE_FIX, precision=LocationPrecision.COORDINATE,
            units=Units.US, as_of=old,
        )
        assert "over an hour old" in summarize(MockWeatherProvider().report(loc))

    def test_missing_current_conditions_are_admitted(self):
        report = WeatherReport(location=seattle(), current=None, periods=[], provider="x")
        assert "don't have current conditions" in summarize(report)

    def test_mock_varies_with_location(self):
        """A constant mock would let a location-routing bug pass every test."""
        from dataclasses import replace
        a = MockWeatherProvider().report(seattle())
        b = MockWeatherProvider().report(replace(seattle(), latitude=10.0))
        assert a.current.temperature != b.current.temperature
