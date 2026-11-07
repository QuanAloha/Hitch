
from dataclasses import dataclass

from dummy_available_drivers import get_driver
from dummy_ticket_db import get_all_tickets

from pathing import (
    location_cluster,
    route_stop,
    routes_from_clusters,
)

from dummy_valhalla import generate_route


# ==========================================
# DATA STRUCTURES
# ==========================================

@dataclass
class Dispatch:
    route: list[route_stop]
    driver_id: str
    total_time: float


# ==========================================
# ROUTE VALIDATION
# ==========================================

async def validate_driver_route(
    route: list[route_stop],
    courier,
    shift_start: float,
    shift_end: float,
    tickets: dict,
) -> tuple[bool, list[str]]:
    """
    Validate that every ticket:
    - Has exactly one pickup and one delivery.
    - Is picked up within the driver's shift.
    - Is delivered before the driver's shift ends.

    Returns:
        (True, []) when valid.
        (False, invalid_ticket_ids) otherwise.
    """

    if not route or shift_start >= shift_end:
        return False, list({stop.order_id for stop in route})

    current_time = shift_start
    current_location = courier.origin

    picked_up: set[str] = set()
    delivered: set[str] = set()

    invalid_ticket_ids: set[str] = set()

    # --------------------------------------
    # VERIFY STOP PAIRS
    # --------------------------------------

    ticket_stops: dict[str, list[route_stop]] = {}

    for stop in route:
        ticket_stops.setdefault(stop.order_id, []).append(stop)

    for ticket_id, stops in ticket_stops.items():

        ticket = tickets.get(ticket_id)

        if ticket is None:
            invalid_ticket_ids.add(ticket_id)
            continue

        if len(stops) != 2:
            invalid_ticket_ids.add(ticket_id)
            continue

        if sum(stop.is_pickup for stop in stops) != 1:
            invalid_ticket_ids.add(ticket_id)
            continue

        if not (
            shift_start <= ticket.pickup_time <= shift_end
        ):
            invalid_ticket_ids.add(ticket_id)

        # Verify that the stop coordinates match the ticket.
        node = ticket.locations

        for stop in stops:

            expected_location = (
                node.origin if stop.is_pickup
                else node.destination
            )

            if stop.location != expected_location:
                invalid_ticket_ids.add(ticket_id)

    # --------------------------------------
    # SIMULATE ROUTE
    # --------------------------------------

    for stop in route:

        ticket_id = stop.order_id

        if ticket_id in invalid_ticket_ids:
            continue

        ticket = tickets[ticket_id]

        # A delivery cannot occur before its pickup.
        if not stop.is_pickup and ticket_id not in picked_up:
            invalid_ticket_ids.add(ticket_id)
            continue

        if stop.is_pickup and ticket_id in picked_up:
            invalid_ticket_ids.add(ticket_id)
            continue

        result = await generate_route([
            current_location,
            stop.location,
        ])

        travel_seconds = float(result["route_time"]) * 60.0

        current_time += travel_seconds

        if current_time > shift_end:
            invalid_ticket_ids.add(ticket_id)
            continue

        if stop.is_pickup:

            # Wait until the scheduled pickup time if necessary.
            current_time = max(
                current_time,
                ticket.pickup_time,
            )

            if current_time > shift_end:
                invalid_ticket_ids.add(ticket_id)
                continue

            picked_up.add(ticket_id)

        else:

            delivered.add(ticket_id)

        current_location = stop.location

    # --------------------------------------
    # VALIDATE FINAL COURIER DESTINATION
    # --------------------------------------

    valid_ticket_ids = (
        set(ticket_stops.keys()) - invalid_ticket_ids
    )

    if valid_ticket_ids:

        result = await generate_route([
            current_location,
            courier.destination,
        ])

        final_travel_seconds = (
            float(result["route_time"]) * 60.0
        )

        if current_time + final_travel_seconds > shift_end:

            # Route cannot finish before the shift ends.
            # Remove the last ticket and retry.
            last_ticket_id = next(
                (
                    stop.order_id
                    for stop in reversed(route)
                    if stop.order_id in valid_ticket_ids
                ),
                None,
            )

            if last_ticket_id is not None:
                invalid_ticket_ids.add(last_ticket_id)

    # Ensure every retained ticket was delivered.
    for ticket_id in valid_ticket_ids:

        if ticket_id not in delivered:
            invalid_ticket_ids.add(ticket_id)

    return (
        len(invalid_ticket_ids) == 0,
        list(invalid_ticket_ids),
    )


# ==========================================
# CLUSTER FILTERING
# ==========================================

