from dataclasses import dataclass, field

from dummy_valhalla import (
    location_node,
    generate_route,
    retrieve_route,
)

MAXIMUM_NODE_SIZE = 3600  # Seconds (1 hour)
MAXIMUM_DESTINATION_TIME = 15.0  # Minutes


@dataclass
class location_cluster:
    cluster_type: str
    locations: list[location_node] = field(default_factory=list)
    total_time: float = 0.0  # Seconds
    maximum_size: float = MAXIMUM_NODE_SIZE


async def destination_distance(a: dict, b: dict) -> float:
    """
    Get the real Valhalla travel time between two destinations.
    Returns minutes.
    """
    result = await generate_route([a, b])
    return float(result["route_time"])


async def calculate_route_time(locations: list[dict]) -> float:
    """
    Calculate total journey time across consecutive locations.
    Returns seconds, matching MAXIMUM_NODE_SIZE.
    """
    if len(locations) < 2:
        return 0.0

    total_time_seconds = 0.0

    for index in range(len(locations) - 1):
        result = await generate_route([locations[index], locations[index + 1]])
        total_time_seconds += float(result["route_time"]) * 60.0

    return total_time_seconds


async def defineClusters(
    route_pool: list[location_node],
    maximum_size: float = MAXIMUM_NODE_SIZE,
    maximum_destination_distance: float = MAXIMUM_DESTINATION_TIME,
) -> list[location_cluster]:
    clusters: list[location_cluster] = []

    if maximum_size <= 0 or maximum_destination_distance < 0:
        raise ValueError("Invalid cluster limits")

    for node in route_pool:
        if node.node_type not in ("pickup", "dropoff"):
            continue

        if node.origin is None or node.destination is None:
            continue

        matching_clusters = []

        for cluster in clusters:
            if cluster.cluster_type != node.node_type or not cluster.locations:
                continue

            distances = [
                await destination_distance(existing.destination, node.destination)
                for existing in cluster.locations
            ]

            if all(distance <= maximum_destination_distance for distance in distances):
                matching_clusters.append((cluster, max(distances, default=0.0)))

        matching_clusters.sort(key=lambda item: item[1])

        added = False

        for cluster, _destination_score in matching_clusters:
            proposed_nodes = cluster.locations + [node]
            proposed_route = []

            for existing in proposed_nodes:
                proposed_route.extend([existing.origin, existing.destination])

            proposed_time = await calculate_route_time(proposed_route)

            if proposed_time <= maximum_size:
                cluster.locations.append(node)
                cluster.total_time = proposed_time
                added = True
                break

        if not added:
            # Calculate the first node's actual origin-to-destination time.
            initial_time = await calculate_route_time([node.origin, node.destination])
            clusters.append(
                location_cluster(
                    cluster_type=node.node_type,
                    locations=[node],
                    total_time=initial_time,
                    maximum_size=maximum_size,
                )
            )

    return clusters
