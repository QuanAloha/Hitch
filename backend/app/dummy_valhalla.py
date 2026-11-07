
import uuid
import math

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Any

import httpx

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


# ==========================================
# CONFIGURATION
# ==========================================

VALHALLA_URL = "http://localhost:8002"
MAX_STORED_ROUTES = 1000

app = FastAPI(title="Dummy Valhalla API", version="0.5.0")


# ==========================================
# DATA STRUCTURES
# ==========================================

@dataclass
class location_node:
    node_type: str
    stop_id: str
    origin: Optional[dict[str, Any]] = None
    destination: Optional[dict[str, Any]] = None


@dataclass
class stored_route:
    route_id: str
    origin: dict
    destination: dict
    locations: list[dict]
    costing: str
    route_plan: dict
    route_time: float
    created_at: str


# ==========================================
# IN-MEMORY DATABASE
# ==========================================

_route_database: dict[str, stored_route] = {}


def clear_database():
    _route_database.clear()


def get_all_routes() -> list[stored_route]:
    return list(_route_database.values())


def _store_route(route: stored_route):

    _route_database[route.route_id] = route

    while len(_route_database) > MAX_STORED_ROUTES:
        oldest_route_id = next(iter(_route_database))
        del _route_database[oldest_route_id]


# ==========================================
# API REQUEST MODEL
# ==========================================

class RouteRequest(BaseModel):

    # Now supports any number of locations >= 2.
    locations: list[dict[str, Any]] = Field(
        min_length=2
    )

    costing: str = "auto"


# ==========================================
# LOCATION VALIDATION
# ==========================================

def _validate_location(location: dict, index: int):

    if not isinstance(location, dict):
        raise HTTPException(
            status_code=422,
            detail=f"Location {index} must be an object."
        )

    try:
        lat = float(location["lat"])
        lon = float(location["lon"])

    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Location {index} must contain numeric lat and lon."
        ) from exc

    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(
            status_code=422,
            detail=f"Location {index} has invalid coordinates."
        )


# ==========================================
# DUMMY ROUTING
# ==========================================

def _haversine_miles(origin: dict, destination: dict) -> float:

    earth_radius_miles = 3958.7613

    lat1 = math.radians(float(origin["lat"]))
    lon1 = math.radians(float(origin["lon"]))

    lat2 = math.radians(float(destination["lat"]))
    lon2 = math.radians(float(destination["lon"]))

    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(delta_lon / 2) ** 2
    )

    return 2 * earth_radius_miles * math.asin(math.sqrt(a))


def _build_dummy_route_plan(
    locations: list[dict],
    costing: str,
    reason: str = "Dummy routing enabled"
) -> tuple[dict, float]:

    average_speeds_mph = {
        "auto": 30.0,
        "motorcycle": 35.0,
        "truck": 25.0,
        "bicycle": 10.0,
        "pedestrian": 3.0,
    }

    average_speed = average_speeds_mph.get(costing, 30.0)

    total_distance = 0.0
    total_duration = 0.0

    legs = []

    # Calculate every consecutive journey.
    for index in range(len(locations) - 1):

        origin = locations[index]
        destination = locations[index + 1]

        straight_line = _haversine_miles(
            origin,
            destination
        )

        road_distance = straight_line * 1.3

        duration = (
            road_distance / average_speed * 3600.0
            if road_distance > 0
            else 0.0
        )

        total_distance += road_distance
        total_duration += duration

        legs.append({
            "from": origin,
            "to": destination,
            "summary": {
                "time": duration,
                "length": road_distance
            }
        })

    route_plan = {
        "dummy_fallback": True,
        "fallback_reason": reason,
        "trip": {
            "summary": {
                "time": total_duration,
                "length": total_distance
            },
            "locations": locations,
            "legs": legs
        }
    }

    return route_plan, total_duration


# ==========================================
# ROUTE GENERATION
# ==========================================

