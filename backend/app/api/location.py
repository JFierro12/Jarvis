"""Location and weather endpoints for the dashboard.

These exist to remove a second, contradictory location path. The dashboard
widget used to resolve location entirely in the browser and, on any failure --
permission denied, timeout, geolocation unsupported -- silently fell back to
hardcoded coordinates for New York and rendered a real temperature next to the
label "New York, NY". A user in Seattle saw a confident, specific, wrong
answer with nothing marking it as a guess.

Routing the widget through here puts it behind the same `LocationResolver` the
`get_weather` tool uses, so there is one place that decides where the user is,
one set of rules about what may be inferred, and no hardcoded city anywhere.
"""

from datetime import datetime
from datetime import timezone as tz

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings, get_settings
from app.core.security import require_bearer_token
from app.services.location import (
    DeviceFix,
    LocationUnavailable,
    Units,
    get_location_resolver,
    get_location_store,
)
from app.services.weather import WeatherUnavailable, get_weather_service

router = APIRouter()


class DeviceFixRequest(BaseModel):
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    accuracy_metres: float | None = None
    timezone: str = ""
    label: str = ""


class LocationResponse(BaseModel):
    label: str
    latitude: float
    longitude: float
    timezone: str
    source: str
    precision: str
    units: str
    is_stale: bool
    provenance: str


class WeatherResponse(BaseModel):
    location: LocationResponse
    temperature: float | None = None
    temperature_unit: str
    condition: str
    humidity_percent: float | None = None
    wind_speed: float | None = None
    wind_unit: str
    observed_at: str | None = None
    provider: str
    from_cache: bool
    cache_age_seconds: float
    partial_reason: str = ""


def _as_location_response(location) -> LocationResponse:
    return LocationResponse(
        label=location.label,
        latitude=location.latitude,
        longitude=location.longitude,
        timezone=location.timezone,
        source=location.source.value,
        precision=location.precision.value,
        units=location.units.value,
        is_stale=location.is_stale,
        provenance=location.provenance_note(),
    )


@router.post("/v1/location/device-fix", response_model=LocationResponse)
def report_device_fix(
    payload: DeviceFixRequest,
    request: Request,
    _token: str = Depends(require_bearer_token),
    settings: Settings = Depends(get_settings),
) -> LocationResponse:
    """Accept a position the browser obtained through its permission prompt.

    Reaching this endpoint at all means `navigator.geolocation` already
    returned a fix, which only happens after the user granted permission --
    so `permission_granted` is set here and nowhere else. The browser cannot
    ask the backend to believe in a permission it never obtained, because the
    backend has no other way to set that flag.

    The fix is held in memory only. Persisting a location history is not
    needed for answering "what's the weather" and is exactly the kind of data
    this project does not keep by default.
    """
    store = get_location_store(settings)
    store.set_device_fix(DeviceFix(
        latitude=payload.latitude,
        longitude=payload.longitude,
        timezone=payload.timezone or "UTC",
        label=payload.label,
        accuracy_metres=payload.accuracy_metres,
        reported_at=datetime.now(tz.utc),
        permission_granted=True,
    ))
    resolver = get_location_resolver(settings)
    return _as_location_response(resolver.resolve(""))


@router.delete("/v1/location/device-fix")
def clear_device_fix(
    request: Request,
    _token: str = Depends(require_bearer_token),
    settings: Settings = Depends(get_settings),
):
    """Forget the device position, e.g. when the user revokes permission."""
    get_location_store(settings).clear_device_fix()
    return {"cleared": True}


@router.get("/v1/location", response_model=LocationResponse)
def current_location(
    request: Request,
    _token: str = Depends(require_bearer_token),
    settings: Settings = Depends(get_settings),
) -> LocationResponse:
    try:
        return _as_location_response(get_location_resolver(settings).resolve(""))
    except LocationUnavailable as exc:
        # 409, not 500: nothing is broken. There is simply no location, and
        # the correct response is to ask rather than to invent one.
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/v1/weather", response_model=WeatherResponse)
async def current_weather(
    request: Request,
    place: str = "",
    _token: str = Depends(require_bearer_token),
    settings: Settings = Depends(get_settings),
) -> WeatherResponse:
    """Current conditions for the resolved location.

    `place` names an explicit destination for this request only; it never
    becomes the saved default.
    """
    resolver = get_location_resolver(settings)
    try:
        location = resolver.resolve(f"in {place}" if place else "")
    except LocationUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        report = await run_in_threadpool(get_weather_service(settings).report, location, 1)
    except WeatherUnavailable as exc:
        # 503: the provider failed. Deliberately not a cached-looking success.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    current = report.current
    return WeatherResponse(
        location=_as_location_response(report.location),
        temperature=current.temperature if current else None,
        temperature_unit="F" if location.units is Units.US else "C",
        condition=(current.condition if current else "Unavailable"),
        humidity_percent=current.humidity_percent if current else None,
        wind_speed=current.wind_speed if current else None,
        wind_unit="mph" if location.units is Units.US else "km/h",
        observed_at=(current.observed_at.isoformat()
                     if current and current.observed_at else None),
        provider=report.provider,
        from_cache=report.from_cache,
        cache_age_seconds=round(report.cache_age_seconds, 1),
        partial_reason=report.partial_reason,
    )
