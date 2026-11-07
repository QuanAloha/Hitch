"""Road-following route geometry from the Valhalla container.

Valhalla returns each leg's shape as a precision-6 encoded polyline. This module
asks Valhalla for a route through the given stops and returns it as a GeoJSON
LineString ([lon, lat] order) for the frontend map.
"""

import os
from typing import Any, Optional

import httpx

VALHALLA_URL = os.getenv("VALHALLA_URL", "http://localhost:8002").rstrip("/")
MAX_LOCATIONS = 25


class ValhallaUnavailable(Exception):
    """Valhalla could not be reached or could not route these stops."""


def decode_polyline(encoded: str, precision: int = 6) -> list[list[float]]:
    """Decode a Valhalla/Google encoded polyline into [lon, lat] pairs."""
    factor = 10 ** precision
    index = lat = lon = 0
    coordinates: list[list[float]] = []
    while index < len(encoded):
        for axis in range(2):
            result = shift = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lon += delta
        coordinates.append([lon / factor, lat / factor])
    return coordinates


def fetch_route_geometry(
    locations: list[dict[str, Any]], costing: str = "auto"
) -> dict[str, Any]:
    """Route through `locations` in order and return geometry + totals.

    Raises ValhallaUnavailable if the container is down or cannot route the stops.
    """
    if not 2 <= len(locations) <= MAX_LOCATIONS:
        raise ValueError(f"Provide between 2 and {MAX_LOCATIONS} locations")

    payload = {
        "locations": [
            {"lat": float(p["lat"]), "lon": float(p["lon"]), "type": "break"} for p in locations
        ],
        "costing": costing,
        "units": "miles",
    }
    try:
        response = httpx.post(f"{VALHALLA_URL}/route", json=payload, timeout=15.0)
        response.raise_for_status()
        trip = response.json()["trip"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise ValhallaUnavailable(f"Valhalla routing failed: {exc}") from exc

    coordinates: list[list[float]] = []
    for leg in trip.get("legs", []):
        points = decode_polyline(leg["shape"])
        # Each leg starts where the previous one ended; drop the duplicate joint.
        coordinates.extend(points[1:] if coordinates else points)
    if len(coordinates) < 2:
        raise ValhallaUnavailable("Valhalla returned no route shape")

    summary = trip.get("summary", {})
    return {
        "geometry": {"type": "LineString", "coordinates": coordinates, "source": "valhalla"},
        "time_minutes": round(float(summary.get("time", 0)) / 60.0, 1),
        "distance_miles": round(float(summary.get("length", 0)), 1),
    }


def stops_to_locations(stops: list[dict[str, Any]]) -> Optional[list[dict[str, Any]]]:
    locations = [s for s in stops if s.get("lat") is not None and s.get("lon") is not None]
    return locations if len(locations) >= 2 else None
