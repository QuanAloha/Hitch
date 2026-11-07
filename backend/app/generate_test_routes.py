
import asyncio
import random
import uuid
import json

from pathing import (
    defineClusters,
    location_cluster,
    MAXIMUM_NODE_SIZE,
    MAXIMUM_DESTINATION_TIME
)

from dummy_valhalla import location_node


# ==========================================
# CONFIGURATION
# ==========================================

NUMBER_OF_PAIRS = 100

# Salt Lake City coordinate boundaries
LAT_MIN = 40.70
LAT_MAX = 40.82

LON_MIN = -111.95
LON_MAX = -111.82

RANDOM_SEED = 42


# ==========================================
# LOCATION GENERATION
# ==========================================

def generate_location() -> dict:
    """
    Generate a random coordinate around Salt Lake City.
    """

    return {
        "lat": round(random.uniform(LAT_MIN, LAT_MAX), 6),
        "lon": round(random.uniform(LON_MIN, LON_MAX), 6)
    }


# ==========================================
# PICKUP / DELIVERY GENERATION
# ==========================================

def generate_route_pool(
    number_of_pairs: int
) -> list[location_node]:

    route_pool = []

    for i in range(number_of_pairs):

        stop_id = str(uuid.uuid4())

        pickup_location = generate_location()
        dropoff_location = generate_location()

        # Pickup node
        pickup = location_node(
            node_type="pickup",
            stop_id=f"{stop_id}-P",
            origin=pickup_location,
            destination=dropoff_location
        )

        # Dropoff node
        dropoff = location_node(
            node_type="dropoff",
            stop_id=f"{stop_id}-D",
            origin=pickup_location,
            destination=dropoff_location
        )

        route_pool.append(pickup)
        route_pool.append(dropoff)

    return route_pool


# ==========================================
# OUTPUT
# ==========================================

def print_clusters(clusters: list[location_cluster]):

    print("\n" + "=" * 60)
    print("HITCH ROUTE CLUSTER RESULTS")
    print("=" * 60)

    print(f"Total clusters: {len(clusters)}")

    for index, cluster in enumerate(clusters, start=1):

        print(f"\nCLUSTER {index}")
        print("-" * 40)

        print(f"Type: {cluster.cluster_type}")
        print(f"Locations: {len(cluster.locations)}")
        print(f"Total route time: {cluster.total_time:.2f} seconds")
        print(f"Maximum time: {cluster.maximum_size:.2f} seconds")

        for node in cluster.locations:

            print(f"\n  Stop ID: {node.stop_id}")
            print(f"  Type: {node.node_type}")
            print(f"  Origin: {node.origin}")
            print(f"  Destination: {node.destination}")

        print(
            f"\n  Within time limit: "
            f"{cluster.total_time <= cluster.maximum_size}"
        )

    print("\n" + "=" * 60)


# ==========================================
# MAIN
# ==========================================

async def main():

    random.seed(RANDOM_SEED)

    print("Generating dummy pickup/delivery pairs...")

    route_pool = generate_route_pool(NUMBER_OF_PAIRS)

    print(f"Generated {NUMBER_OF_PAIRS} pairs.")
    print(f"Generated {len(route_pool)} location nodes.")

    print("\nRunning Hitch clustering algorithm...")

    clusters = await defineClusters(
        route_pool=route_pool,
        maximum_size=MAXIMUM_NODE_SIZE,
        maximum_destination_distance=MAXIMUM_DESTINATION_TIME
    )

    print_clusters(clusters)

    # Export results for inspection.
    output = []

    for cluster in clusters:

        output.append({
            "cluster_type": cluster.cluster_type,
            "total_time": cluster.total_time,
            "maximum_size": cluster.maximum_size,
            "locations": [
                {
                    "stop_id": node.stop_id,
                    "node_type": node.node_type,
                    "origin": node.origin,
                    "destination": node.destination
                }
                for node in cluster.locations
            ]
        })

    with open("../../cluster_results.json", "w") as file:
        json.dump(output, file, indent=4)

    print("\nResults exported to cluster_results.json")


if __name__ == "__main__":
    asyncio.run(main())
