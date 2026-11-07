"""PostgreSQL schema and repository functions.

Every table maps to a dataclass in models.py. Reads return those dataclasses
(never raw rows), and writes accept them.

location_cluster is intentionally not persisted; it is a transient grouping
computed by pathing.py.
"""

import os
from typing import Optional

import psycopg
from psycopg.types.json import Jsonb

from app.models import (
    AvailableDriver,
    Car,
    DeliveryTicket,
    Dispatch,
    Driver,
    Route,
    location_node,
)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://hitch:hitch_dev_password@localhost:5432/hitch",
)

_database_initialized = False


# ==========================================
# CONNECTION / SCHEMA
# ==========================================

def get_db_connection():
    return psycopg.connect(DATABASE_URL, connect_timeout=5)


# Order matters: referenced tables come first.
_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS location_nodes (
        stop_id TEXT PRIMARY KEY,
        node_type TEXT NOT NULL,
        origin JSONB,
        destination JSONB
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS routes (
        route_id UUID PRIMARY KEY,
        origin JSONB NOT NULL,
        destination JSONB NOT NULL,
        costing TEXT NOT NULL,
        route_plan JSONB NOT NULL,
        route_time_minutes DOUBLE PRECISION NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    # Upgrades for databases created before these columns existed.
    "ALTER TABLE routes ADD COLUMN IF NOT EXISTS locations JSONB",
    "ALTER TABLE routes ADD COLUMN IF NOT EXISTS stop_ids JSONB",
    """
    CREATE TABLE IF NOT EXISTS delivery_tickets (
        ticket_id TEXT PRIMARY KEY,
        ticket_type TEXT NOT NULL,
        stop_id TEXT NOT NULL REFERENCES location_nodes(stop_id),
        description TEXT NOT NULL DEFAULT '',
        quantity INTEGER NOT NULL DEFAULT 1
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cars (
        car_id TEXT PRIMARY KEY,
        make TEXT NOT NULL,
        model TEXT NOT NULL,
        year INTEGER NOT NULL,
        color TEXT NOT NULL,
        mpg DOUBLE PRECISION NOT NULL,
        cargo_capacity DOUBLE PRECISION NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS drivers (
        driver_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        location JSONB,
        car_id TEXT REFERENCES cars(car_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS available_drivers (
        driver_id TEXT NOT NULL REFERENCES drivers(driver_id),
        route_origin JSONB NOT NULL,
        route_end JSONB NOT NULL,
        shift_start TIMESTAMPTZ NOT NULL,
        shift_end TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (driver_id, shift_start)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dispatches (
        dispatch_id TEXT PRIMARY KEY,
        route_id UUID NOT NULL REFERENCES routes(route_id),
        driver_id TEXT REFERENCES drivers(driver_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dispatch_tickets (
        dispatch_id TEXT NOT NULL REFERENCES dispatches(dispatch_id) ON DELETE CASCADE,
        ticket_id TEXT NOT NULL REFERENCES delivery_tickets(ticket_id),
        position INTEGER NOT NULL,
        PRIMARY KEY (dispatch_id, ticket_id)
    )
    """,
    # ---- Frontend-facing documents (see frontend_routes.py) ----
    # Each table keeps the API document as JSONB plus the columns we filter on.
    """
    CREATE TABLE IF NOT EXISTS shipper_orders (
        id TEXT PRIMARY KEY,
        order_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        fulfillment_type TEXT NOT NULL,
        data JSONB NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pickup_runs (
        id TEXT PRIMARY KEY,
        run_date DATE NOT NULL,
        data JSONB NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS seller_pickup_requests (
        id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        data JSONB NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS driver_vehicles (
        id TEXT PRIMARY KEY,
        vin TEXT NOT NULL UNIQUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        data JSONB NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS driver_availability (
        id TEXT PRIMARY KEY,
        vehicle_id TEXT NOT NULL,
        start_at TIMESTAMPTZ NOT NULL,
        end_at TIMESTAMPTZ NOT NULL,
        route_id TEXT,
        data JSONB NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS travel_times (
        a_lat DOUBLE PRECISION NOT NULL,
        a_lon DOUBLE PRECISION NOT NULL,
        b_lat DOUBLE PRECISION NOT NULL,
        b_lon DOUBLE PRECISION NOT NULL,
        seconds DOUBLE PRECISION NOT NULL,
        estimated BOOLEAN NOT NULL DEFAULT FALSE,
        PRIMARY KEY (a_lat, a_lon, b_lat, b_lon)
    )
    """,
    "ALTER TABLE travel_times ADD COLUMN IF NOT EXISTS estimated BOOLEAN NOT NULL DEFAULT FALSE",
    """
    CREATE TABLE IF NOT EXISTS demo_state (
        key TEXT PRIMARY KEY,
        value JSONB NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS last_minute_requests (
        id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        data JSONB NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS driver_routes (
        id TEXT PRIMARY KEY,
        availability_id TEXT NOT NULL,
        data JSONB NOT NULL
    )
    """,
]


def initialize_database() -> None:
    """Create all tables once; safe to call from scripts or API startup."""
    global _database_initialized
    if _database_initialized:
        return

    try:
        with get_db_connection() as connection:
            for statement in _SCHEMA:
                connection.execute(statement)
        _database_initialized = True
    except psycopg.Error as exc:
        raise RuntimeError(
            f"Could not initialize PostgreSQL tables using DATABASE_URL={DATABASE_URL!r}"
        ) from exc


# ==========================================
# ROW -> DATACLASS CONVERTERS
# ==========================================

_ROUTE_COLUMNS = (
    "route_id, origin, destination, costing, route_plan, "
    "route_time_minutes, created_at, locations, stop_ids"
)


def _route_from_row(row) -> Route:
    # Rows saved before the `locations` column existed only have origin/destination.
    return Route(
        route_id=str(row[0]),
        locations=row[7] or [row[1], row[2]],
        costing=row[3],
        route_time_minutes=row[5],
        route_plan=row[4],
        stop_ids=row[8] or [],
        created_at=row[6],
    )


_NODE_COLUMNS = "stop_id, node_type, origin, destination"


def _node_from_row(row) -> location_node:
    return location_node(
        stop_id=row[0], node_type=row[1], origin=row[2], destination=row[3]
    )


_TICKET_COLUMNS = "ticket_id, ticket_type, stop_id, description, quantity"


def _ticket_from_row(row) -> DeliveryTicket:
    return DeliveryTicket(
        ticket_id=row[0],
        ticket_type=row[1],
        stop_id=row[2],
        description=row[3],
        quantity=row[4],
    )


_CAR_COLUMNS = "car_id, make, model, year, color, mpg, cargo_capacity"


def _car_from_row(row) -> Car:
    return Car(
        car_id=row[0], make=row[1], model=row[2], year=row[3],
        color=row[4], mpg=row[5], cargo_capacity=row[6],
    )


_DRIVER_COLUMNS = "driver_id, name, location, car_id"


def _driver_from_row(row) -> Driver:
    return Driver(driver_id=row[0], name=row[1], location=row[2], car_id=row[3])


_AVAILABLE_DRIVER_COLUMNS = "driver_id, route_origin, route_end, shift_start, shift_end"


def _available_driver_from_row(row) -> AvailableDriver:
    return AvailableDriver(
        driver_id=row[0], route_origin=row[1], route_end=row[2],
        shift_start=row[3], shift_end=row[4],
    )


# ==========================================
# LOCATION NODES
# ==========================================

def save_location_node(node: location_node) -> None:
    initialize_database()
    with get_db_connection() as connection:
        connection.execute(
            f"""
            INSERT INTO location_nodes ({_NODE_COLUMNS})
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (stop_id) DO UPDATE SET
                node_type = EXCLUDED.node_type,
                origin = EXCLUDED.origin,
                destination = EXCLUDED.destination
            """,
            (
                node.stop_id,
                node.node_type,
                Jsonb(node.origin) if node.origin is not None else None,
                Jsonb(node.destination) if node.destination is not None else None,
            ),
        )


def get_location_node(stop_id: str) -> Optional[location_node]:
    initialize_database()
    with get_db_connection() as connection:
        row = connection.execute(
            f"SELECT {_NODE_COLUMNS} FROM location_nodes WHERE stop_id = %s",
            (stop_id,),
        ).fetchone()
    return _node_from_row(row) if row else None


def list_location_nodes() -> list[location_node]:
    initialize_database()
    with get_db_connection() as connection:
        rows = connection.execute(
            f"SELECT {_NODE_COLUMNS} FROM location_nodes ORDER BY stop_id"
        ).fetchall()
    return [_node_from_row(row) for row in rows]


# ==========================================
# ROUTES
# ==========================================

def save_route(route: Route) -> None:
    initialize_database()
    with get_db_connection() as connection:
        connection.execute(
            """
            INSERT INTO routes (
                route_id, origin, destination, costing, route_plan,
                route_time_minutes, locations, stop_ids
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (route_id) DO UPDATE SET
                origin = EXCLUDED.origin,
                destination = EXCLUDED.destination,
                costing = EXCLUDED.costing,
                route_plan = EXCLUDED.route_plan,
                route_time_minutes = EXCLUDED.route_time_minutes,
                locations = EXCLUDED.locations,
                stop_ids = EXCLUDED.stop_ids
            """,
            (
                route.route_id,
                Jsonb(route.origin),
                Jsonb(route.destination),
                route.costing,
                Jsonb(route.route_plan),
                route.route_time_minutes,
                Jsonb(route.locations),
                Jsonb(route.stop_ids),
            ),
        )


def get_route(route_id: str) -> Optional[Route]:
    """`route_id` must be a valid UUID string."""
    initialize_database()
    with get_db_connection() as connection:
        row = connection.execute(
            f"SELECT {_ROUTE_COLUMNS} FROM routes WHERE route_id = %s",
            (route_id,),
        ).fetchone()
    return _route_from_row(row) if row else None


def list_routes() -> list[Route]:
    initialize_database()
    with get_db_connection() as connection:
        rows = connection.execute(
            f"SELECT {_ROUTE_COLUMNS} FROM routes ORDER BY created_at DESC"
        ).fetchall()
    return [_route_from_row(row) for row in rows]


# ==========================================
# DELIVERY TICKETS
# ==========================================

def save_delivery_ticket(ticket: DeliveryTicket) -> None:
    initialize_database()
    with get_db_connection() as connection:
        connection.execute(
            f"""
            INSERT INTO delivery_tickets ({_TICKET_COLUMNS})
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (ticket_id) DO UPDATE SET
                ticket_type = EXCLUDED.ticket_type,
                stop_id = EXCLUDED.stop_id,
                description = EXCLUDED.description,
                quantity = EXCLUDED.quantity
            """,
            (ticket.ticket_id, ticket.ticket_type, ticket.stop_id,
             ticket.description, ticket.quantity),
        )


def get_delivery_ticket(ticket_id: str) -> Optional[DeliveryTicket]:
    initialize_database()
    with get_db_connection() as connection:
        row = connection.execute(
            f"SELECT {_TICKET_COLUMNS} FROM delivery_tickets WHERE ticket_id = %s",
            (ticket_id,),
        ).fetchone()
    return _ticket_from_row(row) if row else None


# ==========================================
# CARS / DRIVERS / AVAILABLE DRIVERS
# ==========================================

def save_car(car: Car) -> None:
    initialize_database()
    with get_db_connection() as connection:
        connection.execute(
            f"""
            INSERT INTO cars ({_CAR_COLUMNS})
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (car_id) DO UPDATE SET
                make = EXCLUDED.make, model = EXCLUDED.model,
                year = EXCLUDED.year, color = EXCLUDED.color,
                mpg = EXCLUDED.mpg, cargo_capacity = EXCLUDED.cargo_capacity
            """,
            (car.car_id, car.make, car.model, car.year,
             car.color, car.mpg, car.cargo_capacity),
        )


def get_car(car_id: str) -> Optional[Car]:
    initialize_database()
    with get_db_connection() as connection:
        row = connection.execute(
            f"SELECT {_CAR_COLUMNS} FROM cars WHERE car_id = %s", (car_id,)
        ).fetchone()
    return _car_from_row(row) if row else None


def save_driver(driver: Driver) -> None:
    initialize_database()
    with get_db_connection() as connection:
        connection.execute(
            f"""
            INSERT INTO drivers ({_DRIVER_COLUMNS})
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (driver_id) DO UPDATE SET
                name = EXCLUDED.name,
                location = EXCLUDED.location,
                car_id = EXCLUDED.car_id
            """,
            (
                driver.driver_id,
                driver.name,
                Jsonb(driver.location) if driver.location is not None else None,
                driver.car_id,
            ),
        )


def get_driver(driver_id: str) -> Optional[Driver]:
    initialize_database()
    with get_db_connection() as connection:
        row = connection.execute(
            f"SELECT {_DRIVER_COLUMNS} FROM drivers WHERE driver_id = %s",
            (driver_id,),
        ).fetchone()
    return _driver_from_row(row) if row else None


def save_available_driver(available: AvailableDriver) -> None:
    initialize_database()
    with get_db_connection() as connection:
        connection.execute(
            f"""
            INSERT INTO available_drivers ({_AVAILABLE_DRIVER_COLUMNS})
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (driver_id, shift_start) DO UPDATE SET
                route_origin = EXCLUDED.route_origin,
                route_end = EXCLUDED.route_end,
                shift_end = EXCLUDED.shift_end
            """,
            (
                available.driver_id,
                Jsonb(available.route_origin),
                Jsonb(available.route_end),
                available.shift_start,
                available.shift_end,
            ),
        )


def list_available_drivers() -> list[AvailableDriver]:
    initialize_database()
    with get_db_connection() as connection:
        rows = connection.execute(
            f"SELECT {_AVAILABLE_DRIVER_COLUMNS} FROM available_drivers "
            "ORDER BY shift_start"
        ).fetchall()
    return [_available_driver_from_row(row) for row in rows]


# ==========================================
# DISPATCHES
# ==========================================

def save_dispatch(dispatch: Dispatch) -> None:
    """Persist a dispatch and its ticket list. Route and tickets must already exist."""
    initialize_database()
    with get_db_connection() as connection:
        connection.execute(
            """
            INSERT INTO dispatches (dispatch_id, route_id, driver_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (dispatch_id) DO UPDATE SET
                route_id = EXCLUDED.route_id,
                driver_id = EXCLUDED.driver_id
            """,
            (dispatch.dispatch_id, dispatch.route.route_id, dispatch.driver_id),
        )
        connection.execute(
            "DELETE FROM dispatch_tickets WHERE dispatch_id = %s",
            (dispatch.dispatch_id,),
        )
        for position, ticket in enumerate(dispatch.tickets):
            connection.execute(
                "INSERT INTO dispatch_tickets (dispatch_id, ticket_id, position) "
                "VALUES (%s, %s, %s)",
                (dispatch.dispatch_id, ticket.ticket_id, position),
            )


def get_dispatch(dispatch_id: str) -> Optional[Dispatch]:
    initialize_database()
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT dispatch_id, route_id, driver_id FROM dispatches "
            "WHERE dispatch_id = %s",
            (dispatch_id,),
        ).fetchone()
        if row is None:
            return None

        route_row = connection.execute(
            f"SELECT {_ROUTE_COLUMNS} FROM routes WHERE route_id = %s", (row[1],)
        ).fetchone()
        ticket_rows = connection.execute(
            """
            SELECT t.ticket_id, t.ticket_type, t.stop_id, t.description, t.quantity
            FROM dispatch_tickets dt
            JOIN delivery_tickets t ON t.ticket_id = dt.ticket_id
            WHERE dt.dispatch_id = %s
            ORDER BY dt.position
            """,
            (dispatch_id,),
        ).fetchall()

    return Dispatch(
        dispatch_id=row[0],
        route=_route_from_row(route_row),
        tickets=[_ticket_from_row(t) for t in ticket_rows],
        driver_id=row[2],
    )