async def generate_route(
    locations: list[dict],
    costing: str = "auto"
) -> dict:

    """
    Generate a route using any number of locations >= 2.

    Uses Valhalla when available.
    Falls back to dummy routing when unavailable.

    Returns:
        {
            "route_id": str,
            "route_time": float,
            "route_source": str
        }

    route_time is returned in minutes.
    """

    if len(locations) < 2:
        raise ValueError(
            "At least two locations are required."
        )

    # Validate every location.
    for index, location in enumerate(locations, start=1):
        _validate_location(location, index)

    # Copy input locations.
    locations = [dict(location) for location in locations]

    # Prepare Valhalla locations.
    valhalla_locations = [
        {
            **location,
            "type": location.get("type", "break")
        }
        for location in locations
    ]

    payload = {
        "locations": valhalla_locations,
        "costing": costing,
        "units": "miles",
        "directions_type": "instructions"
    }

    route_source = "valhalla"

    try:

        async with httpx.AsyncClient(timeout=15.0) as client:

            response = await client.post(
                f"{VALHALLA_URL}/route",
                json=payload
            )

            response.raise_for_status()

            route_plan = response.json()

    except httpx.RequestError:

        route_source = "dummy"

        route_plan, duration_seconds = _build_dummy_route_plan(
            locations,
            costing
        )

    else:

        try:

            duration_seconds = float(
                route_plan["trip"]["summary"]["time"]
            )

            if not math.isfinite(duration_seconds) or duration_seconds < 0:
                raise ValueError("Invalid route duration")

        except (KeyError, TypeError, ValueError) as exc:

            raise HTTPException(
                status_code=502,
                detail="Invalid Valhalla route response."
            ) from exc

    route_time_minutes = round(
        duration_seconds / 60.0,
        2
    )

    route_id = str(uuid.uuid4())

    route = stored_route(
        route_id=route_id,
        origin=locations[0],
        destination=locations[-1],
        locations=locations,
        costing=costing,
        route_plan=route_plan,
        route_time=route_time_minutes,
        created_at=datetime.now(timezone.utc).isoformat()
    )

    _store_route(route)

    return {
        "route_id": route_id,
        "route_time": route_time_minutes,
        "route_source": route_source
    }


# ==========================================
# DATABASE RETRIEVAL
# ==========================================

def get_stored_route(route_id: str) -> dict:

    try:
        uuid.UUID(route_id)

    except (ValueError, TypeError, AttributeError) as exc:

        raise HTTPException(
            status_code=422,
            detail="route_id must be a valid UUID."
        ) from exc

    route = _route_database.get(route_id)

    if route is None:

        raise HTTPException(
            status_code=404,
            detail="Route not found."
        )

    return {
        "route_id": route.route_id,
        "origin": route.origin,
        "destination": route.destination,
        "locations": route.locations,
        "costing": route.costing,
        "route_plan": route.route_plan,
        "route_time": route.route_time,
        "created_at": route.created_at
    }


async def retrieve_route(route_id: str) -> dict:
    return get_stored_route(route_id)


# ==========================================
# API ENDPOINTS
# ==========================================

@app.post("/route")
async def get_route(request: RouteRequest):

    return await generate_route(
        request.locations,
        request.costing
    )


@app.get("/route/{route_id}")
def get_route_by_id(route_id: str):

    return get_stored_route(route_id)


@app.get("/routes")
def get_all_routes_endpoint():

    routes = get_all_routes()

    return {
        "total_routes": len(routes),
        "routes": [
            {
                "route_id": route.route_id,
                "origin": route.origin,
                "destination": route.destination,
                "locations": route.locations,
                "costing": route.costing,
                "route_time": route.route_time,
                "created_at": route.created_at
            }
            for route in routes
        ]
    }


@app.delete("/routes")
def clear_routes_endpoint():

    clear_database()

    return {
        "message": "Route database cleared."
    }
