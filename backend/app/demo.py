"""Demo control script: forward time, populate sellers, and watch dispatch work.

Run inside the backend container:

    docker compose exec backend python -m app.demo <command> [options]

Commands
  status                       clock, queue, blocks, last-minute pool
  scenario                     wipe + fleet + traditional orders + Etsy bookings (quick start)
  wipe                         delete all frontend/dispatch data and reset the clock
  world   --days N             regional driver fleet with availability blocks
  business --day tomorrow -n   traditional shippers' predicted shipments (no "schedule" button)
  etsy    --day tomorrow -n    Etsy / Marketplace sellers booking pickups (same path as the UI)
  tick                         run the dispatcher now (plan; finalizes anything due)
  advance --hours H | --days D | --to "2026-11-08 07:00"
                               move the clock, stopping at every route-finalization moment
  report  --day tomorrow       every route, stop by stop, with efficiency stats
  trace   <order-or-request-id> where a package landed (or will land) and its neighbours
  lastminute                   open last-minute requests
  reset-clock                  back to real time

Days: today | tomorrow | +N | YYYY-MM-DD   (all in Mountain time)
"""

import argparse
import random

import httpx
from datetime import date, datetime, time, timedelta, timezone

from psycopg.types.json import Jsonb

from app import demo_clock, dispatch_engine, planner
from app.database import get_db_connection, initialize_database
from app.frontend_routes import (
    Dimensions,
    PickupRequestCreate,
    Place,
    TimeWindow,
    store_pickup_request,
)

TZ = demo_clock.LOCAL_TZ
rng = random.Random()

STREETS = ["Main St", "State St", "Center St", "Redwood Rd", "700 E", "900 S", "Foothill Dr", "Highland Dr",
           "2100 S", "Union Park Ave", "Fort Union Blvd", "Bangerter Hwy", "University Ave", "Canyon Rd", "Lakeview Dr"]
REGION_SIGMA = {"Salt Lake City": 0.045, "Provo": 0.025, "Ogden": 0.025}  # default 0.018 degrees
REGION_WEIGHT = {"Salt Lake City": 5, "Provo": 2, "Lehi": 2, "Ogden": 2, "Logan": 1, "Park City": 1, "Tooele": 1}
NEIGHBOURS = {  # where cross-town deliveries realistically go
    "Logan": ["Ogden"], "Ogden": ["Logan", "Salt Lake City"], "Salt Lake City": ["Ogden", "Park City", "Tooele", "Lehi"],
    "Park City": ["Salt Lake City"], "Tooele": ["Salt Lake City"], "Lehi": ["Salt Lake City", "Provo"], "Provo": ["Lehi"],
}

FLEET = [  # (region, drivers, capacity per vehicle)
    ("Logan", [("Hannah Cole", "Honda CR-V", 8)]),
    ("Ogden", [("Ben Ortiz", "Ford Transit", 14), ("Tess Nguyen", "Toyota Camry", 4)]),
    ("Salt Lake City", [("Sam Rivera", "Honda Civic", 4), ("Casey Morgan", "Ford F-150", 10), ("Priya Das", "Subaru Outback", 6),
                        ("Luis Vega", "Ford Transit", 14), ("Mei Tan", "Toyota RAV4", 8)]),
    ("Park City", [("Dana Frost", "Subaru Forester", 8)]),
    ("Tooele", [("Ray Holt", "Chevy Silverado", 10)]),
    ("Lehi", [("Jo Park", "Kia Carnival", 9), ("Eli Brandt", "Toyota Tacoma", 8)]),
    ("Provo", [("Nina Lopez", "Honda Odyssey", 9), ("Cam Wells", "Hyundai Elantra", 4)]),
]

