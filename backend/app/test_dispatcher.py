
import asyncio
import json
import random
import uuid

from pathlib import Path
from datetime import datetime, timezone

from app.dummy_ticket_db import (
    create_ticket,
    get_all_tickets,
    clear_database,
)

from app.dummy_available_drivers import (
    get_all_driver_ids,
    generate_driver,
    clear_drivers,
)

from app.pathing import (
    defineClusters,
    MAXIMUM_NODE_SIZE,
    MAXIMUM_DESTINATION_TIME,
)

from app.dispatcher import (
    generate_dispatches,
)

from app.dummy_valhalla import (
    generate_route,
    location_node,
)


# ==========================================
# CONFIGURATION
# ==========================================

NUMBER_OF_TICKETS = 5
NUMBER_OF_DRIVERS = 2

RANDOM_SEED = 42

OUTPUT_FILE = Path(__file__).with_name(
    "dispatch_test_results.json"
)

# Salt Lake City coordinate boundaries.
LAT_MIN = 40.70
LAT_MAX = 40.82

LON_MIN = -111.95
LON_MAX = -111.82


# ==========================================
# LOCATION GENERATION
# ==========================================

def generate_location() -> dict:

    return {
        "lat": round(random.uniform(LAT_MIN, LAT_MAX), 6),
        "lon": round(random.uniform(LON_MIN, LON_MAX), 6),
    }


# ==========================================
# DRIVER GENERATION
# ==========================================

def generate_test_drivers(number_of_drivers: int):

    """
    Generate drivers with shifts on November 1, 2026.

    Drivers receive different shift windows so that
    dispatch eligibility can be tested.
    """

    base_date = datetime(
        2026, 11, 1, 6, 0,
        tzinfo=timezone.utc
    ).timestamp()

    shift_duration = 4 * 60 * 60

    for index in range(number_of_drivers):

        shift_start = base_date + (
            index % 4
        ) * 3 * 60 * 60

        shift_end = shift_start + shift_duration

        driver_id = f"TEST-DRIVER-{index + 1:03d}"

        driver_location = location_node(
            node_type="driver",
            stop_id=driver_id,
            origin=generate_location(),
            destination=generate_location(),
        )

        generate_driver(
            driver_id=driver_id,
            location_node=driver_location,
            shift_start=shift_start,
            shift_end=shift_end,
        )


# ==========================================
# DATABASE GENERATION
# ==========================================

def generate_test_tickets(number_of_tickets: int):

    print("\nGenerating dummy delivery tickets...")

    for _ in range(number_of_tickets):
        create_ticket()

    tickets = get_all_tickets()

    print(f"Generated {len(tickets)} tickets.")

    return tickets


# ==========================================
# VALIDATION
# ==========================================

async def validate_dispatches(dispatches, tickets):

    print("\nValidating dispatches...")

    assigned_ids = []

    ticket_lookup = {
        ticket.ticket_id: ticket
        for ticket in tickets
    }

    for dispatch in dispatches:

        driver_id = dispatch.driver_id

        driver_stops = dispatch.route

        driver_pickups = {}
        driver_deliveries = {}

        for index, stop in enumerate(driver_stops):

            if stop.is_pickup:
                driver_pickups[stop.order_id] = index
            else:
                driver_deliveries[stop.order_id] = index

            assigned_ids.append(stop.order_id)

        # Every ticket must have one pickup and one delivery.
        for ticket_id in set(
            stop.order_id for stop in driver_stops
        ):

            assert ticket_id in driver_pickups, (
                f"{ticket_id} missing pickup"
            )

            assert ticket_id in driver_deliveries, (
                f"{ticket_id} missing delivery"
            )

            assert (
                driver_pickups[ticket_id]
                < driver_deliveries[ticket_id]
            ), f"{ticket_id} delivered before pickup"

            assert ticket_id in ticket_lookup

        print(
            f"  Driver {driver_id}: "
            f"{len(set(assigned_ids))} tickets processed."
        )

    # Ensure no duplicate ticket assignments.
    assert len(assigned_ids) == len(set(assigned_ids)), (
        "A ticket was assigned more than once!"
    )

    print("All uniqueness and ordering checks passed.")

    return True


