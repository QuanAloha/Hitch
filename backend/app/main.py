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


class RouteRequest(BaseModel):
    locations: list[dict] = Field(
        min_length=2,
        examples=[
            [
                {"lat": 40.7608, "lon": -111.891},
                {"lat": 40.2338, "lon": -111.6585},
            ]
        ],
    )
    costing: str = "auto"


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
