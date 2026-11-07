import os

import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from dummy_valhalla import (
    generate_route,
    get_stored_route,
    initialize_database,
)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://hitch:hitch_dev_password@localhost:5432/hitch",
)

app = FastAPI(title="Hitch API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class RouteRequest(BaseModel):
    locations: list[dict] = Field(
        min_length=2,
        max_length=2,
        examples=[
            [
                {"lat": 40.7608, "lon": -111.891},
                {"lat": 40.2338, "lon": -111.6585},
            ]
        ],
    )
    costing: str = "auto"


@app.on_event("startup")
def startup():
    initialize_database()


@app.get("/")
def root():
    return {"name": "Hitch API", "docs": "/docs"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/db")
def database_health():
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3) as connection:
            connection.execute("SELECT 1")
        return {"status": "ok"}
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc


@app.post("/route")
@app.post("/api/route")
async def create_route(request: RouteRequest):
    """
    Calculate route through Valhalla, persist it in PostgreSQL,
    and return its database ID and duration in minutes.
    """
    return await generate_route(request.locations, request.costing)


@app.get("/route/{route_id}")
@app.get("/api/route/{route_id}")
def route_by_id(route_id: str):
    return get_stored_route(route_id)
