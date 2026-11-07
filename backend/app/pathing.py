from app.dummy_valhalla import generate_route
from app.dummy_ticket_db import (
    delivery_ticket,
    get_all_tickets,
)
from app.models import (
    MAXIMUM_DESTINATION_TIME,
    MAXIMUM_NODE_SIZE,
    location_cluster,
    location_node,
    route_stop,
)


async def destination_distance(a: dict, b: dict) -> float:
    """
    Get Valhalla travel time between two destinations.
    Returns minutes.
    """

    result = await generate_route([a, b])

    return float(result["route_time"])


async def calculate_route_time(locations: list[dict]) -> float:
    """
    Calculate total journey time across consecutive locations.
    Returns seconds.
    """

    if len(locations) < 2:
        return 0.0

    total_time_seconds = 0.0

    for index in range(len(locations) - 1):

        result = await generate_route([
            locations[index],
            locations[index + 1]
        ])

        total_time_seconds += float(result["route_time"]) * 60.0

    return total_time_seconds


async def defineClusters(
    route_pool: list[delivery_ticket],
    maximum_size: float = MAXIMUM_NODE_SIZE,
    maximum_destination_distance: float = MAXIMUM_DESTINATION_TIME,
) -> list[location_cluster]:

    clusters: list[location_cluster] = []

    if maximum_size <= 0 or maximum_destination_distance < 0:
        raise ValueError("Invalid cluster limits")

    for ticket in route_pool:

        # Extract location_node from delivery_ticket.
        node = ticket.locations

        if node.node_type not in ("pickup", "dropoff"):
            continue

        if node.origin is None or node.destination is None:
            continue

        matching_clusters = []

        for cluster in clusters:

            if cluster.cluster_type != node.node_type:
                continue

            if not cluster.locations:
                continue

            distances = [
                await destination_distance(
                    existing.destination,
                    node.destination
                )
                for existing in cluster.locations
            ]

            if all(
                distance <= maximum_destination_distance
                for distance in distances
            ):
                matching_clusters.append(
                    (cluster, max(distances, default=0.0))
                )

        matching_clusters.sort(key=lambda item: item[1])

        added = False

        for cluster, _destination_score in matching_clusters:

            proposed_nodes = cluster.locations + [node]

            proposed_route = []

            for existing in proposed_nodes:
                proposed_route.extend([
                    existing.origin,
                    existing.destination
                ])

            proposed_time = await calculate_route_time(
                proposed_route
            )

            if proposed_time <= maximum_size:

                cluster.locations.append(node)
                cluster.ticket_ids.append(ticket.ticket_id)
                cluster.total_time = proposed_time

                added = True
                break

        if not added:

            initial_time = await calculate_route_time([
                node.origin,
                node.destination
            ])

            clusters.append(
                location_cluster(
                    cluster_type=node.node_type,
                    locations=[node],
                    ticket_ids=[ticket.ticket_id],
                    total_time=initial_time,
                    maximum_size=maximum_size,
                )
            )

    return clusters


async def routes_from_clusters(
    courier: location_node,
    cluster_pool: list[location_cluster]
) -> list[route_stop]:

    if courier.origin is None or courier.destination is None:
        raise ValueError("Courier must have an origin and destination")

    # ------------------------------------------
    # 1. EXTRACT ALL ROUTE STOPS
    # ------------------------------------------

    stops: list[route_stop] = []

    for cluster in cluster_pool:

        if len(cluster.locations) != len(cluster.ticket_ids):
            raise ValueError(
                "Cluster locations and ticket IDs are mismatched"
            )

        for node, order_id in zip(
            cluster.locations,
            cluster.ticket_ids
        ):

            if node.origin is not None:
                stops.append(
                    route_stop(
                        location=node.origin,
                        order_id=order_id
                    )
                )

            if node.destination is not None:
                stops.append(
                    route_stop(
                        location=node.destination,
                        order_id=order_id
                    )
                )

    if not stops:
        return []

    # ------------------------------------------
    # 2. ROUTE TIME HELPER
    # ------------------------------------------

    route_cache: dict[tuple, float] = {}

    async def get_travel_time(locations: list[dict]) -> float:

        key = tuple(
            (loc["lat"], loc["lon"])
            for loc in locations
        )

        if key not in route_cache:

            result = await generate_route(locations)

            # Dummy Valhalla returns minutes.
            route_cache[key] = float(result["route_time"])

        return route_cache[key]

    # ------------------------------------------
    # 3. FIND THE BEST FIRST STOP
    # ------------------------------------------

    remaining = stops.copy()
    ordered_stops: list[route_stop] = []

    current_location = courier.origin

    while remaining:

        best_stop = None
        best_score = float("inf")

        for candidate in remaining:

            # Calculate the route:
            # Current location -> candidate -> courier destination.
            #
            # This encourages the algorithm to select stops
            # that are both accessible and directionally useful.

            score = await get_travel_time([
                current_location,
                candidate.location,
                courier.destination
            ])

            if score < best_score:
                best_score = score
                best_stop = candidate

        # Add the best candidate.
        ordered_stops.append(best_stop)

        remaining.remove(best_stop)

        current_location = best_stop.location

    # ------------------------------------------
    # 4. VALIDATE THE COMPLETED ROUTE
    # ------------------------------------------

    complete_route = [
        courier.origin
    ]

    complete_route.extend(
        stop.location for stop in ordered_stops
    )

    complete_route.append(
        courier.destination
    )

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
    """
    Retrieve all existing delivery tickets and cluster them.
    """

    tickets = get_all_tickets()

    return await defineClusters(tickets)
