import os
import uuid
import math
from dataclasses import dataclass
from typing import Optional, Any

import httpx
import psycopg
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


# ==========================================
# CONFIGURATION
# ==========================================

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://hitch:hitch_dev_password@localhost:5432/hitch",
)
VALHALLA_URL = os.getenv("VALHALLA_URL", "http://localhost:8002").rstrip("/")

app = FastAPI(title="Dummy Valhalla API", version="0.3.0")
_database_initialized = False


# ==========================================
# DATA STRUCTURES
# ==========================================

@dataclass
class location_node:
    node_type: str
    stop_id: str
    origin: Optional[dict[str, Any]] = None
    destination: Optional[dict[str, Any]] = None


# ==========================================
# DATABASE
# ==========================================

def get_db_connection():
    return psycopg.connect(DATABASE_URL, connect_timeout=5)


def initialize_database() -> None:
    """Create the routes table once; safe to call from scripts or API startup."""
    global _database_initialized
    if _database_initialized:
        return

    try:
        with get_db_connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS routes (
                    route_id UUID PRIMARY KEY,
                    origin JSONB NOT NULL,
                    destination JSONB NOT NULL,
                    costing TEXT NOT NULL,
                    route_plan JSONB NOT NULL,
                    route_time_minutes DOUBLE PRECISION NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        _database_initialized = True
    except psycopg.Error as exc:
        raise RuntimeError(
            f"Could not initialize PostgreSQL table using DATABASE_URL={DATABASE_URL!r}"
        ) from exc


@app.on_event("startup")
def startup() -> None:
    initialize_database()


# ==========================================
# API REQUEST MODEL
# ==========================================

class RouteRequest(BaseModel):
    locations: list[dict[str, Any]] = Field(min_length=2, max_length=2)
    costing: str = "auto"


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
    origin: dict, destination: dict, costing: str, reason: str
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
    straight_line_miles = _haversine_miles(origin, destination)
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
            "locations": [origin, destination],
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
    if len(locations) != 2:
        raise ValueError("Exactly two locations required")

    _validate_location(locations[0], 1)
    _validate_location(locations[1], 2)

    # Copy the caller's dictionaries; don't mutate their input objects.
    valhalla_locations = [
        {**locations[0], "type": locations[0].get("type", "break")},
        {**locations[1], "type": locations[1].get("type", "break")},
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
            locations[0],
            locations[1],
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
        initialize_database()
        with get_db_connection() as connection:
            connection.execute(
                """
                INSERT INTO routes (
                    route_id, origin, destination, costing,
                    route_plan, route_time_minutes
                )
                VALUES (%s, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s)
                """,
                (
                    route_id,
                    psycopg.types.json.Jsonb(locations[0]),
                    psycopg.types.json.Jsonb(locations[1]),
                    costing,
                    psycopg.types.json.Jsonb(route_plan),
                    route_time_minutes,
                ),
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

def get_stored_route(route_id: str) -> dict:
    try:
        parsed_id = uuid.UUID(route_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=422, detail="route_id must be a valid UUID.") from exc

    try:
        initialize_database()
        with get_db_connection() as connection:
            row = connection.execute(
                """
                SELECT route_id, origin, destination, costing,
                       route_plan, route_time_minutes, created_at
                FROM routes
                WHERE route_id = %s
                """,
                (parsed_id,),
            ).fetchone()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Database unavailable.") from exc

    if row is None:
        raise HTTPException(status_code=404, detail="Route not found.")

    return {
        "route_id": str(row[0]),
        "origin": row[1],
        "destination": row[2],
        "costing": row[3],
        "route_plan": row[4],
        "route_time": row[5],
        "created_at": row[6],
    }


async def retrieve_route(route_id: str) -> dict:
    """Async-compatible helper used by pathing.py; reads the persisted route."""
    return get_stored_route(route_id)


# ==========================================
# API ENDPOINTS
# ==========================================

@app.post("/route")
async def get_route(request: RouteRequest):
    return await generate_route(request.locations, request.costing)


@app.get("/route/{route_id}")
def get_route_by_id(route_id: str):
    return get_stored_route(route_id)


@app.get("/routes")
def get_all_routes():
    try:
        initialize_database()
        with get_db_connection() as connection:
            rows = connection.execute(
                """
                SELECT route_id, origin, destination, costing,
                       route_time_minutes, created_at
                FROM routes
                ORDER BY created_at DESC
                """
            ).fetchall()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Database unavailable.") from exc

    return {
        "total_routes": len(rows),
        "routes": [
            {
                "route_id": str(row[0]),
                "origin": row[1],
                "destination": row[2],
                "costing": row[3],
                "route_time": row[4],
                "created_at": row[5],
            }
            for row in rows
        ],
    }
