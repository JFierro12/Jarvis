"""Tests for the dashboard location/weather endpoints.

These replaced a browser-side path that fell back to hardcoded New York
coordinates whenever geolocation failed, and rendered a real temperature
beside the label "New York, NY". The regression worth guarding is not that
the endpoint works -- it is that no code path anywhere produces a location
nobody asked for.
"""

import pytest

from app.core.config import Settings
from app.services.location import SavedLocation, Units, get_location_store

from .conftest import AUTH_HEADERS


def saved_seattle():
    store = get_location_store(Settings(_env_file=None))
    store.set_saved(SavedLocation(
        label="Seattle, Washington, United States", latitude=47.60621,
        longitude=-122.33207, timezone="America/Los_Angeles", units=Units.US,
    ))
    return store


class TestNoFabricatedLocation:
    def test_unconfigured_location_returns_409_not_a_default_city(self, client):
        """The bug this endpoint exists to kill.

        With nothing configured the answer is "I don't know", expressed as a
        409 the UI can turn into a prompt -- never a plausible city with a
        real temperature next to it.
        """
        response = client.get("/v1/location", headers=AUTH_HEADERS)
        assert response.status_code == 409
        assert "which city" in response.json()["detail"].lower()

    def test_weather_without_a_location_also_refuses(self, client):
        response = client.get("/v1/weather", headers=AUTH_HEADERS)
        assert response.status_code == 409

    def test_no_new_york_anywhere_in_the_source(self):
        """The old fallback was `{lat: 40.7128, lon: -74.006, place: "New York, NY"}`.

        Asserted against the shipped frontend rather than the backend,
        because that is where it lived and where it would come back.
        """
        import re
        from pathlib import Path

        app_js = Path(__file__).resolve().parents[1] / "app" / "static" / "assets" / "app.js"
        # Comments are stripped first: the comment explaining why the
        # fallback was removed is the record of the decision, and a test
        # that forbade mentioning it would force its deletion.
        code = re.sub(r"^\s*//.*$", "", app_js.read_text(encoding="utf-8"), flags=re.MULTILINE)
        assert "40.7128" not in code, "hardcoded New York fallback is back"
        assert "-74.006" not in code
        assert "New York, NY" not in code

    def test_frontend_no_longer_calls_an_undisclosed_geocoder(self):
        """The old path sent precise coordinates to api.bigdatacloud.net,
        a third party documented nowhere in the project."""
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "app" / "static" / "assets"
                  / "app.js").read_text(encoding="utf-8")
        # The URL, not the word: the comment explaining why it was removed
        # is worth keeping, and a test that forbade naming it would delete
        # the only record of the decision.
        assert "https://api.bigdatacloud.net" not in source

    def test_frontend_does_not_call_any_weather_provider_directly(self):
        """Location must be decided in one place. A direct browser call to a
        forecast API bypasses the resolver and reintroduces a second, silent
        notion of where the user is."""
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "app" / "static" / "assets"
                  / "app.js").read_text(encoding="utf-8")
        assert "https://api.open-meteo.com" not in source
        assert "https://api.weather.gov" not in source


