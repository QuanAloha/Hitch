"""Fill the frontend-facing tables with dummy data.

Run inside the backend container (or anywhere DATABASE_URL points at Postgres):

    docker compose exec backend python -m app.seed_frontend_data

Safe to re-run: every row has a fixed ID and is upserted, so you get the same
data back instead of duplicates. Timestamps are relative to "now" so the
forming / locked route states always make sense:

    availability-demo-locked   starts in 6h  -> already inside the 12h lock window
    availability-demo-forming  starts in 3d  -> still forming
    availability-demo-later    starts in 8d  -> still forming
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from app import demo_clock
from app.database import get_db_connection, initialize_database

TZ = ZoneInfo("America/Denver")
LOCK_BEFORE_START = timedelta(hours=12)


def now() -> datetime:
    return demo_clock.local_now().replace(microsecond=0)  # follows `python -m app.demo advance`


def today_at(hour: int, minute: int = 0, days: int = 0) -> datetime:
    day = now().date() + timedelta(days=days)
    return datetime.combine(day, time(hour, minute), tzinfo=TZ)


def place(label: str, address: str, lat: float, lon: float) -> dict:
    return {"label": label, "address": address, "lat": lat, "lon": lon}


def window(start: datetime, end: datetime) -> dict:
    return {"start": start.isoformat(), "end": end.isoformat()}


# --- Places around Salt Lake City -------------------------------------------
SUGAR_HOUSE = place("Sugar House", "2100 S 1100 E, Salt Lake City, UT", 40.7231, -111.8575)
AVENUES = place("The Avenues", "300 E 3rd Ave, Salt Lake City, UT", 40.7790, -111.8760)
MILLCREEK = place("Millcreek", "3300 S 900 E, Millcreek, UT", 40.6950, -111.8750)
MURRAY = place("Murray", "5300 S State St, Murray, UT", 40.6560, -111.8880)
DOWNTOWN = place("Downtown Salt Lake City", "50 W 200 S, Salt Lake City, UT", 40.7640, -111.8930)
HOLLADAY = place("Holladay", "4700 S 2300 E, Holladay, UT", 40.6680, -111.8240)
LIBERTY_WELLS = place("Liberty Wells", "1300 S 500 E, Salt Lake City, UT", 40.7420, -111.8700)
LOGAN = place("Logan", "100 N Main St, Logan, UT", 41.7370, -111.8340)
BOISE = place("Boise, Idaho", "500 W Main St, Boise, ID", 43.6150, -116.2023)
USPS_DOWNTOWN = place("Downtown Post Office", "230 W 200 S, Salt Lake City, UT", 40.7655, -111.8985)


# ==========================================
# VEHICLES
# ==========================================

def vehicles() -> list[dict]:
    def vehicle(vid, vin, plate, year, make, model, body, spots):
        return {
            "id": vid, "vin": vin, "vin_last4": vin[-4:], "plate": plate, "plate_state": "UT",
            "year": year, "make": make, "model": model, "body_class": body,
            "name": f"{year} {make} {model}",
            "capacity": {"value": spots, "unit": "standard_package_spots", "source": "estimated_from_body_class"},
        }

    return [
        vehicle("vehicle-demo-rav4", "2T3W1RFV5KW012345", "8ABC123", 2019, "Toyota", "RAV4",
                "Sport Utility Vehicle (SUV)/Multi-Purpose Vehicle (MPV)", 8),
        vehicle("vehicle-demo-civic", "2HGFC2F69LH512346", "C45XYZ", 2020, "Honda", "Civic", "Sedan/Saloon", 4),
        vehicle("vehicle-demo-f150", "1FTEW1EP5KFA12347", "T77TRK", 2019, "Ford", "F-150", "Pickup", 10),
    ]


# ==========================================
# SHIPPER ORDERS + PICKUP RUNS
# ==========================================

def orders_and_runs() -> tuple[list[tuple], list[tuple]]:
    """Return ([(id, order_date, fulfillment_type, doc)], [(id, run_date, doc)])."""
    orders: list[tuple] = []

    def order(oid, days_ago, product, qty, dims, pickup, delivery, source, ftype, status,
              customer, run=None, driver=None, win=None, carrier=None, dropoff=None):
        placed = today_at(9, 15, -days_ago) + timedelta(minutes=len(orders) * 7)
        doc = {
            "id": oid, "source": source, "customer_name": customer, "product_name": product,
            "quantity": qty, "dimensions": {**dims, "unit": "in"}, "pickup": pickup, "delivery": delivery,
            "fulfillment_type": ftype, "status": status, "order_date": placed.isoformat(),
            "pickup_run_id": run, "driver_id": driver[0] if driver else None,
            "driver_name": driver[1] if driver else None, "pickup_window": win,
            "carrier": carrier, "dropoff": dropoff,
        }
        orders.append((oid, placed, ftype, doc))

    jordan, sam = ("driver-17", "Jordan Lee"), ("driver-22", "Sam Rivera")
    win_204 = window(today_at(10, 30), today_at(11, 0))
    win_205 = window(today_at(11, 0), today_at(12, 0))

    # Today: assigned to pickup runs, plus one still waiting and one carrier hand-off.
    order("ORD-4821", 0, "Bakery order", 2, dict(length=18, width=14, height=12), SUGAR_HOUSE, DOWNTOWN,
          "facebook_marketplace", "local_driver", "assigned", "Mia Chen", "run-204", jordan, win_204)
    order("ORD-4820", 0, "Ceramic tableware", 1, dict(length=18, width=14, height=8), SUGAR_HOUSE, AVENUES,
          "etsy", "local_driver", "assigned", "Oliver Grant", "run-204", jordan, win_204)
    order("ORD-4819", 0, "Home goods", 3, dict(length=20, width=16, height=14), MILLCREEK, MURRAY,
          "facebook_marketplace", "local_driver", "assigned", "Noah Patel", "run-205", sam, win_205)
    order("ORD-4817", 0, "Coffee subscription", 1, dict(length=10, width=8, height=6), DOWNTOWN, HOLLADAY,
          "etsy", "local_driver", "assigned", "Ethan Brooks", "run-205", sam, win_205)
    order("ORD-4818", 0, "Vintage lamp", 1, dict(length=30, width=14, height=14), SUGAR_HOUSE, LIBERTY_WELLS,
          "facebook_marketplace", "local_driver", "unassigned", "Ava Thompson")
    order("ORD-4816", 0, "Handmade wall hanging", 1, dict(length=24, width=18, height=3), LOGAN, BOISE,
          "etsy", "fedex", "handed_off", "Priya Shah", driver=("driver-31", "Casey Morgan"), carrier="FedEx",
          dropoff={"facility_name": "FedEx ship center", "address": "Logan, UT"})

    # Earlier in the week / month so the freight chart has history.
    history = [
        (1, "Candles (set of 4)", "etsy", "local_driver", "delivered"),
        (1, "Ceramic planter", "etsy", "fedex", "delivered"),
        (2, "Vintage chair", "facebook_marketplace", "local_driver", "delivered"),
        (2, "Hand-knit blanket", "etsy", "fedex", "delivered"),
        (2, "Bakery order", "direct", "local_driver", "delivered"),
        (3, "Bookshelf", "facebook_marketplace", "local_driver", "delivered"),
        (3, "Wool scarf", "etsy", "third_party_logistics", "delivered"),
        (4, "Pottery mug set", "etsy", "local_driver", "delivered"),
        (4, "Art print", "etsy", "fedex", "delivered"),
        (5, "Coffee beans", "direct", "local_driver", "delivered"),
        (6, "Wall clock", "facebook_marketplace", "local_driver", "delivered"),
        (8, "Dining table", "facebook_marketplace", "local_driver", "delivered"),
        (9, "Ceramic vase", "etsy", "fedex", "delivered"),
        (11, "Quilt", "etsy", "local_driver", "delivered"),
        (15, "Bike", "facebook_marketplace", "local_driver", "delivered"),
        (17, "Candle gift box", "etsy", "fedex", "delivered"),
        (20, "Sofa table", "facebook_marketplace", "local_driver", "delivered"),
        (24, "Pottery bowl", "etsy", "local_driver", "delivered"),
    ]
    for index, (days_ago, product, source, ftype, status) in enumerate(history, start=1):
        order(f"ORD-47{index:02d}", days_ago, product, 1, dict(length=12, width=10, height=8), SUGAR_HOUSE,
              DOWNTOWN if ftype == "local_driver" else BOISE, source, ftype, status, f"Customer {index}",
              carrier="FedEx" if ftype == "fedex" else None)

    runs = [
        ("run-204", now().date(), {
            "id": "run-204", "label": "Pickup run 204 · Sugar House", "date": now().date().isoformat(),
            "driver_id": jordan[0], "driver_name": jordan[1], "vehicle_name": "2019 Ford F-150",
            "pickup_window": win_204, "order_ids": ["ORD-4821", "ORD-4820"],
        }),
        ("run-205", now().date(), {
            "id": "run-205", "label": "Pickup run 205 · Millcreek", "date": now().date().isoformat(),
            "driver_id": sam[0], "driver_name": sam[1], "vehicle_name": "2020 Honda Civic",
            "pickup_window": win_205, "order_ids": ["ORD-4819", "ORD-4817"],
        }),
    ]
    return orders, runs


# ==========================================
# SELLER PICKUP REQUESTS
# ==========================================

def pickup_requests() -> list[tuple]:
    def request(rid, source, product, qty, dims, pickup, dest, start, end, notes="", assigned=None):
        created = now() - timedelta(hours=len(rid))
        doc = {
            "id": rid, "source": source, "product_name": product, "quantity": qty,
            "dimensions": {**dims, "unit": "in"}, "notes": notes,
            "pickup": {k: v for k, v in pickup.items()}, "destination": {k: v for k, v in dest.items()},
            "available_window": window(start, end),
            "status": "assigned" if assigned else "awaiting_driver",
            "pickup_run_id": assigned["run"] if assigned else None,
            "driver_id": assigned["driver_id"] if assigned else None,
            "driver_name": assigned["driver_name"] if assigned else None,
            "confirmed_pickup_window": assigned["window"] if assigned else None,
            "created_at": created.isoformat(),
        }
        return (rid, source, created, doc)

    return [
        request("pickup-request-demo-1", "etsy", "Ceramic vase", 1, dict(length=14, width=10, height=10),
                SUGAR_HOUSE, AVENUES, today_at(9, days=1), today_at(12, days=1), "Fragile"),
        request("pickup-request-demo-2", "etsy", "Handmade mugs (box of 6)", 1, dict(length=16, width=12, height=8),
                MILLCREEK, DOWNTOWN, today_at(13, days=1), today_at(16, days=1)),
        request("pickup-request-demo-3", "etsy", "Wall hanging", 1, dict(length=24, width=18, height=3),
                LIBERTY_WELLS, MURRAY, today_at(10, days=2), today_at(14, days=2),
                assigned={"run": "run-301", "driver_id": "driver-17", "driver_name": "Jordan Lee",
                          "window": window(today_at(11, days=2), today_at(11, 30, days=2))}),
        request("pickup-request-demo-4", "facebook_marketplace", "Vintage side table", 1,
                dict(length=28, width=20, height=24), SUGAR_HOUSE, HOLLADAY, today_at(9, days=1), today_at(11, days=1),
                "Meet at the garage"),
        request("pickup-request-demo-5", "facebook_marketplace", "Standing desk", 2, dict(length=48, width=30, height=6),
                MURRAY, LIBERTY_WELLS, today_at(15, days=1), today_at(18, days=1),
                assigned={"run": "run-302", "driver_id": "driver-22", "driver_name": "Sam Rivera",
                          "window": window(today_at(16, days=1), today_at(16, 30, days=1))}),
    ]


# ==========================================
# AVAILABILITY + ROUTES
# ==========================================

def availability_and_routes() -> tuple[list[tuple], list[tuple]]:
    veh = {v["id"]: v for v in vehicles()}

    def block(bid, vehicle_id, start, hours, start_place, fuel, route_id=None, fill=0, spots=0):
        v = veh[vehicle_id]
        end = start + timedelta(hours=hours)
        doc = {
            "id": bid, "vehicle_id": vehicle_id, "vehicle_name": v["name"], "capacity": v["capacity"],
            "start_location": start_place, "window": window(start, end), "fuel_level_percent": fuel,
            "locks_at": (start - LOCK_BEFORE_START).isoformat(), "estimated_fill_percent": fill,
        }
        return (bid, vehicle_id, start, end, route_id, doc)

    locked_start = (now() + timedelta(hours=6)).replace(minute=0, second=0)
    blocks = [
        block("availability-demo-locked", "vehicle-demo-rav4", locked_start, 4, SUGAR_HOUSE, 75,
              route_id="route-demo-1", fill=78),
        block("availability-demo-forming", "vehicle-demo-civic", today_at(9, days=3), 4, SUGAR_HOUSE, 90, fill=42),
        block("availability-demo-later", "vehicle-demo-f150", today_at(10, days=8), 4, MILLCREEK, 60, fill=55),
    ]

    def stop(sid, kind, label, p, order_ids=(), note=None):
        return {"id": sid, "type": kind, "label": label, "address": p["address"], "lat": p["lat"], "lon": p["lon"],
                "order_ids": list(order_ids), "instructions": note, "completed_at": None}

    stops = [
        stop("stop-1", "start", "Start · Sugar House", SUGAR_HOUSE),
        stop("stop-2", "pickup", "Etsy pickup · Juniper & Clay, Sugar House", SUGAR_HOUSE, ["pickup-request-demo-1"],
             "Ring the studio bell."),
        stop("stop-3", "pickup", "Etsy pickup · Wasatch Wares, Millcreek", MILLCREEK, ["pickup-request-demo-2"]),
        stop("stop-4", "carrier_dropoff", "Carrier drop-off · USPS Downtown", USPS_DOWNTOWN,
             ["pickup-request-demo-1", "pickup-request-demo-2"]),
        stop("stop-5", "pickup", "Pickup · Acme Goods, Sugar House", SUGAR_HOUSE, ["ORD-4820"]),
        stop("stop-6", "dropoff", "Drop-off · The Avenues", AVENUES, ["ORD-4820"]),
        stop("stop-7", "dropoff", "Drop-off · Holladay", HOLLADAY, ["ORD-4817"]),
    ]
    route = {
        "id": "route-demo-1", "availability_id": "availability-demo-locked",
        # No geometry on purpose: the API fills it from Valhalla on first read.
        "stops": stops, "geometry": None,
        "fill_percent": 78, "confirmed_package_spots": 6,
        "windows": [{"stop_id": s["id"], "start": (locked_start + timedelta(minutes=25 * i)).isoformat(),
                     "end": (locked_start + timedelta(minutes=25 * i + 20)).isoformat()} for i, s in enumerate(stops)],
        "receipt": {
            "lines": [
                {"shipper": "Acme Goods", "item": "Ceramic tableware · ORD-4820", "amount": 18.50},
                {"shipper": "Mia Chen · Marketplace", "item": "Coffee subscription · ORD-4817", "amount": 12.00},
                {"shipper": "Juniper & Clay · Etsy", "item": "Ceramic vase · carrier batch", "amount": 9.75},
            ],
            "total": 40.25,
        },
        "fuel_stops": [
            {"id": "fuel-1", "name": "Maverik", "address": "1450 S 700 E, Salt Lake City", "lat": 40.7370,
             "lon": -111.8700, "price": 3.19, "price_unit": "gal", "price_label": "$3.19 / gal",
             "detour_minutes": 4, "detour_label": "approx. 4 min detour", "source": "demo",
             "updated_at": (now() - timedelta(hours=1)).isoformat()},
        ],
    }
    return blocks, [("route-demo-1", "availability-demo-locked", route)]


# ==========================================
# WRITE
# ==========================================

def seed() -> dict[str, int]:
    initialize_database()
    orders, runs = orders_and_runs()
    requests = pickup_requests()
    blocks, routes = availability_and_routes()

    with get_db_connection() as c:
        for v in vehicles():
            c.execute(
                "INSERT INTO driver_vehicles (id, vin, data) VALUES (%s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
                (v["id"], v["vin"], Jsonb(v)),
            )
        for oid, placed, ftype, doc in orders:
            c.execute(
                "INSERT INTO shipper_orders (id, order_date, fulfillment_type, data) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET order_date = EXCLUDED.order_date, "
                "fulfillment_type = EXCLUDED.fulfillment_type, data = EXCLUDED.data",
                (oid, placed, ftype, Jsonb(doc)),
            )
        # Runs are dated "today"; drop stale demo runs from earlier seeds first.
        c.execute("DELETE FROM pickup_runs WHERE id IN ('run-204', 'run-205')")
        for rid, run_date, doc in runs:
            c.execute("INSERT INTO pickup_runs (id, run_date, data) VALUES (%s, %s, %s)", (rid, run_date, Jsonb(doc)))
        for rid, source, created, doc in requests:
            c.execute(
                "INSERT INTO seller_pickup_requests (id, source, created_at, data) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
                (rid, source, created, Jsonb(doc)),
            )
        for bid, vid, start, end, route_id, doc in blocks:
            c.execute(
                "INSERT INTO driver_availability (id, vehicle_id, start_at, end_at, route_id, data) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET start_at = EXCLUDED.start_at, "
                "end_at = EXCLUDED.end_at, route_id = EXCLUDED.route_id, data = EXCLUDED.data",
                (bid, vid, start, end, route_id, Jsonb(doc)),
            )
        for rid, availability_id, doc in routes:
            c.execute(
                "INSERT INTO driver_routes (id, availability_id, data) VALUES (%s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
                (rid, availability_id, Jsonb(doc)),
            )

    return {
        "vehicles": len(vehicles()), "shipper_orders": len(orders), "pickup_runs": len(runs),
        "seller_pickup_requests": len(requests), "driver_availability": len(blocks), "driver_routes": len(routes),
    }


if __name__ == "__main__":
    for table, count in seed().items():
        print(f"{table}: {count}")
