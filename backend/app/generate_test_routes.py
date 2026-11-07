
import asyncio
import json
import random
import uuid
from pathlib import Path

from dummy_ticket_db  import (
    create_ticket,
    get_all_tickets,
    clear_database,
)

from dummy_valhalla import location_node

from pathing import (
    defineClusters,
    routes_from_clusters,
    location_cluster,
    route_stop,
    MAXIMUM_NODE_SIZE,
    MAXIMUM_DESTINATION_TIME,
)


# ==========================================
# CONFIGURATION
# ==========================================

NUMBER_OF_TICKETS = 10

# Salt Lake City metro coordinates
LAT_MIN = 40.70
LAT_MAX = 40.82

LON_MIN = -111.95
LON_MAX = -111.82

RANDOM_SEED = 42

OUTPUT_FILE = Path(__file__).with_name("route_results.json")


# ==========================================
# LOCATION GENERATION
# ==========================================

def generate_location() -> dict:
    """Generate a random coordinate around Salt Lake City."""

    return {
        "lat": round(random.uniform(LAT_MIN, LAT_MAX), 6),
        "lon": round(random.uniform(LON_MIN, LON_MAX), 6),
    }


# ==========================================
# COURIER GENERATION
# ==========================================

def generate_courier() -> location_node:
    """Generate a dummy courier with an origin and destination."""

    return location_node(
        node_type="courier",
        stop_id=f"COURIER-{uuid.uuid4()}",
        origin=generate_location(),
        destination=generate_location(),
    )


# ==========================================
# OUTPUT HELPERS
# ==========================================

def print_clusters(clusters: list[location_cluster]):

    print("\n" + "=" * 60)
    print("HITCH CLUSTER RESULTS")
    print("=" * 60)

    print(f"Total clusters: {len(clusters)}")

    for index, cluster in enumerate(clusters, 1):

        print(f"\nCLUSTER {index}")
        print("-" * 40)

        print(f"Type: {cluster.cluster_type}")
        print(f"Tickets: {len(cluster.ticket_ids)}")
        print(f"Total route time: {cluster.total_time:.2f} seconds")

        for node, ticket_id in zip(
            cluster.locations,
            cluster.ticket_ids
        ):
            print(f"\n  Ticket ID: {ticket_id}")
            print(f"  Origin: {node.origin}")
            print(f"  Destination: {node.destination}")

        print(
            f"\n  Within time limit: "
            f"{cluster.total_time <= cluster.maximum_size}"
        )

    print("\n" + "=" * 60)


def print_route(
    route: list[route_stop],
    courier: location_node,
    total_time: float
):

    print("\n" + "=" * 60)
    print("HITCH COURIER ROUTE")
    print("=" * 60)

    print(f"Courier ID: {courier.stop_id}")
    print(f"Starting location: {courier.origin}")
    print(f"Final destination: {courier.destination}")

    print(f"\nTotal stops: {len(route)}")
    print(f"Total route time: {total_time:.2f} minutes")

    for index, stop in enumerate(route, 1):

        print(f"\nSTOP {index}")
        print("-" * 40)

        print(f"Order ID: {stop.order_id}")
        print(f"Location: {stop.location}")

    print("\n" + "=" * 60)


# ==========================================
# JSON SERIALIZATION
# ==========================================

def serialize_cluster(cluster: location_cluster) -> dict:

    return {
        "cluster_type": cluster.cluster_type,
        "total_time": cluster.total_time,
        "maximum_size": cluster.maximum_size,
        "ticket_ids": cluster.ticket_ids,
        "locations": [
            {
                "stop_id": node.stop_id,
                "node_type": node.node_type,
                "origin": node.origin,
                "destination": node.destination,
            }
            for node in cluster.locations
        ],
    }


def serialize_route(
    route: list[route_stop],
    courier: location_node,
    total_time: float,
) -> dict:

    return {
        "courier": {
            "courier_id": courier.stop_id,
            "origin": courier.origin,
            "destination": courier.destination,
        },
        "total_time_minutes": total_time,
        "total_stops": len(route),
        "route": [
            {
                "location": stop.location,
                "order_id": stop.order_id,
            }
            for stop in route
        ],
    }


# ==========================================
# MAIN
# ==========================================

async def main():

    random.seed(RANDOM_SEED)

    # --------------------------------------
    # 1. GENERATE DUMMY DATABASE TICKETS
    # --------------------------------------

    print("Generating dummy delivery tickets...")

    clear_database()

    for _ in range(NUMBER_OF_TICKETS):
        create_ticket()

    tickets = get_all_tickets()

    print(f"Generated {len(tickets)} delivery tickets.")

    # --------------------------------------
    # 2. GENERATE COURIER
    # --------------------------------------

    print("\nGenerating dummy courier...")

    courier = generate_courier()

    print(f"Courier ID: {courier.stop_id}")
    print(f"Origin: {courier.origin}")
    print(f"Destination: {courier.destination}")

    # --------------------------------------
    # 3. GENERATE CLUSTERS
    # --------------------------------------

    print("\nRunning Hitch clustering algorithm...")

    clusters = await defineClusters(
        route_pool=tickets,
        maximum_size=MAXIMUM_NODE_SIZE,
        maximum_destination_distance=MAXIMUM_DESTINATION_TIME,
    )

    print_clusters(clusters)

    # --------------------------------------
    # 4. GENERATE COURIER ROUTE
    # --------------------------------------

    print("\nGenerating courier route using Valhalla...")

    route = await routes_from_clusters(
        courier=courier,
        cluster_pool=clusters,
    )

    # --------------------------------------
    # 5. CALCULATE FINAL ROUTE TIME
    # --------------------------------------

    complete_locations = [
        courier.origin,
        *[stop.location for stop in route],
        courier.destination,
    ]

    result = await generate_route(complete_locations)

    total_time = float(result["route_time"])

    print_route(
        route=route,
        courier=courier,
        total_time=total_time,
    )

    # --------------------------------------
    # 6. EXPORT RESULTS
    # --------------------------------------

    output = {
        "configuration": {
            "number_of_tickets": NUMBER_OF_TICKETS,
            "maximum_node_size": MAXIMUM_NODE_SIZE,
            "maximum_destination_time": MAXIMUM_DESTINATION_TIME,
            "random_seed": RANDOM_SEED,
        },

        "clusters": [
            serialize_cluster(cluster)
            for cluster in clusters
        ],

        "courier_route": serialize_route(
            route,
            courier,
            total_time,
        ),
    }

    OUTPUT_FILE.write_text(
        json.dumps(output, indent=4)
    )

    print(f"\nResults exported to {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
