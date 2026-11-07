"""Frontend-facing API (see frontend-backend-handoff.md).

Dispatch owns the creation of orders, pickup runs, and driver routes: the
dispatch algorithm writes those rows and this module only serves them. The
endpoints that accept writes from the UI are seller pickup requests, driver
vehicles, driver availability, and route start / stop completion.

Authentication is not decided yet, so every request is treated as coming from
the single active shipper / seller / driver. Add owner scoping once the
identity mechanism is agreed.
"""

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, Optional

import httpx
import psycopg
from fastapi import APIRouter, HTTPException, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator, model_validator

from app import demo_clock, dispatch_engine
from app.database import get_db_connection, initialize_database
from app.route_geometry import ValhallaUnavailable, fetch_route_geometry, stops_to_locations

router = APIRouter(prefix="/api")

LOCK_BEFORE_START = timedelta(hours=12)
FUEL_STALE_AFTER = timedelta(hours=6)
VIN_PATTERN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

# Standard package spots by NHTSA body class keyword (first match wins).
BODY_CLASS_CAPACITY = [
    ("van", 16),
    ("pickup", 10),
    ("sport utility", 8),
    ("crossover", 8),
    ("wagon", 6),
    ("hatchback", 5),
    ("sedan", 4),
    ("coupe", 3),
]
DEFAULT_CAPACITY = 4


# ==========================================
# HELPERS
# ==========================================

