import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, enum.Enum):
    shipper = "shipper"
    carrier = "carrier"


class ShipmentStatus(str, enum.Enum):
    open = "open"
    assigned = "assigned"
    in_transit = "in_transit"
    delivered = "delivered"
    cancelled = "cancelled"


class ParcelStatus(str, enum.Enum):
    pending = "pending"
    delivered = "delivered"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[Role] = mapped_column(Enum(Role, name="user_role"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Shipment(Base):
    __tablename__ = "shipments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    shipper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    carrier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[ShipmentStatus] = mapped_column(
        Enum(ShipmentStatus, name="shipment_status"), default=ShipmentStatus.open, index=True
    )
    pickup_address: Mapped[str] = mapped_column(String(300))
    pickup_lat: Mapped[float] = mapped_column(Float)
    pickup_lon: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    parcels: Mapped[list["Parcel"]] = relationship(
        back_populates="shipment", cascade="all, delete-orphan", lazy="selectin", order_by="Parcel.created_at"
    )
    route: Mapped["Route | None"] = relationship(
        back_populates="shipment", cascade="all, delete-orphan", lazy="selectin", uselist=False
    )


class Parcel(Base):
    __tablename__ = "parcels"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    shipment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shipments.id", ondelete="CASCADE"), index=True)
    description: Mapped[str] = mapped_column(String(300))
    weight_kg: Mapped[float] = mapped_column(Float)
    length_cm: Mapped[float | None] = mapped_column(Float)
    width_cm: Mapped[float | None] = mapped_column(Float)
    height_cm: Mapped[float | None] = mapped_column(Float)
    dropoff_address: Mapped[str] = mapped_column(String(300))
    dropoff_lat: Mapped[float] = mapped_column(Float)
    dropoff_lon: Mapped[float] = mapped_column(Float)
    status: Mapped[ParcelStatus] = mapped_column(
        Enum(ParcelStatus, name="parcel_status"), default=ParcelStatus.pending
    )
    stop_order: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    shipment: Mapped[Shipment] = relationship(back_populates="parcels")


class Route(Base):
    __tablename__ = "routes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    shipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"), unique=True
    )
    carrier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    start_lat: Mapped[float] = mapped_column(Float)
    start_lon: Mapped[float] = mapped_column(Float)
    distance_km: Mapped[float] = mapped_column(Float)
    duration_s: Mapped[float] = mapped_column(Float)
    stops: Mapped[list] = mapped_column(JSONB)  # ordered pickup + dropoff stops
    legs: Mapped[list] = mapped_column(JSONB)  # per-leg distance/duration + polyline6 shape
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    shipment: Mapped[Shipment] = relationship(back_populates="route")