def filter_clusters(
    cluster_pool: list[location_cluster],
    excluded_ticket_ids: set[str],
    eligible_ticket_ids: set[str],
) -> list[location_cluster]:
    """
    Return copies of clusters containing only complete,
    eligible ticket pairs.
    """

    filtered_clusters = []

    for cluster in cluster_pool:

        ticket_stops: dict[str, list[route_stop]] = {}

        for stop in cluster.locations:
            ticket_stops.setdefault(
                stop.order_id,
                []
            ).append(stop)

        filtered_stops = []

        for ticket_id, stops in ticket_stops.items():

            if ticket_id in excluded_ticket_ids:
                continue

            if ticket_id not in eligible_ticket_ids:
                continue

            # Keep both pickup and delivery together.
            if len(stops) != 2:
                continue

            if sum(stop.is_pickup for stop in stops) != 1:
                continue

            filtered_stops.extend(stops)

        if filtered_stops:

            filtered_clusters.append(
                location_cluster(
                    cluster_type=cluster.cluster_type,
                    locations=filtered_stops,
                    total_time=cluster.total_time,
                    maximum_size=cluster.maximum_size,
                )
            )

    return filtered_clusters


# ==========================================
# DRIVER POOL MANAGER
# ==========================================

async def generate_dispatches(
    driver_ids: list[str],
    cluster_pool: list[location_cluster],
) -> tuple[list[Dispatch], list[str]]:
    """
    Generate dispatches for available drivers.

    Returns:
        Item 1: Successful dispatches.
        Item 2: Unassigned ticket IDs.
    """

    dispatches: list[Dispatch] = []

    tickets = {
        ticket.ticket_id: ticket
        for ticket in get_all_tickets()
    }

    all_ticket_ids = set(tickets.keys())

    # Global assignment tracking.
    assigned_ticket_ids: set[str] = set()

    # --------------------------------------
    # PROCESS DRIVERS
    # --------------------------------------

    for driver_id in driver_ids:

        driver = get_driver(driver_id)

        if driver is None:
            print(f"Driver {driver_id} not found.")
            continue

        courier = driver.location_node

        if courier.node_type != "driver":
            continue

        if driver.shift_start >= driver.shift_end:
            continue

        # ----------------------------------
        # FILTER TICKETS BY PICKUP TIME
        # ----------------------------------

        eligible_ticket_ids = {
            ticket_id
            for ticket_id, ticket in tickets.items()
            if (
                driver.shift_start
                <= ticket.pickup_time
                <= driver.shift_end
                and ticket_id not in assigned_ticket_ids
            )
        }

        remaining_clusters = filter_clusters(
            cluster_pool=cluster_pool,
            excluded_ticket_ids=assigned_ticket_ids,
            eligible_ticket_ids=eligible_ticket_ids,
        )

        # ----------------------------------
        # ITERATIVELY GENERATE ROUTES
        # ----------------------------------

        while remaining_clusters:

            route = await routes_from_clusters(
                courier=courier,
                cluster_pool=remaining_clusters,
            )

            if not route:
                break

            valid, invalid_ids = await validate_driver_route(
                route=route,
                courier=courier,
                shift_start=driver.shift_start,
                shift_end=driver.shift_end,
                tickets=tickets,
            )

            if valid:

                complete_locations = [
                    courier.origin,
                    *[stop.location for stop in route],
                    courier.destination,
                ]

                result = await generate_route(
                    complete_locations
                )

                total_time = float(result["route_time"])

                assigned_ids = {
                    stop.order_id
                    for stop in route
                }

                dispatches.append(
                    Dispatch(
                        route=route,
                        driver_id=driver_id,
                        total_time=total_time,
                    )
                )

                assigned_ticket_ids.update(assigned_ids)

                print(
                    f"Driver {driver_id}: "
                    f"{len(assigned_ids)} tickets dispatched, "
                    f"{len(route)} stops, "
                    f"{total_time:.2f} minutes."
                )

                break

            # ----------------------------------
            # REMOVE INVALID TICKETS
            # ----------------------------------

            invalid_ids = set(invalid_ids)

            if not invalid_ids:
                break

            print(
                f"Driver {driver_id}: removing "
                f"{len(invalid_ids)} infeasible tickets."
            )

            remaining_clusters = filter_clusters(
                cluster_pool=remaining_clusters,
                excluded_ticket_ids=invalid_ids,
                eligible_ticket_ids=eligible_ticket_ids,
            )

    # --------------------------------------
    # FIND UNASSIGNED TICKETS
    # --------------------------------------

    unassigned_ticket_ids = sorted(
        all_ticket_ids - assigned_ticket_ids
    )

    return dispatches, unassigned_ticket_ids