def _now() -> datetime:
    """Demo time: real time plus the offset moved by `python -m app.demo advance`."""
    return demo_clock.now()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _db_call(fn):
    """Run fn(connection); map database failures to a 503."""
    try:
        initialize_database()
        with get_db_connection() as connection:
            return fn(connection)
    except (psycopg.Error, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must include a timezone offset")
    return value


# ==========================================
# SHARED MODELS
# ==========================================

class Dimensions(BaseModel):
    length: float = Field(gt=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    unit: Literal["in", "cm"]


class Place(BaseModel):
    address: str = Field(min_length=1)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    label: Optional[str] = None


class TimeWindow(BaseModel):
    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @model_validator(mode="after")
    def _ordered(self):
        if self.end <= self.start:
            raise ValueError("window end must be after start")
        return self


# ==========================================
# 1. SHIPPER ORDERS AND FULFILLMENT
# ==========================================

@router.get("/shipper/orders")
def list_shipper_orders(
    from_: Optional[datetime] = Query(None, alias="from"),
    to: Optional[datetime] = None,
    fulfillment_type: Optional[
        Literal["local_driver", "fedex", "third_party_logistics"]
    ] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    clauses, params = [], []
    if from_:
        clauses.append("order_date >= %s")
        params.append(from_)
    if to:
        clauses.append("order_date < %s")
        params.append(to)
    if fulfillment_type:
        clauses.append("fulfillment_type = %s")
        params.append(fulfillment_type)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    def run(connection):
        total = connection.execute(
            f"SELECT COUNT(*) FROM shipper_orders {where}", params
        ).fetchone()[0]
        rows = connection.execute(
            f"SELECT data FROM shipper_orders {where} "
            "ORDER BY order_date DESC, id LIMIT %s OFFSET %s",
            [*params, limit, offset],
        ).fetchall()
        return total, [row[0] for row in rows]

    total, orders = _db_call(run)
    return {"orders": orders, "total": total, "limit": limit, "offset": offset}


@router.get("/shipper/pickup-runs")
def list_pickup_runs(date: date):
    rows = _db_call(
        lambda c: c.execute(
            "SELECT data FROM pickup_runs WHERE run_date = %s ORDER BY id", (date,)
        ).fetchall()
    )
    return {"date": date.isoformat(), "pickup_runs": [row[0] for row in rows]}


@router.get("/shipper/freight-summary")
def freight_summary(
    period: Literal["week", "month"],
    from_: Optional[date] = Query(None, alias="from"),
):
    """Independent-driver vs. other-carrier order counts per time bucket.

    week: seven daily buckets starting at `from` (default: today).
    month: weekly buckets covering the calendar month containing `from`.
    """
    start = from_ or _now().date()
    if period == "week":
        end = start + timedelta(days=7)
        bucket_starts = [start + timedelta(days=i) for i in range(7)]
    else:
        start = start.replace(day=1)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        bucket_starts = [start + timedelta(days=i) for i in range(0, (end - start).days, 7)]

    rows = _db_call(
        lambda c: c.execute(
            "SELECT (order_date AT TIME ZONE 'UTC')::date, fulfillment_type "
            "FROM shipper_orders "
            "WHERE (order_date AT TIME ZONE 'UTC')::date >= %s "
            "AND (order_date AT TIME ZONE 'UTC')::date < %s",
            (start, end),
        ).fetchall()
    )

    buckets = []
    for index, bucket_start in enumerate(bucket_starts):
        bucket_end = (
            bucket_starts[index + 1] if index + 1 < len(bucket_starts) else end
        )
        buckets.append(
            {
                "start": bucket_start.isoformat(),
                "end": bucket_end.isoformat(),
                "independent_driver": 0,
                "other_carrier": 0,
            }
        )
    for order_day, fulfillment_type in rows:
        for bucket in buckets:
            if bucket["start"] <= order_day.isoformat() < bucket["end"]:
                key = (
                    "independent_driver"
                    if fulfillment_type == "local_driver"
                    else "other_carrier"
                )
                bucket[key] += 1
                break

    return {
        "period": period,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "measure": "orders",
        "buckets": buckets,
        "totals": {
            "independent_driver": sum(b["independent_driver"] for b in buckets),
            "other_carrier": sum(b["other_carrier"] for b in buckets),
        },
    }


# ==========================================
# 2. HOME-SELLER PICKUP REQUESTS
# ==========================================

class PickupRequestCreate(BaseModel):
    source: Literal["etsy", "facebook_marketplace"]
    product_name: str = Field(min_length=1)
    quantity: int = Field(default=1, ge=1)
    dimensions: Dimensions
    notes: str = ""
    pickup: Place
    destination: Place
    available_window: TimeWindow


def store_pickup_request(body: PickupRequestCreate, created_at: datetime, request_id: Optional[str] = None) -> dict:
    """Insert a seller pickup request into the queue (shared by the API and demo scripts)."""
    request_id = request_id or _new_id("pickup-request")
    document = {
        "id": request_id,
        **body.model_dump(mode="json", exclude={"available_window"}),
        # What the seller offered, kept apart from what dispatch confirms.
        "available_window": body.available_window.model_dump(mode="json"),
        "status": "awaiting_driver",
        "pickup_run_id": None,
        "driver_id": None,
        "driver_name": None,
        "confirmed_pickup_window": None,
        "created_at": created_at.isoformat(),
    }
    _db_call(
        lambda c: c.execute(
            "INSERT INTO seller_pickup_requests (id, source, created_at, data) "
            "VALUES (%s, %s, %s, %s)",
            (request_id, body.source, created_at, Jsonb(document)),
        )
    )
    return document


@router.post("/seller/pickup-requests", status_code=201)
def create_pickup_request(body: PickupRequestCreate):
    created_at = _now()
    document = store_pickup_request(body, created_at)
    # Join the queue now: shows where it will land (provisional) or opens a last-minute request.
    try:
        dispatch_engine.on_new_booking(created_at)
        document = _db_call(
            lambda c: c.execute("SELECT data FROM seller_pickup_requests WHERE id = %s", (document["id"],)).fetchone()[0]
        )
    except Exception:  # booking must never fail because planning did
        pass
    return document


@router.get("/seller/pickup-requests")
def list_pickup_requests(
    source: Optional[Literal["etsy", "facebook_marketplace"]] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    where, params = ("WHERE source = %s", [source]) if source else ("", [])
    rows = _db_call(
        lambda c: c.execute(
            f"SELECT data FROM seller_pickup_requests {where} "
            "ORDER BY created_at DESC LIMIT %s OFFSET %s",
            [*params, limit, offset],
        ).fetchall()
    )
    return {"pickup_requests": [row[0] for row in rows], "limit": limit, "offset": offset}


@router.get("/seller/pickup-requests/{request_id}")
def get_pickup_request(request_id: str):
    row = _db_call(
        lambda c: c.execute(
            "SELECT data FROM seller_pickup_requests WHERE id = %s", (request_id,)
        ).fetchone()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Pickup request not found")
    return row[0]


# ==========================================
# 3. DRIVER VEHICLES AND AVAILABILITY
# ==========================================

class VehicleCreate(BaseModel):
    vin: str
    plate: str = Field(min_length=1, max_length=10)
    plate_state: str = Field(pattern=r"^[A-Za-z]{2}$")

    @field_validator("vin")
    @classmethod
    def _vin(cls, value: str) -> str:
        value = value.strip().upper()
        if not VIN_PATTERN.match(value):
            raise ValueError("VIN must be 17 characters (letters except I, O, Q, and digits)")
        return value


def _estimate_capacity(body_class: str) -> int:
    lowered = body_class.lower()
    for keyword, spots in BODY_CLASS_CAPACITY:
        if keyword in lowered:
            return spots
    return DEFAULT_CAPACITY


async def _decode_vin(vin: str) -> dict[str, Any]:
    url = f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(url, params={"format": "json"})
            response.raise_for_status()
            result = response.json()["Results"][0]
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="VIN lookup service unavailable") from exc

    if not result.get("Make") or not result.get("Model") or not result.get("ModelYear"):
        raise HTTPException(status_code=422, detail="VIN could not be decoded to a vehicle")
    return result


def _public_vehicle(document: dict[str, Any]) -> dict[str, Any]:
    """Vehicle response without the full VIN."""
    return {k: v for k, v in document.items() if k != "vin"}


@router.get("/driver/vehicles")
def list_vehicles():
    rows = _db_call(
        lambda c: c.execute(
            "SELECT data FROM driver_vehicles "
            "WHERE COALESCE(data->>'driver_id', %s) = %s ORDER BY created_at",
            (dispatch_engine.DEFAULT_DRIVER_ID, dispatch_engine.DEFAULT_DRIVER_ID),
        ).fetchall()
    )
    return {"vehicles": [_public_vehicle(row[0]) for row in rows]}


@router.post("/driver/vehicles", status_code=201)
async def create_vehicle(body: VehicleCreate):
    decoded = await _decode_vin(body.vin)
    body_class = decoded.get("BodyClass") or ""
    vehicle_id = _new_id("vehicle")
    document = {
        "id": vehicle_id,
        "vin": body.vin,
        "vin_last4": body.vin[-4:],
        "plate": body.plate.strip().upper(),
        "plate_state": body.plate_state.upper(),
        "year": int(decoded["ModelYear"]),
        "make": decoded["Make"].title(),
        "model": decoded["Model"],
        "body_class": body_class,
        "name": f"{decoded['ModelYear']} {decoded['Make'].title()} {decoded['Model']}",
        "capacity": {
            "value": _estimate_capacity(body_class),
            "unit": "standard_package_spots",
            "source": "estimated_from_body_class",
        },
    }

    def run(connection):
        try:
            connection.execute(
                "INSERT INTO driver_vehicles (id, vin, data) VALUES (%s, %s, %s)",
                (vehicle_id, body.vin, Jsonb(document)),
            )
        except psycopg.errors.UniqueViolation:
            raise HTTPException(status_code=409, detail="Vehicle already saved")

    _db_call(run)
    # VIN is kept in storage for dispatch but not echoed back.
    return {k: v for k, v in _public_vehicle(document).items()}


@router.delete("/driver/vehicles/{vehicle_id}", status_code=204)
def delete_vehicle(vehicle_id: str):
    def run(connection):
        in_use = connection.execute(
            "SELECT 1 FROM driver_availability WHERE vehicle_id = %s AND end_at > NOW()",
            (vehicle_id,),
        ).fetchone()
        if in_use:
            raise HTTPException(
                status_code=409,
                detail="Vehicle has upcoming availability; cancel it first",
            )
        return connection.execute(
            "DELETE FROM driver_vehicles WHERE id = %s", (vehicle_id,)
        ).rowcount

    if _db_call(run) == 0:
        raise HTTPException(status_code=404, detail="Vehicle not found")


class AvailabilityCreate(BaseModel):
    vehicle_id: str
    start_location: Place
    window: TimeWindow
    fuel_level_percent: float = Field(ge=0, le=100)


def _route_state(route_doc: Optional[dict[str, Any]], locks_at: datetime) -> str:
    """forming | locked | started | completed (server clock is authoritative)."""
    if route_doc:
        if route_doc.get("completed_at"):
            return "completed"
        if route_doc.get("started_at"):
            return "started"
    return "locked" if _now() >= locks_at else "forming"


def _availability_response(
    document: dict[str, Any], route_doc: Optional[dict[str, Any]]
) -> dict[str, Any]:
    locks_at = datetime.fromisoformat(document["locks_at"])
    state = _route_state(route_doc, locks_at)
    # Availability collapses started into locked: the handoff lists only
    # forming | locked | completed for blocks.
    block_status = "locked" if state == "started" else state
    locked = state != "forming"
    route_summary = None
    if route_doc:
        route_summary = {
            "id": route_doc["id"],
            "state": state,
            "stop_count": len(route_doc.get("stops", [])),
            "fill_percent": route_doc.get("fill_percent"),
        }
    return {
        **document,
        "route_status": block_status,
        "route_id": route_doc["id"] if route_doc else None,
        "route": route_summary,
        "locked_at": document["locks_at"] if locked else None,
        "estimated_fill_percent": (
            route_doc.get("fill_percent") if route_doc else document.get("estimated_fill_percent", 0)
        ),
        "confirmed_package_spots": (
            route_doc.get("confirmed_package_spots", 0) if route_doc else 0
        ),
    }


def _routes_by_id(connection, route_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not route_ids:
        return {}
    rows = connection.execute(
        "SELECT id, data FROM driver_routes WHERE id = ANY(%s)", (route_ids,)
    ).fetchall()
    return {row[0]: row[1] for row in rows}


@router.get("/driver/availability")
def list_availability(
    from_: Optional[datetime] = Query(None, alias="from"),
    to: Optional[datetime] = None,
):
    """Every availability block the driver posted, forming or locked."""
    clauses, params = ["COALESCE(data->>'driver_id', %s) = %s"], [dispatch_engine.DEFAULT_DRIVER_ID] * 2
    if from_:
        clauses.append("end_at > %s")
        params.append(from_)
    if to:
        clauses.append("start_at < %s")
        params.append(to)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    def run(connection):
        rows = connection.execute(
            f"SELECT data, route_id FROM driver_availability {where} ORDER BY start_at",
            params,
        ).fetchall()
        routes = _routes_by_id(connection, [r[1] for r in rows if r[1]])
        return [_availability_response(r[0], routes.get(r[1])) for r in rows]

    return {"availability": _db_call(run)}


@router.post("/driver/availability", status_code=201)
def create_availability(body: AvailabilityCreate):
    if body.window.start <= _now():
        raise HTTPException(status_code=422, detail="Availability must start in the future")

    def run(connection):
        row = connection.execute(
            "SELECT data FROM driver_vehicles WHERE id = %s", (body.vehicle_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Vehicle not found")
        vehicle = row[0]

        overlap = connection.execute(
            "SELECT 1 FROM driver_availability WHERE start_at < %s AND end_at > %s "
            "AND COALESCE(data->>'driver_id', %s) = %s",
            (body.window.end, body.window.start, dispatch_engine.DEFAULT_DRIVER_ID, dispatch_engine.DEFAULT_DRIVER_ID),
        ).fetchone()
        if overlap:
            raise HTTPException(
                status_code=409, detail="Overlaps an existing availability block"
            )

        block_id = _new_id("availability")
        document = {
            "id": block_id,
            "vehicle_id": vehicle["id"],
            "vehicle_name": vehicle["name"],
            "capacity": vehicle["capacity"],
            "start_location": body.start_location.model_dump(mode="json"),
            "window": body.window.model_dump(mode="json"),
            "fuel_level_percent": body.fuel_level_percent,
            "locks_at": (body.window.start - LOCK_BEFORE_START).isoformat(),
            "estimated_fill_percent": 0,
        }
        connection.execute(
            "INSERT INTO driver_availability "
            "(id, vehicle_id, start_at, end_at, data) VALUES (%s, %s, %s, %s, %s)",
            (block_id, vehicle["id"], body.window.start, body.window.end, Jsonb(document)),
        )
        return _availability_response(document, None)

    return _db_call(run)


@router.delete("/driver/availability/{block_id}", status_code=204)
def cancel_availability(block_id: str):
    def run(connection):
        row = connection.execute(
            "SELECT data, route_id FROM driver_availability WHERE id = %s", (block_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Availability block not found")
        route_doc = _routes_by_id(connection, [row[1]] if row[1] else []).get(row[1])
        if _availability_response(row[0], route_doc)["route_status"] != "forming":
            raise HTTPException(
                status_code=409,
                detail="Route is locked; availability can no longer be cancelled",
            )
        connection.execute("DELETE FROM driver_availability WHERE id = %s", (block_id,))

    _db_call(run)


# ==========================================
# 4. DRIVER ROUTES, STOPS, RECEIPT
# ==========================================

def _get_route_doc(connection, route_id: str, for_update: bool = False) -> tuple[dict, dict]:
    """Return (route document, its availability document) or raise 404."""
    row = connection.execute(
        "SELECT data, availability_id FROM driver_routes WHERE id = %s"
        + (" FOR UPDATE" if for_update else ""),
        (route_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Route not found")
    availability = connection.execute(
        "SELECT data FROM driver_availability WHERE id = %s", (row[1],)
    ).fetchone()
    if availability is None:
        raise HTTPException(status_code=404, detail="Route availability block not found")
    return row[0], availability[0]


def _route_response(route_doc: dict, availability: dict) -> dict:
    locks_at = datetime.fromisoformat(availability["locks_at"])
    state = _route_state(route_doc, locks_at)
    locked = state != "forming"
    stops = route_doc.get("stops", [])
    return {
        "id": route_doc["id"],
        "availability_id": availability["id"],
        "state": state,
        "locks_at": availability["locks_at"],
        "locked_at": availability["locks_at"] if locked else None,
        "started_at": route_doc.get("started_at"),
        "completed_at": route_doc.get("completed_at"),
        "stops": stops,
        "next_stop_id": next((s["id"] for s in stops if not s.get("completed_at")), None),
        "geometry": route_doc.get("geometry"),
        "fill_percent": route_doc.get("fill_percent"),
        "confirmed_package_spots": route_doc.get("confirmed_package_spots"),
        "capacity": availability["capacity"],
        # Final windows and payout are only meaningful once the route locks.
        "windows": route_doc.get("windows") if locked else None,
        "receipt": route_doc.get("receipt") if locked else None,
        "fuel_stops": _fuel_stops(route_doc),
    }


def _fuel_stops(route_doc: dict) -> list[dict]:
    result = []
    for stop in route_doc.get("fuel_stops", []):
        updated_at = datetime.fromisoformat(stop["updated_at"])
        result.append({**stop, "is_stale": _now() - updated_at > FUEL_STALE_AFTER})
    return result


def _ensure_valhalla_geometry(route_doc: dict) -> None:
    """Give the route a road-following line from Valhalla, cached on the route.

    If Valhalla is unreachable the stored geometry (possibly none) is left as is,
    and the frontend falls back to a stop-to-stop preview.
    """
    if (route_doc.get("geometry") or {}).get("source") == "valhalla":
        return
    locations = stops_to_locations(route_doc.get("stops", []))
    if not locations:
        return
    try:
        route_doc["geometry"] = fetch_route_geometry(locations)["geometry"]
    except ValhallaUnavailable:
        return
    _db_call(lambda c: _save_route_doc(c, route_doc))


@router.get("/driver/routes/{route_id}")
def get_driver_route(route_id: str):
    route_doc, availability = _db_call(lambda c: _get_route_doc(c, route_id))
    _ensure_valhalla_geometry(route_doc)
    return _route_response(route_doc, availability)


class RoutePreviewRequest(BaseModel):
    locations: list[Place] = Field(min_length=2, max_length=25)


@router.post("/driver/route-preview")
def preview_route(body: RoutePreviewRequest):
    """Road geometry for a set of candidate stops (nothing is stored)."""
    try:
        return fetch_route_geometry([p.model_dump() for p in body.locations])
    except ValhallaUnavailable as exc:
        raise HTTPException(status_code=503, detail="Routing service unavailable") from exc


@router.get("/driver/routes/{route_id}/fuel-stops")
def get_route_fuel_stops(route_id: str):
    route_doc, _ = _db_call(lambda c: _get_route_doc(c, route_id))
    return {"fuel_stops": _fuel_stops(route_doc)}


def _save_route_doc(connection, route_doc: dict) -> None:
    connection.execute(
        "UPDATE driver_routes SET data = %s WHERE id = %s",
        (Jsonb(route_doc), route_doc["id"]),
    )


@router.post("/driver/routes/{route_id}/start")
def start_route(route_id: str):
    def run(connection):
        route_doc, availability = _get_route_doc(connection, route_id, for_update=True)
        state = _route_state(route_doc, datetime.fromisoformat(availability["locks_at"]))
        if state == "started":
            return _route_response(route_doc, availability)  # idempotent
        if state != "locked":
            raise HTTPException(
                status_code=409, detail=f"Route is {state}; only a locked route can start"
            )
        route_doc["started_at"] = _iso(_now())
        _save_route_doc(connection, route_doc)
        return _route_response(route_doc, availability)

    return _db_call(run)


@router.post("/driver/routes/{route_id}/stops/{stop_id}/complete")
def complete_stop(route_id: str, stop_id: str):
    def run(connection):
        route_doc, availability = _get_route_doc(connection, route_id, for_update=True)
        state = _route_state(route_doc, datetime.fromisoformat(availability["locks_at"]))
        if state not in ("started", "completed"):
            raise HTTPException(status_code=409, detail="Start the route before completing stops")

        stops = route_doc.get("stops", [])
        stop = next((s for s in stops if s["id"] == stop_id), None)
        if stop is None:
            raise HTTPException(status_code=404, detail="Stop not found")

        if not stop.get("completed_at"):
            pending = [s for s in stops if not s.get("completed_at")]
            if pending[0]["id"] != stop_id:
                raise HTTPException(
                    status_code=409,
                    detail=f"Stops must be completed in order; next is {pending[0]['id']}",
                )
            stop["completed_at"] = _iso(_now())
            if all(s.get("completed_at") for s in stops):
                route_doc["completed_at"] = _iso(_now())
            _save_route_doc(connection, route_doc)

        response = _route_response(route_doc, availability)
        next_id = response["next_stop_id"]
        return {
            "completed_stop_id": stop_id,
            "next_stop": next((s for s in stops if s["id"] == next_id), None),
            "route": response,
        }

    return _db_call(run)


# ==========================================
# 6. LAST-MINUTE REQUESTS + DEMO CLOCK
# ==========================================

def _preview_stops(request_doc: dict, blk) -> list[dict]:
    """Start point, then the stops this request would add, in route order."""
    new_ids = set(request_doc["package_ids"])
    stops = [{"id": "start", "type": "start", "label": "Start · your route", "lat": blk.start[0], "lon": blk.start[1]}] if blk else []
    if blk:
        for index, stop in enumerate(blk.seq):
            if stop.pkg.id in new_ids:
                place = stop.pkg.pick_info if stop.kind == "pickup" else stop.pkg.drop_info
                stops.append({
                    "id": f"{stop.kind}-{index}", "type": "pickup" if stop.kind == "pickup" else "dropoff",
                    "label": f"{'Pickup' if stop.kind == 'pickup' else 'Drop-off'} · {place.get('label') or place.get('address')}",
                    "lat": stop.pt[0], "lon": stop.pt[1],
                })
    return stops


@router.get("/driver/last-minute-requests")
def list_last_minute_requests():
    """Open requests nobody could cover on time, with the best fit on this driver's routes."""
    now = _now()

    def run(connection):
        rows = connection.execute(
            "SELECT data FROM last_minute_requests WHERE status = 'open' ORDER BY created_at"
        ).fetchall()
        results = []
        for (doc,) in rows:
            if datetime.fromisoformat(doc["pickup_window"]["end"]) <= now:
                continue
            fits = dispatch_engine.find_fits(connection, doc, now)
            fit = None
            blk = None
            if fits:
                rec, extra, blk, _ = fits[0]
                fit = {
                    "availability_id": rec.id, "route_id": rec.route_id,
                    "extra_drive_minutes": round(extra / 60), "route_state": "locked" if rec.route_id else "forming",
                }
            results.append({**doc, "fit": fit, "preview_stops": _preview_stops(doc, blk)})
        return results

    return {"requests": _db_call(run)}


class LastMinuteAccept(BaseModel):
    availability_id: Optional[str] = None


@router.post("/driver/last-minute-requests/{request_id}/accept")
def accept_last_minute_request(request_id: str, body: LastMinuteAccept = LastMinuteAccept()):
    try:
        return dispatch_engine.accept_last_minute(request_id, body.availability_id, _now())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/demo/clock")
def get_demo_clock():
    """Demo time, so the browser can run on the same clock as the dispatcher."""
    current = _now()
    return {"now": current.isoformat(), "offset_seconds": demo_clock.offset_seconds()}
