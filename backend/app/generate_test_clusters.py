
import asyncio
import json
import random
import uuid

from pathlib import Path

from app.pathing import (
    defineClusters,
    location_cluster,
    MAXIMUM_NODE_SIZE,
    MAXIMUM_DESTINATION_TIME,
)

from app.dummy_valhalla import location_node
from app.dummy_ticket_db import delivery_ticket


# ==========================================
# CONFIGURATION
# ==========================================

NUMBER_OF_PAIRS = 8

# Salt Lake City coordinate boundaries
LAT_MIN = 40.70
LAT_MAX = 40.82

LON_MIN = -111.95
LON_MAX = -111.82

RANDOM_SEED = 42

OUTPUT_FILE = Path(__file__).with_name("cluster_results.json")


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
# TICKET GENERATION
# ==========================================

def generate_ticket_pool(
    number_of_pairs: int
) -> list[delivery_ticket]:

    tickets = []

    for _ in range(number_of_pairs):

        ticket_id = str(uuid.uuid4())

        pickup_location = generate_location()
        dropoff_location = generate_location()

        node = location_node(
            node_type="pickup",
            stop_id=ticket_id,
            origin=pickup_location,
            destination=dropoff_location,
        )

        ticket = delivery_ticket(
            ticket_id=ticket_id,
            locations=node,
            pickup_time=0.0,
        )

        tickets.append(ticket)

    return tickets


# ==========================================
# CLUSTER OUTPUT
# ==========================================

def print_clusters(
    clusters: list[location_cluster]
):

    print("\n" + "=" * 60)
    print("HITCH ROUTE CLUSTER RESULTS")
    print("=" * 60)

    print(f"Total clusters: {len(clusters)}")

    total_stops = 0
    total_orders = 0

    for index, cluster in enumerate(clusters, start=1):

        print(f"\nCLUSTER {index}")
        print("-" * 40)

        print(f"Type: {cluster.cluster_type}")
        print(f"Stops: {len(cluster.locations)}")

        order_ids = {
            stop.order_id
            for stop in cluster.locations
        }

        print(f"Orders: {len(order_ids)}")

        print(
            f"Total route time: "
            f"{cluster.total_time:.2f} seconds"
        )

        print(
            f"Maximum time: "
            f"{cluster.maximum_size:.2f} seconds"
        )

        print(
            f"Within time limit: "
            f"{cluster.total_time <= cluster.maximum_size}"
        )

        total_stops += len(cluster.locations)
        total_orders += len(order_ids)

        for stop in cluster.locations:

            stop_type = (
                "PICKUP"
                if stop.is_pickup
                else "DELIVERY"
            )

            print(f"\n  Order ID: {stop.order_id}")
            print(f"  Stop type: {stop_type}")
            print(f"  Location: {stop.location}")

    print("\n" + "=" * 60)

    print(f"Total stops: {total_stops}")
    print(f"Total clustered orders: {total_orders}")

    print("=" * 60)


# ==========================================
# EXPORT
# ==========================================

def export_clusters(
    clusters: list[location_cluster]
):

    output = []

    for cluster in clusters:

        output.append({
            "cluster_type": cluster.cluster_type,
            "total_time": cluster.total_time,
            "maximum_size": cluster.maximum_size,

            "locations": [
                {
                    "order_id": stop.order_id,
                    "is_pickup": stop.is_pickup,
                    "location": stop.location,
                }
                for stop in cluster.locations
            ],
        })

    OUTPUT_FILE.write_text(
        json.dumps(output, indent=4)
    )

    print(f"\nResults exported to {OUTPUT_FILE}")


# ==========================================
# MAIN
# ==========================================

async def main():

    random.seed(RANDOM_SEED)

    print("Generating dummy delivery tickets...")

    tickets = generate_ticket_pool(NUMBER_OF_PAIRS)

    print(f"Generated {len(tickets)} tickets.")

    print("\nRunning Hitch clustering algorithm...")
    print("Using multi-location Valhalla routing.")

    clusters = await defineClusters(
        route_pool=tickets,
        maximum_size=MAXIMUM_NODE_SIZE,
        maximum_destination_distance=MAXIMUM_DESTINATION_TIME,
    )

    print_clusters(clusters)

    export_clusters(clusters)


# ==========================================
# ENTRY POINT
# ==========================================

if __name__ == "__main__":
    asyncio.run(main())
