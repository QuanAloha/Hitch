
import uuid
import random

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from dummy_valhalla import location_node


# ==========================================
# CONFIGURATION
# ==========================================

DATABASE_SEED = 42

# Salt Lake City metro area boundaries
LAT_MIN = 40.45
LAT_MAX = 40.95

LON_MIN = -112.15
LON_MAX = -111.70

SLC_TIMEZONE = ZoneInfo("America/Denver")

random.seed(DATABASE_SEED)


# ==========================================
# DATA STRUCTURES
# ==========================================

@dataclass
class delivery_ticket:
    # Each ticket corresponds to a pickup.
    ticket_id: str
    locations: location_node
    pickup_time: float  # UNIX timestamp


# ==========================================
# DUMMY DATABASE
# ==========================================

ticket_database: dict[str, delivery_ticket] = {}


# ==========================================
# LOCATION GENERATION
# ==========================================

def generate_location() -> dict:
    """
    Generate a random location within the
    Salt Lake City metropolitan area.
    """

    return {
        "lat": round(random.uniform(LAT_MIN, LAT_MAX), 6),
        "lon": round(random.uniform(LON_MIN, LON_MAX), 6)
    }


# ==========================================
# PICKUP TIME GENERATION
# ==========================================

def generate_pickup_time() -> float:
    """
    Generate a random UNIX timestamp between
    6 AM and 8 PM on November 1, 2026.

    Uses Salt Lake City's local timezone.
    """

    start = datetime(
        2026, 11, 1, 6, 0, 0,
        tzinfo=SLC_TIMEZONE
    )

    end = datetime(
        2026, 11, 1, 20, 0, 0,
        tzinfo=SLC_TIMEZONE
    )

    start_timestamp = start.timestamp()
    end_timestamp = end.timestamp()

    return random.uniform(
        start_timestamp,
        end_timestamp
    )


# ==========================================
# TICKET GENERATION
# ==========================================

def generate_ticket(ticket_id: str) -> delivery_ticket:
    """
    Generate a new dummy delivery ticket.
    """

    origin = generate_location()
    destination = generate_location()

    node = location_node(
        node_type="pickup",
        stop_id=ticket_id,
        origin=origin,
        destination=destination
    )

    return delivery_ticket(
        ticket_id=ticket_id,
        locations=node,
        pickup_time=generate_pickup_time()
    )


# ==========================================
# DATABASE OPERATIONS
# ==========================================

def get_ticket(ticket_id: str) -> delivery_ticket:
    """
    Retrieve a ticket by ID.

    If the ticket doesn't exist, generate it,
    store it, and return it.
    """

    if ticket_id not in ticket_database:

        ticket_database[ticket_id] = generate_ticket(
            ticket_id
        )

    return ticket_database[ticket_id]


def create_ticket() -> delivery_ticket:
    """
    Generate a ticket with a new unique ID.
    """

    ticket_id = str(uuid.uuid4())

    ticket = generate_ticket(ticket_id)

    ticket_database[ticket_id] = ticket

    return ticket


def get_all_tickets() -> list[delivery_ticket]:
    """
    Return all stored tickets.
    """

    return list(ticket_database.values())


def get_database_size() -> int:
    """
    Return the number of stored tickets.
    """

    return len(ticket_database)


def clear_database():
    """
    Clear all stored tickets.
    """

    ticket_database.clear()


# ==========================================
# SERIALIZATION
# ==========================================

def ticket_to_dict(ticket: delivery_ticket) -> dict:
    """
    Convert a ticket into JSON-compatible data.
    """

    return {
        "ticket_id": ticket.ticket_id,
        "locations": {
            "node_type": ticket.locations.node_type,
            "stop_id": ticket.locations.stop_id,
            "origin": ticket.locations.origin,
            "destination": ticket.locations.destination
        },
        "pickup_time": ticket.pickup_time
    }
