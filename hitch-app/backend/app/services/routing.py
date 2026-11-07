from collections import OrderedDict

from app.models import Parcel
from app.services.valhalla import ValhallaClient

from app.config import settings


class RoutingError(Exception):
    pass


def group_stops(parcels: list[Parcel]) -> list[dict]:
    """Parcels going to the same coordinates share one stop."""
    groups: "OrderedDict[tuple[float, float], dict]" = OrderedDict()
    for p in parcels:
        key = (round(p.dropoff_lat, 5), round(p.dropoff_lon, 5))
        g = groups.setdefault(
            key,
            {"address": p.dropoff_address, "lat": p.dropoff_lat, "lon": p.dropoff_lon, "parcel_ids": []},
        )
        g["parcel_ids"].append(str(p.id))
    return list(groups.values())


def order_stops(cost: list[list[float]]) -> list[int]:
    """Visit order for an open path starting at node 0 (the pickup).

    Nearest-neighbour construction followed by 2-opt improvement. Returns node
    indices excluding node 0.
    """
    n = len(cost)
    path = [0]
    remaining = set(range(1, n))
    while remaining:
        nxt = min(remaining, key=lambda j: cost[path[-1]][j])
        path.append(nxt)
        remaining.remove(nxt)

    def length(p: list[int]) -> float:
        return sum(cost[a][b] for a, b in zip(p, p[1:]))

    best = length(path)
    improved = True
    while improved:
        improved = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                cand = path[:i] + path[i : j + 1][::-1] + path[j + 1 :]
                cand_len = length(cand)
                if cand_len < best - 1e-9:
                    path, best, improved = cand, cand_len, True
    return path[1:]


async def plan_route(
    valhalla: ValhallaClient,
    start: tuple[float, float],
    pickup: tuple[float, float],
    parcels: list[Parcel],
) -> dict:
    """carrier start -> shipment pickup -> every dropoff in an efficient order."""
    stops = group_stops(parcels)
    if not stops:
        raise RoutingError("Shipment has no parcels")
    if len(stops) > settings.max_stops_per_shipment:
        raise RoutingError(f"Too many stops (max {settings.max_stops_per_shipment})")

    if len(stops) > 1:
        points = [pickup] + [(s["lat"], s["lon"]) for s in stops]
        matrix = await valhalla.time_matrix(points)
        if any(cell is None for row in matrix for cell in row):
            raise RoutingError("One or more stops are not reachable by road from each other")
        order = order_stops(matrix)
        stops = [stops[i - 1] for i in order]

    for seq, s in enumerate(stops, start=1):
        s["sequence"] = seq

    result = await valhalla.route([start, pickup] + [(s["lat"], s["lon"]) for s in stops])
    return {
        "distance_km": result["distance_km"],
        "duration_s": result["duration_s"],
        "legs": result["legs"],  # legs[0] = start->pickup, legs[i] = to stop i
        "stops": stops,
    }
