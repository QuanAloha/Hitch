"""Centralized data classes for the Hitch backend.

Domain objects are plain dataclasses; API request bodies are pydantic models.
See "Data formatting.md" for the intended relationships between them.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ==========================================
# CONSTANTS
# ==========================================

MAXIMUM_NODE_SIZE = 3600  # Seconds (1 hour)
MAXIMUM_DESTINATION_TIME = 15.0  # Minutes


# ==========================================
# ROUTING
# ==========================================

@dataclass
class location_node:
    """An individual stop location."""
    node_type: str  # "pickup" | "dropoff"
    stop_id: str
    origin: Optional[dict[str, Any]] = None
    destination: Optional[dict[str, Any]] = None


@dataclass
class location_cluster:
    """A group of location nodes served together, bounded by total time."""
    cluster_type: str
    locations: list[location_node] = field(default_factory=list)
    ticket_ids: list[str] = field(default_factory=list)  # Parallel to `locations`
    total_time: float = 0.0  # Seconds
    maximum_size: float = MAXIMUM_NODE_SIZE


@dataclass
class route_stop:
    """One ordered stop on a courier route, tagged with the ticket it serves."""
    location: dict[str, Any]
    order_id: str


@dataclass
class Route:
    """A collection of stops, used for map routing/driving.

    `locations` are the coordinate waypoints sent to Valhalla (origin, stops,
    destination). `stop_ids` optionally link the route to stored location_nodes.
    """
    route_id: str
    locations: list[dict[str, Any]] = field(default_factory=list)
    costing: str = "auto"
    route_time_minutes: float = 0.0
    route_plan: dict[str, Any] = field(default_factory=dict)
    stop_ids: list[str] = field(default_factory=list)
    created_at: Optional[datetime] = None

    @property
    def origin(self) -> Optional[dict[str, Any]]:
        return self.locations[0] if self.locations else None

    @property
    def destination(self) -> Optional[dict[str, Any]]:
        return self.locations[-1] if self.locations else None


# ==========================================
# DELIVERY
# ==========================================

@dataclass
class DeliveryTicket:
    """An individual pickup/delivery batch."""
    ticket_id: str
    ticket_type: str  # "pickup" | "dropoff"
    stop_id: str  # References a location_node.stop_id
    description: str = ""
    quantity: int = 1


@dataclass
class Dispatch:
    """A route plus its delivery tickets; this is what gets passed to a driver."""
    dispatch_id: str
    route: Route
    tickets: list[DeliveryTicket] = field(default_factory=list)  # stop_id -> location_node
    driver_id: Optional[str] = None


# ==========================================
# DRIVERS & VEHICLES
# ==========================================

@dataclass
class Car:
    car_id: str
    make: str
    model: str
    year: int
    color: str
    mpg: float
    cargo_capacity: float


@dataclass
class Driver:
    driver_id: str
    name: str
    location: Optional[dict[str, Any]] = None  # {"lat": ..., "lon": ...}
    car_id: Optional[str] = None


@dataclass
class AvailableDriver:
    driver_id: str
    route_origin: dict[str, Any]
    route_end: dict[str, Any]
    shift_start: datetime
    shift_end: datetime


# ==========================================
# API REQUEST MODELS
# ==========================================

class RouteRequest(BaseModel):
    """Two or more locations: origin, any number of stops, then final destination."""
    locations: list[dict[str, Any]] = Field(
        min_length=2,
        examples=[
            [
                {"lat": 40.7608, "lon": -111.891},
                {"lat": 40.5, "lon": -111.8},
                {"lat": 40.2338, "lon": -111.6585},
            ]
        ],
    )
    costing: str = "auto"
