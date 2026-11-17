"""Tests for location resolution.

The central property under test is a negative one: JARVIS must decline to
answer rather than invent a location. Most of the bugs this guards against
produce a *plausible* answer for the wrong place, which no amount of output
polish would reveal.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import Settings
from app.services.location import (
    DEVICE_FIX_TTL,
    Ambiguous,
    DeviceFix,
    GeocodeResult,
    GeocodingUnavailable,
    LocationPreferenceStore,
    LocationResolver,
    LocationSource,
    LocationUnavailable,
    MockGeocoder,
    SavedLocation,
    Units,
    extract_requested_place,
    saved_location_from_settings,
)


def _utcnow():
    return datetime.now(timezone.utc)


SEATTLE = SavedLocation(
    label="Seattle, Washington, United States",
    latitude=47.60621, longitude=-122.33207,
    timezone="America/Los_Angeles", units=Units.US, country_code="US",
)


def resolver(saved=None, fix=None):
    store = LocationPreferenceStore(saved)
    if fix is not None:
        store.set_device_fix(fix)
    return LocationResolver(store, MockGeocoder()), store


# ---------------------------------------------------------------------------
# Refusing to guess
# ---------------------------------------------------------------------------


class TestNeverFabricates:
    def test_nothing_configured_raises_instead_of_defaulting(self):
        r, _ = resolver()
        with pytest.raises(LocationUnavailable) as exc:
            r.resolve("what's the weather")
        # The message has to be answerable by the user, not an error code.
        assert "which city" in str(exc.value).lower()

    def test_resolver_has_no_access_to_ip_timezone_or_locale(self):
        """Structural, not behavioural.

        The resolver is constructed with exactly two collaborators. It cannot
        consult the machine's timezone, its locale or an IP geolocation
        service because it was never handed any of them -- which is a stronger
        guarantee than a test asserting it doesn't.
        """
        import inspect

        from app.services import location as mod

        params = list(inspect.signature(LocationResolver.__init__).parameters)
        assert params == ["self", "store", "geocoder"]

        source = inspect.getsource(mod.LocationResolver)
        for forbidden in ("tzlocal", "getlocale", "time.timezone", "ipapi",
                          "geoip", "astimezone()", "socket."):
            assert forbidden not in source, f"resolver reached for {forbidden}"

    def test_unfindable_named_place_does_not_fall_back_to_saved_default(self):
        """The dangerous fallback.

        Asking for Atlantis while a Seattle default exists must not quietly
        answer for Seattle: that reports a real forecast for a place the user
        did not ask about, with nothing in the answer revealing the swap.
        """
        r, _ = resolver(saved=SEATTLE)
        with pytest.raises(LocationUnavailable) as exc:
            r.resolve("what's the weather in Atlantis")
        assert "atlantis" in str(exc.value).lower()

    def test_geocoder_outage_is_reported_not_swallowed(self):
        class Dead:
            def search(self, query, limit=5):
                raise GeocodingUnavailable("service down")

        store = LocationPreferenceStore(SEATTLE)
        r = LocationResolver(store, Dead())
        with pytest.raises(GeocodingUnavailable):
            r.resolve("weather in Denver")


# ---------------------------------------------------------------------------
# Priority order
# ---------------------------------------------------------------------------


class TestPriority:
    def test_saved_default_used_when_no_place_named(self):
        r, _ = resolver(saved=SEATTLE)
        loc = r.resolve("what's the weather")
        assert loc.source is LocationSource.SAVED_DEFAULT
        assert loc.latitude == pytest.approx(47.60621)

    def test_explicit_place_beats_saved_default(self):
        r, _ = resolver(saved=SEATTLE)
        loc = r.resolve("what's the weather in Denver")
        assert loc.source is LocationSource.EXPLICIT_REQUEST
        assert "Denver" in loc.label
        assert loc.latitude == pytest.approx(39.73915)

    def test_explicit_place_does_not_overwrite_saved_default(self):
        """Asking about Denver must not relocate the user to Denver."""
        r, store = resolver(saved=SEATTLE)
        r.resolve("weather in Denver")
        assert store.saved().label == SEATTLE.label
        # ...and the next unqualified question is still Seattle.
        assert r.resolve("what's the weather").latitude == pytest.approx(47.60621)

    def test_fresh_device_fix_beats_saved_default(self):
        fix = DeviceFix(latitude=39.7392, longitude=-104.9903,
                        timezone="America/Denver", label="Denver",
                        reported_at=_utcnow(), permission_granted=True)
        r, _ = resolver(saved=SEATTLE, fix=fix)
        loc = r.resolve("what's the weather")
        assert loc.source is LocationSource.DEVICE_FIX
        assert not loc.is_stale

    def test_stale_device_fix_loses_to_saved_default(self):
        old = _utcnow() - (DEVICE_FIX_TTL + timedelta(minutes=5))
        fix = DeviceFix(latitude=39.7392, longitude=-104.9903,
                        timezone="America/Denver", reported_at=old,
                        permission_granted=True)
        r, _ = resolver(saved=SEATTLE, fix=fix)
        assert r.resolve("weather").source is LocationSource.SAVED_DEFAULT

    def test_stale_device_fix_used_when_nothing_else_but_labelled_stale(self):
        old = _utcnow() - (DEVICE_FIX_TTL + timedelta(minutes=5))
        fix = DeviceFix(latitude=39.7392, longitude=-104.9903,
                        timezone="America/Denver", reported_at=old,
                        permission_granted=True)
        r, _ = resolver(fix=fix)
        loc = r.resolve("weather")
        assert loc.source is LocationSource.DEVICE_FIX
        assert loc.is_stale
        assert "old" in loc.provenance_note()


class TestDevicePermission:
    def test_fix_without_permission_is_refused_loudly(self):
        store = LocationPreferenceStore()
        with pytest.raises(PermissionError):
            store.set_device_fix(DeviceFix(latitude=1.0, longitude=2.0,
                                           timezone="UTC", permission_granted=False))
        assert store.device_fix() is None

    def test_clearing_a_fix_falls_back_not_forward(self):
        fix = DeviceFix(latitude=39.7392, longitude=-104.9903, timezone="America/Denver",
                        reported_at=_utcnow(), permission_granted=True)
        r, store = resolver(saved=SEATTLE, fix=fix)
        assert r.resolve("weather").source is LocationSource.DEVICE_FIX
        store.clear_device_fix()
        assert r.resolve("weather").source is LocationSource.SAVED_DEFAULT


# ---------------------------------------------------------------------------
# Parsing a place out of a request
# ---------------------------------------------------------------------------


class TestPlaceExtraction:
    @pytest.mark.parametrize("text,expected", [
        ("what's the weather in Denver", "Denver"),
        ("weather for London", "London"),
        ("what's the forecast in San Francisco?", "San Francisco"),
        ("news near Portland", "Portland"),
        ("what's the weather in 98101", "98101"),
    ])
    def test_finds_named_places(self, text, expected):
        assert extract_requested_place(text) == expected

    @pytest.mark.parametrize("text", [
        "what's the weather",
        "what's the weather in the morning",
        "what's the weather today",
        "will it rain tonight",
        "explain this in detail",
        "tell me the weather in general",
        "",
    ])
    def test_declines_to_find_a_place_where_there_is_none(self, text):
        """A false positive here geocodes 'the morning' and answers for
        somewhere arbitrary; a false negative merely uses the saved default,
        which is recoverable. The parser is tuned to err the safe way."""
        assert extract_requested_place(text) is None


class TestAmbiguity:
    def test_ambiguous_city_is_reported_not_silently_picked(self):
        r, _ = resolver(saved=SEATTLE)
        result = r.resolve("weather in London", allow_ambiguous=True)
        assert isinstance(result, Ambiguous)
        countries = {c.country_code for c in result.candidates}
        assert {"GB", "CA"} <= countries

    def test_without_allow_ambiguous_the_largest_is_chosen(self):
        r, _ = resolver()
        loc = r.resolve("weather in London")
        # London, England (8.9M) over London, Ontario (347k).
        assert loc.country_code == "GB"

    def test_seattle_mexico_does_not_win_over_seattle_washington(self):
        r, _ = resolver()
        loc = r.resolve("weather in Seattle")
        assert loc.country_code == "US"
        assert loc.timezone == "America/Los_Angeles"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestSavedFromSettings:
    def test_unconfigured_settings_give_no_location(self):
        assert saved_location_from_settings(Settings(_env_file=None)) is None

    def test_latitude_without_longitude_is_unconfigured_not_half_a_location(self):
        s = Settings(_env_file=None, home_latitude=47.6)
        assert saved_location_from_settings(s) is None

    def test_out_of_range_coordinates_rejected(self):
        s = Settings(_env_file=None, home_latitude=999.0, home_longitude=0.0)
        assert saved_location_from_settings(s) is None

    def test_configured_location_round_trips_with_units(self):
        s = Settings(_env_file=None, home_location_label="Seattle",
                     home_latitude=47.60621, home_longitude=-122.33207,
                     home_timezone="America/Los_Angeles", preferred_units="metric")
        saved = saved_location_from_settings(s)
        assert saved is not None
        assert saved.units is Units.METRIC
        assert saved.timezone == "America/Los_Angeles"

    def test_coordinates_without_label_get_a_coordinate_label(self):
        s = Settings(_env_file=None, home_latitude=47.60621, home_longitude=-122.33207)
        saved = saved_location_from_settings(s)
        assert "47.6062" in saved.label


class TestProvenanceIsVisible:
    def test_every_source_explains_itself(self):
        r, _ = resolver(saved=SEATTLE)
        assert "saved default" in r.resolve("weather").provenance_note()
        assert "as you asked" in r.resolve("weather in Denver").provenance_note()

    def test_explicit_change_of_default_is_deliberate(self):
        r, store = resolver(saved=SEATTLE)
        saved = r.set_saved_from_place("Denver")
        assert "Denver" in saved.label
        assert store.saved().label == saved.label
        assert r.resolve("weather").source is LocationSource.SAVED_DEFAULT


class TestCoordinateValidation:
    def test_impossible_coordinates_rejected_at_construction(self):
        from app.services.location import LocationPrecision, ResolvedLocation
        with pytest.raises(ValueError):
            ResolvedLocation(label="x", latitude=91.0, longitude=0.0, timezone="UTC",
                             source=LocationSource.SAVED_DEFAULT,
                             precision=LocationPrecision.CITY, units=Units.US)


class TestGeocoderParsing:
    def test_malformed_row_does_not_lose_the_good_ones(self):
        """One bad row in a provider response must not discard the rest."""
        from app.services.location import OpenMeteoGeocoder

        class FakeHttp:
            def get_json(self, url, params, timeout):
                return {"results": [
                    {"name": "Broken"},  # no coordinates
                    {"name": "Denver", "latitude": 39.73, "longitude": -104.98,
                     "timezone": "America/Denver", "country": "United States",
                     "country_code": "US", "admin1": "Colorado", "population": 715522},
                ]}

        results = OpenMeteoGeocoder(http_client=FakeHttp()).search("Denver")
        assert len(results) == 1
        assert results[0].name == "Denver"

    def test_documented_error_shape_raises(self):
        from app.services.location import OpenMeteoGeocoder

        class FakeHttp:
            def get_json(self, url, params, timeout):
                return {"error": True, "reason": "bad request"}

        with pytest.raises(GeocodingUnavailable):
            OpenMeteoGeocoder(http_client=FakeHttp()).search("Denver")

    def test_missing_results_key_is_not_a_crash(self):
        """The no-match response shape is undocumented, so both an absent
        `results` key and an empty list have to work."""
        from app.services.location import OpenMeteoGeocoder

        class NoKey:
            def get_json(self, url, params, timeout):
                return {}

        class EmptyList:
            def get_json(self, url, params, timeout):
                return {"results": []}

        assert OpenMeteoGeocoder(http_client=NoKey()).search("Nowhere") == []
        assert OpenMeteoGeocoder(http_client=EmptyList()).search("Nowhere") == []

    def test_one_character_query_costs_no_network_call(self):
        from app.services.location import OpenMeteoGeocoder

        class Exploding:
            def get_json(self, url, params, timeout):
                raise AssertionError("should not have made a request")

        assert OpenMeteoGeocoder(http_client=Exploding()).search("a") == []

    def test_duplicate_rows_collapse_by_name_country_admin1(self):
        from app.services.location import _distinct_places
        rows = [
            GeocodeResult("Denver", 39.7, -104.9, "America/Denver", "United States", "US", "Colorado", 715522),
            GeocodeResult("Denver", 39.8, -105.0, "America/Denver", "United States", "US", "Colorado", 10),
        ]
        assert len(_distinct_places(rows)) == 1
