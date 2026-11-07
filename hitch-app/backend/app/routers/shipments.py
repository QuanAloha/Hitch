import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user, get_valhalla, require_role
from app.models import Parcel, Role, Route, Shipment, ShipmentStatus, User
from app.schemas import ParcelCreate, RouteOut, ShipmentCreate, ShipmentOut, StartRequest
from app.services.routing import plan_route
from app.services.valhalla import ValhallaClient

router = APIRouter(prefix="/shipments", tags=["shipments"])

DB = Annotated[AsyncSession, Depends(get_db)]
Shipper = Annotated[User, Depends(require_role(Role.shipper))]
Carrier = Annotated[User, Depends(require_role(Role.carrier))]
Anyone = Annotated[User, Depends(get_current_user)]
Valhalla = Annotated[ValhallaClient, Depends(get_valhalla)]


def _parcel(body: ParcelCreate) -> Parcel:
    return Parcel(**body.model_dump())


async def _get(db: AsyncSession, shipment_id: uuid.UUID, lock: bool = False) -> Shipment:
    stmt = select(Shipment).where(Shipment.id == shipment_id)
    if lock:
        stmt = stmt.with_for_update(of=Shipment)
    shipment = (await db.execute(stmt)).scalar_one_or_none()
    if shipment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shipment not found")
    return shipment


def _can_view(user: User, s: Shipment) -> bool:
    if user.role == Role.shipper:
        return s.shipper_id == user.id
    return s.carrier_id == user.id or s.status == ShipmentStatus.open


async def _build_route(shipment: Shipment, carrier: User, body: StartRequest, valhalla: ValhallaClient) -> Route:
    plan = await plan_route(
        valhalla,
        start=(body.start.lat, body.start.lon),
        pickup=(shipment.pickup_lat, shipment.pickup_lon),
        parcels=shipment.parcels,
    )
    by_id = {str(p.id): p for p in shipment.parcels}
    for stop in plan["stops"]:
        for pid in stop["parcel_ids"]:
            by_id[pid].stop_order = stop["sequence"]

    fields = dict(
        carrier_id=carrier.id,
        start_lat=body.start.lat,
        start_lon=body.start.lon,
        distance_km=plan["distance_km"],
        duration_s=plan["duration_s"],
        stops=plan["stops"],
        legs=plan["legs"],
    )
    if shipment.route is None:
        shipment.route = Route(**fields)
    else:
        for k, v in fields.items():
            setattr(shipment.route, k, v)
    return shipment.route


# ---------- shipper endpoints ----------
@router.post("", response_model=ShipmentOut, status_code=status.HTTP_201_CREATED)
async def create_shipment(body: ShipmentCreate, db: DB, user: Shipper):
    shipment = Shipment(
        shipper_id=user.id,
        title=body.title,
        pickup_address=body.pickup_address,
        pickup_lat=body.pickup_lat,
        pickup_lon=body.pickup_lon,
        parcels=[_parcel(p) for p in body.parcels],
    )
    db.add(shipment)
    await db.commit()
    return shipment


@router.post("/{shipment_id}/parcels", response_model=ShipmentOut)
async def add_parcels(shipment_id: uuid.UUID, body: list[ParcelCreate], db: DB, user: Shipper):
    shipment = await _get(db, shipment_id, lock=True)
    if shipment.shipper_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shipment not found")
    if shipment.status != ShipmentStatus.open:
        raise HTTPException(status.HTTP_409_CONFLICT, "Parcels can only be added while the shipment is open")
    shipment.parcels.extend(_parcel(p) for p in body)
    await db.commit()
    return shipment


@router.post("/{shipment_id}/cancel", response_model=ShipmentOut)
async def cancel_shipment(shipment_id: uuid.UUID, db: DB, user: Shipper):
    shipment = await _get(db, shipment_id, lock=True)
    if shipment.shipper_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shipment not found")
    if shipment.status not in (ShipmentStatus.open, ShipmentStatus.assigned):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot cancel a {shipment.status.value} shipment")
    shipment.status = ShipmentStatus.cancelled
    await db.commit()
    return shipment


# ---------- shared ----------
@router.get("", response_model=list[ShipmentOut])
async def list_shipments(
    db: DB,
    user: Anyone,
    available: Annotated[bool, Query(description="Carriers only: list open shipments instead of their own")] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    stmt = select(Shipment).order_by(Shipment.created_at.desc()).limit(limit).offset(offset)
    if user.role == Role.shipper:
        stmt = stmt.where(Shipment.shipper_id == user.id)
    elif available:
        stmt = stmt.where(Shipment.status == ShipmentStatus.open)
    else:
        stmt = stmt.where(Shipment.carrier_id == user.id)
    return (await db.execute(stmt)).scalars().all()


@router.get("/{shipment_id}", response_model=ShipmentOut)
async def get_shipment(shipment_id: uuid.UUID, db: DB, user: Anyone):
    shipment = await _get(db, shipment_id)
    if not _can_view(user, shipment):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shipment not found")
    return shipment


# ---------- carrier endpoints + routing ----------
@router.post("/{shipment_id}/accept", response_model=RouteOut)
async def accept_shipment(shipment_id: uuid.UUID, body: StartRequest, db: DB, user: Carrier, valhalla: Valhalla):
    """Claim an open shipment and generate the carrier's route in one step."""
    shipment = await _get(db, shipment_id, lock=True)
    if shipment.status != ShipmentStatus.open:
        raise HTTPException(status.HTTP_409_CONFLICT, "Shipment is no longer available")
    route = await _build_route(shipment, user, body, valhalla)  # raises -> nothing is assigned
    shipment.carrier_id = user.id
    shipment.status = ShipmentStatus.assigned
    await db.commit()
    return route


@router.post("/{shipment_id}/route", response_model=RouteOut)
async def rebuild_route(shipment_id: uuid.UUID, body: StartRequest, db: DB, user: Carrier, valhalla: Valhalla):
    """Recompute the route from a new starting position (e.g. the carrier's current location)."""
    shipment = await _get(db, shipment_id, lock=True)
    if shipment.carrier_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shipment not found")
    if shipment.status not in (ShipmentStatus.assigned, ShipmentStatus.in_transit):
        raise HTTPException(status.HTTP_409_CONFLICT, "Shipment is not active")
    route = await _build_route(shipment, user, body, valhalla)
    await db.commit()
    return route


@router.get("/{shipment_id}/route", response_model=RouteOut)
async def get_route(shipment_id: uuid.UUID, db: DB, user: Anyone):
    shipment = await _get(db, shipment_id)
    allowed = shipment.shipper_id == user.id or shipment.carrier_id == user.id
    if not allowed or shipment.route is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Route not found")
    return shipment.route
