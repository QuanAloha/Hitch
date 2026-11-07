import os

import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.models import RouteRequest
from app.dummy_valhalla import (
    generate_route,
    get_stored_route,
    route_to_response,
)
from app.database import initialize_database
from app.frontend_routes import router as frontend_router

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


app.include_router(frontend_router)


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
    return route_to_response(get_stored_route(route_id))
