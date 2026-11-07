
import uuid
import random

from dataclasses import dataclass
from collections import OrderedDict
from typing import Optional, Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


# ==========================================
# CONFIGURATION
# ==========================================

MAX_STORED_ROUTES = 1000

app = FastAPI(title="Dummy Valhalla API", version="0.1.0")


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
    origin: location_node
    destination: location_node
    route_time: int  # Minutes


# ==========================================
# ROUTE STORAGE
# ==========================================

# OrderedDict preserves insertion order.
# Oldest routes are removed when storage is full.

route_history: OrderedDict[str, stored_route] = OrderedDict()


def store_route(route: stored_route):
    route_history[route.route_id] = route

    while len(route_history) > MAX_STORED_ROUTES:
        route_history.popitem(last=False)


# ==========================================
# API REQUEST MODELS
# ==========================================

class RouteRequest(BaseModel):
    locations: list[dict[str, Any]] = Field(
        min_length=2,
        max_length=2
    )

    costing: str = "auto"


# ==========================================
# ROUTE GENERATION
# ==========================================

def generate_route_time() -> int:
    """
    Generate a simulated route duration in minutes.
    """

    return random.randint(5, 120)


# ==========================================
# API ENDPOINTS
# ==========================================

@app.post("/route")
async def get_route(request: RouteRequest):

    if len(request.locations) != 2:
        raise HTTPException(
            status_code=400,
            detail="Exactly two locations are required."
        )

    origin = location_node(
        node_type="pickup",
        stop_id=str(uuid.uuid4()),
        origin=request.locations[0]
    )

    destination = location_node(
        node_type="dropoff",
        stop_id=str(uuid.uuid4()),
        destination=request.locations[1]
    )

    route_id = str(uuid.uuid4())

    route_time = generate_route_time()

    route = stored_route(
        route_id=route_id,
        origin=origin,
        destination=destination,
        route_time=route_time
    )

    store_route(route)

    return {
        "route_id": route_id,
        "route_time": route_time
    }


@app.get("/route/{route_id}")
async def get_stored_route(route_id: str):

    route = route_history.get(route_id)

    if route is None:
        raise HTTPException(
            status_code=404,
            detail="Route not found."
        )

    return {
        "route_id": route.route_id,
        "route_time": route.route_time
    }


@app.get("/routes")
async def get_all_routes():

    return {
        "total_routes": len(route_history),
        "maximum_routes": MAX_STORED_ROUTES,
        "routes": [
            {
                "route_id": route.route_id,
                "route_time": route.route_time
            }
            for route in route_history.values()
        ]
    }


# HELPER functions
async def generate_route(locations: list[dict]) -> dict:
    if len(locations) != 2:
        raise ValueError("Exactly two locations required")

    request = RouteRequest(locations=locations)
    return await get_route(request)


async def retrieve_route(route_id: str) -> dict:
    return await get_stored_route(route_id)