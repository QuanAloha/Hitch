import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import ParcelStatus, Role, ShipmentStatus


class Coordinates(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


# ---- auth ----
class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)  # bcrypt's input limit
    full_name: str = Field(min_length=1, max_length=120)
    role: Role


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: EmailStr
    full_name: str
    role: Role
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---- shipments / parcels ----
class ParcelCreate(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    weight_kg: float = Field(gt=0)
    length_cm: float | None = Field(default=None, gt=0)
    width_cm: float | None = Field(default=None, gt=0)
    height_cm: float | None = Field(default=None, gt=0)
    dropoff_address: str = Field(min_length=1, max_length=300)
    dropoff_lat: float = Field(ge=-90, le=90)
    dropoff_lon: float = Field(ge=-180, le=180)


class ParcelOut(ParcelCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    status: ParcelStatus
    stop_order: int | None


class ShipmentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    pickup_address: str = Field(min_length=1, max_length=300)
    pickup_lat: float = Field(ge=-90, le=90)
    pickup_lon: float = Field(ge=-180, le=180)
    parcels: list[ParcelCreate] = Field(min_length=1)


class ShipmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    shipper_id: uuid.UUID
    carrier_id: uuid.UUID | None
    title: str
    status: ShipmentStatus
    pickup_address: str
    pickup_lat: float
    pickup_lon: float
    created_at: datetime
    parcels: list[ParcelOut]


# ---- routing ----
class StartRequest(BaseModel):
    start: Coordinates  # carrier's current/starting location


class RouteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    shipment_id: uuid.UUID
    carrier_id: uuid.UUID
    distance_km: float
    duration_s: float
    stops: list[dict]
    legs: list[dict]
    created_at: datetime
