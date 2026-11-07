import asyncio

from app.dummy_valhalla import generate_route
from app.dummy_ticket_db import (
    delivery_ticket,
    get_all_tickets,
)
from app.models import (  # noqa: F401  (re-exported for dispatcher.py)
    MAXIMUM_DESTINATION_TIME,
    MAXIMUM_NODE_SIZE,
    location_cluster,
    location_node,
    route_stop,
)


# ==========================================
# ROUTE CALCULATION CACHE
# ==========================================

_route_cache: dict[tuple, float] = {}


def location_key(location: dict) -> tuple:
    return (
        round(float(location["lat"]), 6),
        round(float(location["lon"]), 6)
    )


async def get_route_time(locations: list[dict]) -> float:
    """
    Calculate an entire route in one Valhalla request.
    Returns minutes.
    """

    if len(locations) < 2:
        return 0.0

    key = tuple(location_key(loc) for loc in locations)

    if key not in _route_cache:
        result = await generate_route(locations)
        _route_cache[key] = float(result["route_time"])

    return _route_cache[key]


async def destination_distance(a: dict, b: dict) -> float:
    """Get Valhalla travel time between destinations in minutes."""

    return await get_route_time([a, b])


async def calculate_route_time(locations: list[dict]) -> float:
    """Calculate total journey time in seconds."""

    return await get_route_time(locations) * 60.0


async def get_destination_distances(
    destinations: list[dict],
    target: dict
) -> list[float]:
    """Calculate destination distances concurrently."""

    return await asyncio.gather(*[
        destination_distance(destination, target)
        for destination in destinations
    ])


# ==========================================
# CLUSTER GENERATION
# ==========================================

async def defineClusters(
    route_pool: list[delivery_ticket],
    maximum_size: float = MAXIMUM_NODE_SIZE,
    maximum_destination_distance: float = MAXIMUM_DESTINATION_TIME,
) -> list[location_cluster]:

    clusters: list[location_cluster] = []

    if maximum_size <= 0 or maximum_destination_distance < 0:
        raise ValueError("Invalid cluster limits")

    for ticket in route_pool:

        node = ticket.locations

        if node.node_type not in ("pickup", "dropoff"):
            continue

        if node.origin is None or node.destination is None:
            continue

        # Create pickup and delivery stops.
        pickup_stop = route_stop(
            location=node.origin,
            order_id=ticket.ticket_id,
            is_pickup=True
        )

        delivery_stop = route_stop(
            location=node.destination,
            order_id=ticket.ticket_id,
            is_pickup=False
        )

        # Find compatible clusters.
        candidate_clusters = [
            cluster for cluster in clusters
            if cluster.cluster_type == node.node_type
        ]

        async def check_cluster(cluster):

            existing_destinations = [
                stop.location
                for stop in cluster.locations
                if not stop.is_pickup
            ]

            if not existing_destinations:
                return None

            distances = await get_destination_distances(
                existing_destinations,
                node.destination
            )

            if all(
                distance <= maximum_destination_distance
                for distance in distances
            ):
                return cluster, max(distances)

            return None

        results = await asyncio.gather(*[
            check_cluster(cluster)
            for cluster in candidate_clusters
        ])

        matching_clusters = [
            result for result in results
            if result is not None
        ]

        matching_clusters.sort(key=lambda item: item[1])

        added = False

        # Try adding to existing clusters.
        for cluster, _destination_score in matching_clusters:

            proposed_stops = (
                cluster.locations +
                [pickup_stop, delivery_stop]
            )

            proposed_route = [
                stop.location
                for stop in proposed_stops
            ]

            proposed_time = await calculate_route_time(
                proposed_route
            )

            if proposed_time <= maximum_size:

                cluster.locations.extend([
                    pickup_stop,
                    delivery_stop
                ])

                cluster.total_time = proposed_time

                added = True
                break

        # Create a new cluster if necessary.
        if not added:

            initial_time = await calculate_route_time([
                node.origin,
                node.destination
            ])

            clusters.append(
                location_cluster(
                    cluster_type=node.node_type,
                    locations=[
                        pickup_stop,
                        delivery_stop
                    ],
                    total_time=initial_time,
                    maximum_size=maximum_size
                )
            )

    return clusters


# ==========================================
# ROUTE GENERATION FROM CLUSTERS
# ==========================================

async def routes_from_clusters(
    courier: location_node,
    cluster_pool: list[location_cluster]
) -> list[route_stop]:

    if courier.origin is None or courier.destination is None:
        raise ValueError(
            "Courier must have an origin and destination"
        )

    # Extract all route stops.
    stops: list[route_stop] = []

    for cluster in cluster_pool:
        stops.extend(cluster.locations)

    if not stops:
        return []

    # Remove duplicate stops by order and pickup type.
    unique_stops = {}
    for stop in stops:
        key = (stop.order_id, stop.is_pickup)
        unique_stops[key] = stop

    stops = list(unique_stops.values())

    # Validate that every order has exactly one pickup
    # and one delivery.
    order_pickups = {
        stop.order_id
        for stop in stops
        if stop.is_pickup
    }

    order_deliveries = {
        stop.order_id
        for stop in stops
        if not stop.is_pickup
    }

    if order_pickups != order_deliveries:
        raise ValueError(
            "Every order must have exactly one pickup "
            "and one delivery."
        )

    # Order stops with pickup precedence.
    remaining = stops.copy()
    ordered_stops: list[route_stop] = []

    current_location = courier.origin
    picked_up: set[str] = set()

    while remaining:

        eligible_stops = [
            stop
            for stop in remaining
            if stop.is_pickup or stop.order_id in picked_up
        ]

        if not eligible_stops:
            raise ValueError(
                "Unable to order route: delivery has no preceding pickup"
            )

        async def evaluate_candidate(candidate):

            travel_time = await get_route_time([
                current_location,
                candidate.location
            ])

            onward_time = await get_route_time([
                candidate.location,
                courier.destination
            ])

            return travel_time + onward_time

        scores = await asyncio.gather(*[
            evaluate_candidate(candidate)
            for candidate in eligible_stops
        ])

        best_index = min(
            range(len(scores)),
            key=lambda index: scores[index]
        )

        best_stop = eligible_stops[best_index]

        ordered_stops.append(best_stop)
        remaining.remove(best_stop)

        if best_stop.is_pickup:
            picked_up.add(best_stop.order_id)

        current_location = best_stop.location

    # Validate the completed route.
    complete_route = [
        courier.origin,
        *[stop.location for stop in ordered_stops],
        courier.destination
    ]

    final_route = await generate_route(complete_route)

    print(
        f"Generated courier route: "
        f"{len(ordered_stops)} stops, "
        f"{final_route['route_time']} minutes"
    )

    return ordered_stops


# ==========================================
# DATABASE INTEGRATION
# ==========================================

async def defineClustersFromDatabase() -> list[location_cluster]:
    """Retrieve all tickets and generate clusters."""

    tickets = get_all_tickets()

    return await defineClusters(tickets)


# ==========================================
# CACHE MANAGEMENT
# ==========================================

def clear_route_cache():
    """Clear cached Valhalla calculations."""

    _route_cache.clear()


def get_route_cache_stats() -> dict:
    """Get route cache statistics."""

    return {
        "cached_routes": len(_route_cache)
    }