# ==========================================
# JSON SERIALIZATION
# ==========================================

def serialize_dispatches(dispatches, unassigned_ids):

    output = {
        "dispatch_count": len(dispatches),

        "dispatches": [
            {
                "driver_id": dispatch.driver_id,
                "total_time_minutes": dispatch.total_time,
                "stop_count": len(dispatch.route),

                # Do not export is_pickup.
                "route": [
                    {
                        "location": stop.location,
                        "order_id": stop.order_id,
                    }
                    for stop in dispatch.route
                ],
            }
            for dispatch in dispatches
        ],

        "unassigned_ticket_ids": unassigned_ids,
        "unassigned_count": len(unassigned_ids),
    }

    return output


# ==========================================
# MAIN
# ==========================================

async def main():

    random.seed(RANDOM_SEED)

    print("=" * 65)
    print("HITCH DRIVER POOL MANAGER TEST")
    print("=" * 65)

    # --------------------------------------
    # 1. RESET DUMMY DATABASES
    # --------------------------------------

    print("\nResetting dummy databases...")

    clear_database()
    clear_drivers()

    # --------------------------------------
    # 2. GENERATE TICKETS
    # --------------------------------------

    tickets = generate_test_tickets(
        NUMBER_OF_TICKETS
    )

    # --------------------------------------
    # 3. GENERATE DRIVERS
    # --------------------------------------

    print("\nGenerating dummy drivers...")

    generate_test_drivers(
        NUMBER_OF_DRIVERS
    )

    driver_ids = get_all_driver_ids()

    print(f"Generated {len(driver_ids)} drivers.")

    # --------------------------------------
    # 4. GENERATE CLUSTERS
    # --------------------------------------

    print("\nGenerating delivery clusters...")

    clusters = await defineClusters(
        route_pool=tickets,
        maximum_size=MAXIMUM_NODE_SIZE,
        maximum_destination_distance=MAXIMUM_DESTINATION_TIME,
    )

    print(f"Generated {len(clusters)} clusters.")

    # --------------------------------------
    # 5. GENERATE DISPATCHES
    # --------------------------------------

    print("\nRunning driver pool manager...")

    dispatches, unassigned_ids = await generate_dispatches(
        driver_ids=driver_ids,
        cluster_pool=clusters,
    )

    # --------------------------------------
    # 6. VALIDATE RESULTS
    # --------------------------------------

    await validate_dispatches(
        dispatches,
        tickets
    )

    # --------------------------------------
    # 7. EXPORT RESULTS
    # --------------------------------------

    output = serialize_dispatches(
        dispatches,
        unassigned_ids
    )

    OUTPUT_FILE.write_text(
        json.dumps(output, indent=4)
    )

    # --------------------------------------
    # 8. SUMMARY
    # --------------------------------------

    assigned_count = sum(
        len(set(stop.order_id for stop in dispatch.route))
        for dispatch in dispatches
    )

    print("\n" + "=" * 65)
    print("TEST RESULTS")
    print("=" * 65)

    print(f"Tickets generated: {len(tickets)}")
    print(f"Drivers generated: {len(driver_ids)}")
    print(f"Clusters generated: {len(clusters)}")
    print(f"Successful dispatches: {len(dispatches)}")
    print(f"Tickets assigned: {assigned_count}")
    print(f"Tickets unassigned: {len(unassigned_ids)}")

    print(
        f"Assignment percentage: "
        f"{assigned_count / max(len(tickets), 1) * 100:.2f}%"
    )

    print(f"\nResults exported to: {OUTPUT_FILE}")

    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())
