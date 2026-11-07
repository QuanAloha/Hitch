import os
import uuid
import math

import httpx
import psycopg
from fastapi import FastAPI, HTTPException

from app.database import (
    get_route,
    initialize_database,
    list_routes,
    save_route,
)
from app.models import Route, RouteRequest, location_node  # noqa: F401  (re-exported)

VALHALLA_URL = os.getenv("VALHALLA_URL", "http://localhost:8002").rstrip("/")

app = FastAPI(title="Dummy Valhalla API", version="0.3.0")


@app.on_event("startup")
def startup() -> None:
    initialize_database()


# ==========================================
# ROUTE GENERATION / PERSISTENCE
# ==========================================

def _validate_location(location: dict, index: int) -> None:
    if not isinstance(location, dict):
        raise HTTPException(status_code=422, detail=f"Location {index} must be an object.")

    try:
        lat = float(location["lat"])
        lon = float(location["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Location {index} must contain numeric 'lat' and 'lon' fields.",
        ) from exc

    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(
            status_code=422,
            detail=f"Location {index} has coordinates outside valid latitude/longitude ranges.",
        )


def _haversine_miles(origin: dict, destination: dict) -> float:
    """Calculate straight-line distance between two coordinates."""
    earth_radius_miles = 3958.7613
    lat1 = math.radians(float(origin["lat"]))
    lon1 = math.radians(float(origin["lon"]))
    lat2 = math.radians(float(destination["lat"]))
    lon2 = math.radians(float(destination["lon"]))

    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * earth_radius_miles * math.asin(math.sqrt(a))


def _build_dummy_route_plan(
    locations: list[dict], costing: str, reason: str
) -> tuple[dict, float]:
    """
    Build an explicitly-marked approximate route when Valhalla is unreachable.
    This estimates duration from straight-line distance, not road geometry.
    """
    # Approximate average speeds in miles per hour. A road-distance multiplier
    # accounts roughly for roads being less direct than a straight line.
    average_speeds_mph = {
        "auto": 30.0,
        "motorcycle": 35.0,
        "truck": 25.0,
        "bicycle": 10.0,
        "pedestrian": 3.0,
    }
    straight_line_miles = sum(
        _haversine_miles(a, b) for a, b in zip(locations, locations[1:])
    )
    estimated_road_miles = straight_line_miles * 1.3
    average_speed = average_speeds_mph.get(costing, 30.0)
    duration_seconds = (
        estimated_road_miles / average_speed * 3600.0
        if estimated_road_miles > 0
        else 0.0
    )
    route_plan = {
        "dummy_fallback": True,
        "fallback_reason": reason,
        "warning": (
            "Approximate straight-line estimate; this is not a Valhalla route "
            "and does not include road geometry or turn-by-turn directions."
        ),
        "trip": {
            "summary": {
                "time": duration_seconds,
                "length": estimated_road_miles,
            },
            "locations": locations,
            "legs": [],
        },
    }
    return route_plan, duration_seconds


async def generate_route(locations: list[dict], costing: str = "auto") -> dict:
    """
    Calculate a route with Valhalla, or use a clearly-marked dummy estimate
    if Valhalla cannot be reached. Persist either result in PostgreSQL and
    return the database route UUID and journey duration in minutes.

    This function is intentionally usable directly by pathing.py and by FastAPI.
    """
    if len(locations) < 2:
        raise ValueError("At least two locations required")

    for index, location in enumerate(locations, start=1):
        _validate_location(location, index)

    # Copy the caller's dictionaries; don't mutate their input objects.
    # Every stop is a "break" so Valhalla returns one leg per consecutive pair.
    valhalla_locations = [
        {**location, "type": location.get("type", "break")} for location in locations
    ]
    payload = {
        "locations": valhalla_locations,
        "costing": costing,
        "units": "miles",
        "directions_type": "instructions",
    }

    route_source = "valhalla"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(f"{VALHALLA_URL}/route", json=payload)
            response.raise_for_status()
            route_plan = response.json()
    except httpx.RequestError as exc:
        # Connection failures, DNS errors and timeouts mean Valhalla is
        # unreachable. Use a dummy estimate so offline development can proceed.
        route_source = "dummy_fallback"
        route_plan, duration_seconds = _build_dummy_route_plan(
            locations,
            costing,
            reason=f"{type(exc).__name__}: {str(exc) or 'Valhalla unavailable'}",
        )
    except httpx.HTTPStatusError as exc:
        # Valhalla was reachable but rejected the request; don't hide genuine
        # routing errors behind a fabricated route.
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Valhalla routing failed.",
                "valhalla_error": exc.response.text[:2000],
            },
        ) from exc
    else:
        try:
            # Valhalla trip.summary.time is seconds, regardless of distance units.
            duration_seconds = float(route_plan["trip"]["summary"]["time"])
            if duration_seconds < 0:
                raise ValueError("Negative route duration")
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=502,
                detail="Valhalla response did not contain a valid trip.summary.time.",
            ) from exc

    route_time_minutes = round(duration_seconds / 60.0, 2)
    route_id = str(uuid.uuid4())

    try:
        save_route(
            Route(
                route_id=route_id,
                locations=locations,
                costing=costing,
                route_time_minutes=route_time_minutes,
                route_plan=route_plan,
            )
        )
    except psycopg.Error as exc:
        raise HTTPException(
            status_code=503,
            detail="Route was calculated, but PostgreSQL could not save it.",
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "route_id": route_id,
        "route_time": route_time_minutes,
        "route_source": route_source,
    }


# ==========================================
# DATABASE RETRIEVAL
# ==========================================

def get_stored_route(route_id: str) -> Route:
    """Load a persisted route as a Route dataclass."""
    try:
        parsed_id = uuid.UUID(route_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=422, detail="route_id must be a valid UUID.") from exc

    try:
        route = get_route(str(parsed_id))
    except (psycopg.Error, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Database unavailable.") from exc

    if route is None:
        raise HTTPException(status_code=404, detail="Route not found.")
    return route


async def retrieve_route(route_id: str) -> Route:
    """Async-compatible helper used by pathing.py; reads the persisted route."""
    return get_stored_route(route_id)


def route_to_response(route: Route) -> dict:
    """JSON shape returned by the API for a stored route."""
    return {
        "route_id": route.route_id,
        "origin": route.origin,
        "destination": route.destination,
        "locations": route.locations,
        "costing": route.costing,
        "route_plan": route.route_plan,
        "route_time": route.route_time_minutes,
        "created_at": route.created_at,
    }


# ==========================================
# API ENDPOINTS
# ==========================================

@app.post("/route")
async def get_route_endpoint(request: RouteRequest):
    return await generate_route(request.locations, request.costing)


@app.get("/route/{route_id}")
def get_route_by_id(route_id: str):
    return route_to_response(get_stored_route(route_id))


@app.get("/routes")
def get_all_routes():
    try:
        routes = list_routes()
    except (psycopg.Error, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Database unavailable.") from exc

    return {
        "total_routes": len(routes),
        "routes": [
            {k: v for k, v in route_to_response(route).items() if k != "route_plan"}
            for route in routes
        ],
    }
