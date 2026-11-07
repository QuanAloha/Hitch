import os

import httpx
import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://hitch:hitch_dev_password@localhost:5432/hitch"
)
VALHALLA_URL = os.getenv("VALHALLA_URL", "http://localhost:8002").rstrip("/")

app = FastAPI(title="Hitch API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/route")
async def get_route(request: RouteRequest):
    payload = {
        "locations": request.locations,
        "costing": request.costing,
        "units": "miles",
        "directions_type": "instructions",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{VALHALLA_URL}/route",
                json=payload,
            )

        if response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "Valhalla routing failed",
                    "valhalla_error": response.text,
                },
            )

        return response.json()

    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="Valhalla routing request timed out",
        )

    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail="Could not connect to Valhalla",
        )


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


@app.post("/api/route")
async def route(request: RouteRequest):
    payload = {"locations": request.locations, "costing": request.costing}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{VALHALLA_URL}/route", json=payload)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Valhalla routing request failed") from exc
