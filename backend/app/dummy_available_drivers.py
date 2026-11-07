
import uuid
import random

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from dummy_valhalla import location_node


# ==========================================
# CONFIGURATION
# ==========================================

MAX_DRIVERS = 1000

# Salt Lake City metro coordinate boundaries.
LAT_MIN = 40.70
LAT_MAX = 40.82

LON_MIN = -111.95
LON_MAX = -111.82


# ==========================================
# DATA STRUCTURES
# ==========================================

@dataclass
class available_driver:
    driver_id: str
    location_node: location_node
    shift_start: float
    shift_end: float


# ==========================================
# IN-MEMORY DATABASE
# ==========================================

_driver_database: dict[str, available_driver] = {}


# ==========================================
# LOCATION GENERATION
# ==========================================

def generate_location() -> dict:
    """Generate a random location within the SLC metro area."""

    return {
        "lat": round(random.uniform(LAT_MIN, LAT_MAX), 6),
        "lon": round(random.uniform(LON_MIN, LON_MAX), 6),
    }


# ==========================================
# DRIVER GENERATION
# ==========================================

def generate_driver(
    driver_id: Optional[str] = None,
    location_node: Optional[location_node] = None,
    shift_start: Optional[float] = None,
    shift_end: Optional[float] = None,
) -> available_driver:

    """
    Generate and store an available driver.

    If values are omitted, random values are generated.

    Default shift:
        November 1, 2026
        6:00 AM - 8:00 PM UTC
    """

    if driver_id is None:
        driver_id = str(uuid.uuid4())

    if driver_id in _driver_database:
        raise ValueError(
            f"Driver {driver_id} already exists."
        )

    # Generate driver's route origin and destination.
    if location_node is None:

        location_node = location_node(
            node_type="driver",
            stop_id=driver_id,
            origin=generate_location(),
            destination=generate_location(),
        )

    # Default shift times.
    if shift_start is None:

        shift_start = datetime(
            2026, 11, 1, 6, 0,
            tzinfo=timezone.utc
        ).timestamp()

    if shift_end is None:

        shift_end = datetime(
            2026, 11, 1, 20, 0,
            tzinfo=timezone.utc
        ).timestamp()

    if shift_start >= shift_end:
        raise ValueError(
            "Shift end must be after shift start."
        )

    if (
        location_node.origin is None
        or location_node.destination is None
    ):
        raise ValueError(
            "Driver must have an origin and destination."
        )

    driver = available_driver(
        driver_id=driver_id,
        location_node=location_node,
        shift_start=float(shift_start),
        shift_end=float(shift_end),
    )

    _driver_database[driver_id] = driver

    # Enforce database size.
    while len(_driver_database) > MAX_DRIVERS:
        oldest_driver_id = next(iter(_driver_database))
        del _driver_database[oldest_driver_id]

    return driver


# ==========================================
# DRIVER RETRIEVAL
# ==========================================

def get_driver(driver_id: str) -> Optional[available_driver]:
    """Retrieve a driver by ID."""

    return _driver_database.get(driver_id)


def get_all_drivers() -> list[available_driver]:
    """Retrieve all available drivers."""

    return list(_driver_database.values())


def get_all_driver_ids() -> list[str]:
    """Retrieve all driver IDs."""

    return list(_driver_database.keys())


# ==========================================
# DRIVER REMOVAL
# ==========================================

def remove_driver(driver_id: str) -> bool:
    """Remove a driver from the database."""

    if driver_id not in _driver_database:
        return False

    del _driver_database[driver_id]

    return True


def clear_drivers():
    """Clear the entire driver database."""

    _driver_database.clear()


# ==========================================
# RANDOM DRIVER POPULATION
# ==========================================

def generate_driver_pool(
    number_of_drivers: int,
    shift_start: Optional[float] = None,
    shift_end: Optional[float] = None,
) -> list[available_driver]:

    """Generate multiple random drivers."""

    if number_of_drivers < 0:
        raise ValueError("Number of drivers cannot be negative.")

    drivers = []

    for _ in range(number_of_drivers):

        driver = generate_driver(
            shift_start=shift_start,
            shift_end=shift_end,
        )

        drivers.append(driver)

    return drivers


# ==========================================
# DATABASE SUMMARY
# ==========================================

def get_database_stats() -> dict:

    return {
        "total_drivers": len(_driver_database),
        "maximum_drivers": MAX_DRIVERS,
        "driver_ids": get_all_driver_ids(),
    }