SHIPPERS = [  # traditional business shippers: (name, region, orders per day (min, max), products)
    ("Acme Goods", "Salt Lake City", (6, 12), ["Bakery order", "Home goods", "Ceramic tableware"]),
    ("Sugar House Books", "Salt Lake City", (3, 6), ["Book bundle", "Gift box"]),
    ("Wasatch Outfitters", "Park City", (3, 6), ["Ski goggles", "Trail pack", "Camp stove"]),
    ("Cache Valley Creamery", "Logan", (3, 6), ["Cheese crate", "Ice cream tubs"]),
    ("Junction Hardware", "Ogden", (4, 8), ["Tool set", "Paint cans", "Garden hose"]),
    ("Silicon Slopes Supply", "Lehi", (4, 8), ["Monitor", "Desk lamp", "Networking kit"]),
    ("Provo Bakery Co.", "Provo", (4, 8), ["Pastry boxes", "Cake", "Bread crate"]),
    ("Oquirrh Feed & Farm", "Tooele", (2, 4), ["Feed sack", "Garden tools"]),
]

ETSY_PRODUCTS = ["Ceramic vase", "Handmade mugs", "Wall hanging", "Candle set", "Knit blanket", "Print (framed)", "Wooden tray",
                 "Macrame planter", "Leather wallet", "Soap gift box"]
FB_PRODUCTS = ["Side table", "Desk chair", "Standing desk", "Bike", "Bookshelf", "Lamp", "Dresser drawer set", "Toddler bed"]


# ==========================================
# HELPERS
# ==========================================

def parse_day(text: str) -> date:
    today = demo_clock.local_now().date()
    text = text.lower()
    if text == "today":
        return today
    if text == "tomorrow":
        return today + timedelta(days=1)
    if text.startswith("+"):
        return today + timedelta(days=int(text[1:]))
    return date.fromisoformat(text)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=TZ)


def fmt(moment) -> str:
    if isinstance(moment, str):
        moment = datetime.fromisoformat(moment)
    return moment.astimezone(TZ).strftime("%a %b %d %I:%M %p").replace(" 0", " ")


def hhmm(moment) -> str:
    if isinstance(moment, (int, float)):
        moment = datetime.fromtimestamp(moment, tz=timezone.utc)
    if isinstance(moment, str):
        moment = datetime.fromisoformat(moment)
    return moment.astimezone(TZ).strftime("%I:%M%p").lstrip("0").lower()


def snap(lat: float, lon: float) -> tuple[float, float]:
    """Move a random point onto the nearest drivable road using Valhalla /locate."""
    try:
        r = httpx.post(f"{planner.VALHALLA_URL}/locate", json={"locations": [{"lat": lat, "lon": lon}], "costing": "auto"}, timeout=10)
        edge = r.json()[0]["edges"][0]
        return round(edge["correlated_lat"], 6), round(edge["correlated_lon"], 6)
    except Exception:
        return lat, lon


def routable(lat: float, lon: float, region: str) -> bool:
    """True if the road network connects this point to the middle of its region."""
    centre = planner.REGIONS[region]
    try:
        r = httpx.post(f"{planner.VALHALLA_URL}/route", timeout=10, json={
            "locations": [{"lat": lat, "lon": lon}, {"lat": centre[0], "lon": centre[1]}], "costing": "auto"})
        return r.status_code == 200
    except Exception:
        return True


def point_in(region: str) -> tuple[float, float]:
    lat, lon = planner.REGIONS[region]
    sigma = REGION_SIGMA.get(region, 0.018)
    for _ in range(6):  # reject points that snap onto disconnected road fragments
        candidate = snap(round(rng.gauss(lat, sigma), 6), round(rng.gauss(lon, sigma * 1.2), 6))
        if routable(candidate[0], candidate[1], region):
            return candidate
    return candidate


def place_in(region: str, label: str | None = None) -> dict:
    lat, lon = point_in(region)
    address = f"{rng.randint(100, 4999)} {rng.choice(STREETS)}, {region}, UT"
    return {"label": label or region, "address": address, "lat": lat, "lon": lon}


def dims() -> dict:
    return {"length": rng.randint(8, 22), "width": rng.randint(6, 16), "height": rng.randint(4, 14), "unit": "in"}


def pick_region() -> str:
    return rng.choices(list(REGION_WEIGHT), weights=list(REGION_WEIGHT.values()))[0]


def run(query: str, params=None) -> list:
    initialize_database()
    with get_db_connection() as conn:
        return conn.execute(query, params or ()).fetchall()


# ==========================================
# COMMANDS: DATA
# ==========================================