class TestDeviceFix:
    def test_posting_a_fix_makes_it_the_resolved_location(self, client):
        response = client.post(
            "/v1/location/device-fix",
            json={"latitude": 39.7392, "longitude": -104.9903,
                  "timezone": "America/Denver", "label": "Denver",
                  "accuracy_metres": 25.0},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "device_fix"
        assert body["latitude"] == pytest.approx(39.7392)
        assert "device's current location" in body["provenance"]

    def test_a_fresh_device_fix_outranks_the_saved_default(self, client):
        saved_seattle()
        client.post("/v1/location/device-fix",
                    json={"latitude": 39.7392, "longitude": -104.9903,
                          "timezone": "America/Denver", "label": "Denver"},
                    headers=AUTH_HEADERS)
        body = client.get("/v1/location", headers=AUTH_HEADERS).json()
        assert body["source"] == "device_fix"

    def test_clearing_the_fix_falls_back_to_the_saved_default(self, client):
        saved_seattle()
        client.post("/v1/location/device-fix",
                    json={"latitude": 39.7392, "longitude": -104.9903,
                          "timezone": "America/Denver"},
                    headers=AUTH_HEADERS)
        client.delete("/v1/location/device-fix", headers=AUTH_HEADERS)
        body = client.get("/v1/location", headers=AUTH_HEADERS).json()
        assert body["source"] == "saved_default"
        assert "Seattle" in body["label"]

    @pytest.mark.parametrize("lat,lon", [(91.0, 0.0), (-91.0, 0.0), (0.0, 181.0), (0.0, -181.0)])
    def test_impossible_coordinates_are_rejected(self, client, lat, lon):
        response = client.post("/v1/location/device-fix",
                               json={"latitude": lat, "longitude": lon},
                               headers=AUTH_HEADERS)
        assert response.status_code == 422

    def test_device_fix_requires_authentication(self, client):
        response = client.post("/v1/location/device-fix",
                               json={"latitude": 0.0, "longitude": 0.0})
        assert response.status_code in (401, 403)

    def test_location_is_not_persisted_to_the_database(self, client):
        """A location history is not needed to answer "what's the weather",
        and keeping one by default is what the project's privacy rules
        forbid. The fix lives in memory only."""
        import inspect

        from app.api import location as mod

        source = inspect.getsource(mod)
        for marker in ("MemoryRow", "db.add", "session.add", "commit()"):
            assert marker not in source, f"device fix reached persistence via {marker}"


class TestWeatherEndpoint:
    def test_weather_for_the_saved_default(self, client):
        saved_seattle()
        response = client.get("/v1/weather", headers=AUTH_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert "Seattle" in body["location"]["label"]
        assert body["location"]["source"] == "saved_default"
        assert body["temperature_unit"] == "F"
        assert body["temperature"] is not None

    def test_explicit_place_does_not_become_the_saved_default(self, client):
        saved_seattle()
        denver = client.get("/v1/weather", params={"place": "Denver"},
                            headers=AUTH_HEADERS).json()
        assert "Denver" in denver["location"]["label"]
        assert denver["location"]["source"] == "explicit_request"

        after = client.get("/v1/location", headers=AUTH_HEADERS).json()
        assert "Seattle" in after["label"], "a one-off query relocated the user"

    def test_unfindable_place_is_a_409_not_a_substitute(self, client):
        saved_seattle()
        response = client.get("/v1/weather", params={"place": "Atlantis"},
                              headers=AUTH_HEADERS)
        assert response.status_code == 409

    def test_response_carries_provenance_for_the_widget_to_display(self, client):
        saved_seattle()
        body = client.get("/v1/weather", headers=AUTH_HEADERS).json()
        assert body["location"]["provenance"]
        assert "from_cache" in body
        assert body["provider"] == "mock"

    def test_metric_preference_changes_the_reported_units(self, client):
        store = get_location_store(Settings(_env_file=None))
        store.set_saved(SavedLocation(
            label="London, England", latitude=51.5085, longitude=-0.1257,
            timezone="Europe/London", units=Units.METRIC,
        ))
        body = client.get("/v1/weather", headers=AUTH_HEADERS).json()
        assert body["temperature_unit"] == "C"
        assert body["wind_unit"] == "km/h"

    def test_provider_failure_is_503_not_a_fabricated_reading(self, client, monkeypatch):
        saved_seattle()
        from app.services import weather as weather_mod

        class Dead:
            def report(self, location, days=3):
                raise weather_mod.WeatherUnavailable("provider down")

        weather_mod.reset_weather_service_for_tests()
        monkeypatch.setattr(weather_mod, "get_weather_provider", lambda s: Dead())
        response = client.get("/v1/weather", headers=AUTH_HEADERS)
        assert response.status_code == 503

    def test_weather_requires_authentication(self, client):
        assert client.get("/v1/weather").status_code in (401, 403)