def cmd_wipe(args):
    initialize_database()
    with get_db_connection() as conn:
        for table in ("shipper_orders", "pickup_runs", "seller_pickup_requests", "driver_vehicles",
                      "driver_availability", "driver_routes", "last_minute_requests", "travel_times"):
            conn.execute(f"DELETE FROM {table}")
    demo_clock.reset()
    print("All frontend/dispatch data deleted and clock reset to real time.")


def cmd_world(args):
    initialize_database()
    first = parse_day(args.start)
    created = 0
    with get_db_connection() as conn:
        n = 0
        for region, drivers in FLEET:
            for name, vehicle, capacity in drivers:
                n += 1
                driver_id, vehicle_id = f"demo-driver-{n}", f"demo-vehicle-{n}"
                vdoc = {
                    "id": vehicle_id, "driver_id": driver_id, "vin": f"DEMO{n:013d}", "vin_last4": f"{n:04d}",
                    "plate": f"HT{n:04d}", "plate_state": "UT", "year": 2018 + n % 6, "make": vehicle.split()[0], "model": " ".join(vehicle.split()[1:]),
                    "body_class": "demo", "name": vehicle, "capacity": {"value": capacity, "unit": "standard_package_spots", "source": "demo"},
                }
                conn.execute("INSERT INTO driver_vehicles (id, vin, data) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
                             (vehicle_id, vdoc["vin"], Jsonb(vdoc)))
                home = place_in(region, f"{region} (home)")
                for d in range(args.days):
                    day = first + timedelta(days=d)
                    start = at(day, 8 if n % 2 else 12)
                    end = start + timedelta(hours=6)
                    block_id = f"availability-{driver_id}-{day.isoformat()}"
                    doc = {
                        "id": block_id, "vehicle_id": vehicle_id, "vehicle_name": vehicle, "capacity": vdoc["capacity"],
                        "driver_id": driver_id, "driver_name": name, "start_location": home,
                        "window": {"start": start.isoformat(), "end": end.isoformat()}, "fuel_level_percent": 80,
                        "locks_at": (start - dispatch_engine.LOCK_BEFORE_START).isoformat(), "estimated_fill_percent": 0,
                    }
                    cur = conn.execute(
                        "INSERT INTO driver_availability (id, vehicle_id, start_at, end_at, data) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                        (block_id, vehicle_id, start, end, Jsonb(doc)))
                    created += cur.rowcount
        # the signed-in demo driver also drives each day (skipped if a block already overlaps)
        active = conn.execute("SELECT data FROM driver_vehicles WHERE COALESCE(data->>'driver_id', 'driver-17') = 'driver-17' LIMIT 1").fetchone()
        if not active:  # the driver the UI is signed in as needs a vehicle to schedule against
            vdoc = {
                "id": "vehicle-demo-rav4", "vin": "2T3W1RFV5KW012345", "vin_last4": "2345", "plate": "8ABC123", "plate_state": "UT",
                "year": 2019, "make": "Toyota", "model": "RAV4", "body_class": "Sport Utility Vehicle (SUV)/Multi-Purpose Vehicle (MPV)",
                "name": "2019 Toyota RAV4", "capacity": {"value": 8, "unit": "standard_package_spots", "source": "estimated_from_body_class"},
            }
            conn.execute("INSERT INTO driver_vehicles (id, vin, data) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING", (vdoc["id"], vdoc["vin"], Jsonb(vdoc)))
            active = (vdoc,)
        if active:
            vehicle = active[0]
            for d in range(args.days):
                day = first + timedelta(days=d)
                start, end = at(day, 8), at(day, 14)
                clash = conn.execute(
                    "SELECT 1 FROM driver_availability WHERE COALESCE(data->>'driver_id', 'driver-17') = 'driver-17' AND start_at < %s AND end_at > %s",
                    (end, start)).fetchone()
                if clash:
                    continue
                block_id = f"availability-driver-17-{day.isoformat()}"
                doc = {
                    "id": block_id, "vehicle_id": vehicle["id"], "vehicle_name": vehicle["name"], "capacity": vehicle["capacity"],
                    "start_location": place_in("Salt Lake City", "Sugar House"), "window": {"start": start.isoformat(), "end": end.isoformat()},
                    "fuel_level_percent": 75, "locks_at": (start - dispatch_engine.LOCK_BEFORE_START).isoformat(), "estimated_fill_percent": 0,
                }
                conn.execute(
                    "INSERT INTO driver_availability (id, vehicle_id, start_at, end_at, data) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                    (block_id, vehicle["id"], start, end, Jsonb(doc)))
                created += 1
    print(f"World ready: {n} regional drivers, {created} availability blocks over {args.days} day(s) from {first}.")


def cmd_business(args):
    day = parse_day(args.day)
    created_at = demo_clock.now()
    made = 0
    with get_db_connection() as conn:
        for name, region, (low, high), products in SHIPPERS:
            count = args.count if args.count else rng.randint(low, high)
            if args.shipper and args.shipper.lower() not in name.lower():
                continue
            home = place_in(region, name)  # one warehouse per shipper per day
            for i in range(count):
                dest_region = region if rng.random() < 0.85 else rng.choice(NEIGHBOURS[region])
                ready_from = rng.choice([8, 9, 10, 11])
                oid = f"ORD-{day:%m%d}-{sum(map(ord, name)) % 90 + 10}{i:02d}"
                doc = {
                    "id": oid, "source": "direct", "shipper_name": name, "customer_name": f"Customer {rng.randint(1000, 9999)}",
                    "product_name": rng.choice(products), "quantity": rng.choice([1, 1, 1, 2]), "dimensions": dims(),
                    "pickup": home, "delivery": place_in(dest_region), "fulfillment_type": "local_driver", "status": "unassigned",
                    "order_date": created_at.isoformat(), "created_at": created_at.isoformat(),
                    "ready_window": {"start": at(day, ready_from).isoformat(), "end": at(day, 17, 30).isoformat()},
                    "pickup_run_id": None, "driver_id": None, "driver_name": None, "pickup_window": None,
                }
                cur = conn.execute(
                    "INSERT INTO shipper_orders (id, order_date, fulfillment_type, data) VALUES (%s, %s, 'local_driver', %s) ON CONFLICT (id) DO NOTHING",
                    (oid, created_at, Jsonb(doc)))
                made += cur.rowcount
    print(f"{made} traditional-shipper orders queued for {day} (booked at {fmt(created_at)}).")
    if not args.no_plan:
        _print_events(dispatch_engine.tick(finalize=False))


def cmd_etsy(args):
    day = parse_day(args.day)
    created_at = demo_clock.now()
    made = []
    for _ in range(args.count):
        region = args.region or pick_region()
        source = "facebook_marketplace" if rng.random() < args.facebook_share else "etsy"
        pickup = place_in(region, f"{rng.choice(['Sunset', 'Maple', 'Juniper', 'Cedar', 'Willow'])} {rng.choice(['Studio', 'Shop', 'Home'])}")
        dest_region = region if rng.random() < 0.7 else rng.choice(NEIGHBOURS[region])
        dest = place_in(dest_region, "Buyer" if source == "facebook_marketplace" else "Carrier drop-off")
        if args.same_day:
            start = created_at + timedelta(hours=rng.randint(1, 3))
            window = (start, start + timedelta(hours=3))
        else:
            hour = rng.choice([9, 10, 12, 13, 14])
            window = (at(day, hour), at(day, hour) + timedelta(hours=rng.choice([2, 3, 4])))
        body = PickupRequestCreate(
            source=source, product_name=rng.choice(ETSY_PRODUCTS if source == "etsy" else FB_PRODUCTS), quantity=rng.choice([1, 1, 2]),
            dimensions=Dimensions(**dims()), notes="demo", pickup=Place(**pickup), destination=Place(**dest),
            available_window=TimeWindow(start=window[0], end=window[1]),
        )
        made.append(store_pickup_request(body, created_at))
    print(f"{len(made)} seller pickup request(s) booked for {'SAME DAY' if args.same_day else day} (booked at {fmt(created_at)}).")
    _print_events(dispatch_engine.tick(finalize=False))
    for doc in made[: args.show]:
        _print_placement(doc["id"], brief=True)


def cmd_scenario(args):
    cmd_wipe(args)
    # Start the story at 10 AM so tomorrow's routes are still weeks of notice away from locking.
    demo_clock.set_to(at(demo_clock.local_now().date(), 10))
    print(f"Demo clock set to {fmt(demo_clock.now())}.")
    args.start, args.days = "tomorrow", 3
    cmd_world(args)
    for offset in ("tomorrow", "+2"):
        args.day, args.count, args.shipper, args.no_plan = offset, None, None, True
        cmd_business(args)
    args.day, args.count, args.same_day, args.facebook_share, args.show, args.region = "tomorrow", 12, False, 0.3, 4, None
    cmd_etsy(args)
    print("\nNext: `report`, then `advance --hours 12` and `report` again, or `trace <id>`.")


# ==========================================
# COMMANDS: DISPATCH + TIME
# ==========================================

def _print_events(events: list[str]) -> None:
    for e in events:
        print(f"  {e}")


def cmd_tick(args):
    _print_events(dispatch_engine.tick())


def cmd_advance(args):
    start = demo_clock.now()
    if args.to:
        target = datetime.fromisoformat(args.to).replace(tzinfo=TZ)
    else:
        target = start + timedelta(hours=args.hours or 0, days=args.days or 0)
    if target <= start:
        raise SystemExit("Target time must be after the current demo time.")
    print(f"Demo clock: {fmt(start)}  →  {fmt(target)}")
    t = start
    while True:
        nxt = dispatch_engine.next_event_after(t, target)
        if nxt is None:
            break
        print(f"\n● {fmt(nxt)}")
        _print_events(dispatch_engine.tick(nxt) or ["(nothing to do)"])
        t = nxt
    demo_clock.set_to(target)
    print(f"\n● {fmt(target)}  (now)")
    _print_events(dispatch_engine.tick(target) or ["(nothing to do)"])


def cmd_reset_clock(args):
    demo_clock.reset()
    print("Clock back to real time.")


# ==========================================
# REPORTING
# ==========================================

def _route_table(label: str, rec_doc: dict, stops: list[dict], highlight: set[str], note: str) -> None:
    driver = rec_doc.get("driver_name") or dispatch_engine.DEFAULT_DRIVER_NAME
    start = rec_doc["window"]["start"]
    print(f"\n{label}  {fmt(start)}–{hhmm(rec_doc['window']['end'])}  {driver} · {rec_doc.get('vehicle_name')} ({rec_doc['capacity']['value']} spots)")
    print(f"   {note}")
    home = rec_doc["start_location"]
    print(f"   start    {home.get('label') or home.get('address')}")
    for s in stops:
        mark = "★" if highlight & set(s["order_ids"]) else " "
        kind = "PICKUP " if s["type"] == "pickup" else "DROP   "
        print(f" {mark} {s['n']:>2}. {kind} {hhmm(s['eta']):>8}  {s['label'].split(' · ', 1)[-1][:32]:<32} {', '.join(i[-9:] for i in s['order_ids'])}")


def _day_blocks(day: date) -> list[tuple]:
    lo, hi = at(day, 0), at(day, 0) + timedelta(days=1)
    return run("SELECT id, data, start_at, route_id FROM driver_availability WHERE start_at >= %s AND start_at < %s ORDER BY start_at, id", (lo, hi))


def cmd_report(args, highlight: set[str] | None = None, day: date | None = None, only_block: str | None = None):
    day = day or parse_day(args.day)
    highlight = highlight or set()
    blocks = [b for b in _day_blocks(day) if not only_block or b[0] == only_block]
    routes = {rid: doc for rid, doc in run("SELECT id, data FROM driver_routes")}
    placed = drive = 0
    cross = 0
    longest_hop = 0.0
    print(f"\n=== ROUTES FOR {day:%A %b %d} (demo time now {fmt(demo_clock.now())}) ===")
    for block_id, doc, start_at, route_id in blocks:
        if route_id:
            route = routes[route_id]
            stops = [
                {"n": n, "type": s["type"], "label": s["label"], "eta": s["eta"], "order_ids": s["order_ids"]}
                for n, s in enumerate((s for s in route["stops"] if s["type"] != "start"), start=1)
            ]
            note = (f"FINALIZED {fmt(route['finalized_at'])} ({round((start_at - datetime.fromisoformat(route['finalized_at'])).total_seconds() / 3600, 1)}h before start) · "
                    f"{route['drive_minutes']} min driving · {route['fill_percent']}% full · payout ${route['receipt']['total']:.2f}")
            drive += route["drive_minutes"]
        else:
            prov = doc.get("provisional")
            if not prov:
                if args.all:
                    print(f"\n{block_id}  {fmt(start_at)}  {doc.get('driver_name', dispatch_engine.DEFAULT_DRIVER_NAME)}: no packages")
                continue
            stops = prov["stops"]
            note = (f"PROVISIONAL (finalizes {fmt(datetime.fromisoformat(doc['locks_at']) - dispatch_engine.FINALIZE_MARGIN)}) · "
                    f"{prov['drive_minutes']} min driving · {prov['fill_percent']}% full")
            drive += prov["drive_minutes"]
        n_pk = sum(len(s["order_ids"]) for s in stops if s["type"] == "pickup")
        placed += n_pk
        regions = {planner.nearest_region((float(s.get("lat", 0)), float(s.get("lon", 0)))) for s in route["stops"]} if route_id else set()
        if route_id:
            pts = [(float(s["lat"]), float(s["lon"])) for s in route["stops"]]
            planner.MATRIX.ensure(pts)
            hops = [planner.MATRIX.sec(a, b) / 60 for a, b in zip(pts, pts[1:])]
            longest_hop = max([longest_hop] + hops)
        if len(regions) > 2:
            cross += 1
        _route_table(route_id or block_id, doc, stops, highlight, note + (f" · regions: {', '.join(sorted(regions))}" if regions else ""))

    queue = run("SELECT count(*) FROM shipper_orders WHERE data->>'status' IN ('unassigned','pending') AND fulfillment_type='local_driver'")[0][0] \
        + run("SELECT count(*) FROM seller_pickup_requests WHERE data->>'status' = 'awaiting_driver'")[0][0]
    print(f"\nSummary: {placed} packages on {sum(1 for b in blocks if b[3] or b[1].get('provisional'))} routes · {drive} total driving minutes"
          f"{f' ({drive / placed:.1f} min/package)' if placed else ''} · longest hop between consecutive stops {longest_hop:.0f} min · {cross} route(s) touching 3+ regions · {queue} package(s) still in queue overall")


def _find_doc(pkg_id: str):
    for table in ("shipper_orders", "seller_pickup_requests"):
        rows = run(f"SELECT data FROM {table} WHERE id = %s", (pkg_id,))
        if rows:
            return table, rows[0][0]
    return None, None


def _print_placement(pkg_id: str, brief: bool = False) -> None:
    table, doc = _find_doc(pkg_id)
    if not doc:
        print(f"  {pkg_id}: not found")
        return
    name = doc.get("product_name")
    if doc.get("route_id"):
        n_p, n_d = doc["pickup_stop_id"].removeprefix("stop-"), doc["dropoff_stop_id"].removeprefix("stop-")
        print(f"  {pkg_id} ({name}): ASSIGNED to {doc['route_id']} · {doc['driver_name']} · pickup stop {int(n_p) - 1} "
              f"{hhmm((doc.get('confirmed_pickup_window') or doc.get('pickup_window'))['start'])} · drop-off stop {int(n_d) - 1} {hhmm(doc['dropoff_eta'])}")
    elif doc.get("provisional"):
        p = doc["provisional"]
        print(f"  {pkg_id} ({name}): QUEUED → will land on {p['driver_name']}'s route (block {p['availability_id']}) as pickup stop {p['pickup_stop']}/{p['total_stops']} "
              f"≈{hhmm(p['pickup_eta'])} and drop-off stop {p['dropoff_stop']}/{p['total_stops']} ≈{hhmm(p['dropoff_eta'])}; route finalizes {fmt(p['finalizes_at'])}")
    elif doc.get("last_minute_request_id"):
        print(f"  {pkg_id} ({name}): no open route can take it → LAST-MINUTE request {doc['last_minute_request_id']}")
    else:
        print(f"  {pkg_id} ({name}): in the queue, status {doc.get('status')} (no route can take it yet)")


def cmd_trace(args):
    table, doc = _find_doc(args.id)
    if not doc:
        raise SystemExit(f"{args.id} not found")
    print(f"\n{args.id} — {doc.get('product_name')} ({doc.get('shipper_name') or doc.get('source')}), booked {fmt(doc['created_at'])}")
    _print_placement(args.id)
    block_id = doc.get("availability_id") or (doc.get("provisional") or {}).get("availability_id")
    if block_id:
        start_at = run("SELECT start_at FROM driver_availability WHERE id = %s", (block_id,))[0][0]
        args.all = False
        cmd_report(args, {args.id}, start_at.astimezone(TZ).date(), only_block=block_id)


def cmd_lastminute(args):
    rows = run("SELECT data FROM last_minute_requests WHERE status = 'open' ORDER BY created_at")
    print(f"{len(rows)} open last-minute request(s):")
    for (doc,) in rows:
        print(f"  {doc['id']}  {doc['shipper_name']} @ {doc['pickup'].get('label')}  {doc['package_count']} pkg / {doc['package_spots']} spots  "
              f"pickup {fmt(doc['pickup_window']['start'])}–{hhmm(doc['pickup_window']['end'])}  reason={doc['reason']}")


def cmd_status(args):
    now = demo_clock.now()
    print(f"Demo time: {fmt(now)} (real time + {demo_clock.offset_seconds() / 3600:.1f}h)")
    for title, query in [
        ("traditional orders waiting", "SELECT count(*) FROM shipper_orders WHERE data->>'status' IN ('unassigned','pending') AND fulfillment_type='local_driver'"),
        ("seller pickups waiting", "SELECT count(*) FROM seller_pickup_requests WHERE data->>'status'='awaiting_driver'"),
        ("blocks without a route", "SELECT count(*) FROM driver_availability WHERE route_id IS NULL AND end_at > NOW()"),
        ("finalized routes", "SELECT count(*) FROM driver_routes"),
        ("open last-minute requests", "SELECT count(*) FROM last_minute_requests WHERE status='open'"),
    ]:
        print(f"  {title:<28} {run(query)[0][0]}")
    print(f"  valhalla matrix pairs        {planner.MATRIX.valhalla_pairs} real / {planner.MATRIX.estimated_pairs} estimated")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.demo", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("wipe").set_defaults(fn=cmd_wipe)
    sub.add_parser("scenario").set_defaults(fn=cmd_scenario)
    sub.add_parser("tick").set_defaults(fn=cmd_tick)
    sub.add_parser("lastminute").set_defaults(fn=cmd_lastminute)
    sub.add_parser("reset-clock").set_defaults(fn=cmd_reset_clock)

    p = sub.add_parser("world")
    p.add_argument("--days", type=int, default=3)
    p.add_argument("--start", default="tomorrow")
    p.set_defaults(fn=cmd_world)

    p = sub.add_parser("business")
    p.add_argument("--day", default="tomorrow")
    p.add_argument("-n", "--count", type=int, help="orders per shipper (default: realistic range)")
    p.add_argument("--shipper", help="only shippers whose name contains this")
    p.add_argument("--no-plan", action="store_true")
    p.set_defaults(fn=cmd_business)

    p = sub.add_parser("etsy")
    p.add_argument("--day", default="tomorrow")
    p.add_argument("-n", "--count", type=int, default=5)
    p.add_argument("--same-day", action="store_true", help="pickup window starts in 1-3 hours")
    p.add_argument("--facebook-share", type=float, default=0.0, help="fraction booked as Facebook Marketplace")
    p.add_argument("--show", type=int, default=5, help="print placement for the first N")
    p.add_argument("--region", choices=list(planner.REGIONS), help="force sellers into one region")
    p.set_defaults(fn=cmd_etsy)

    p = sub.add_parser("advance")
    p.add_argument("--hours", type=float)
    p.add_argument("--days", type=float)
    p.add_argument("--to", help="local time, e.g. '2026-11-08 07:00'")
    p.set_defaults(fn=cmd_advance)

    p = sub.add_parser("report")
    p.add_argument("--day", default="tomorrow")
    p.add_argument("--all", action="store_true", help="include blocks with no packages")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("trace")
    p.add_argument("id")
    p.set_defaults(fn=cmd_trace)

    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
