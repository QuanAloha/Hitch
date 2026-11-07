"""Dispatch engine: turns the package queue into finalized driver routes.

Each `tick(now)`:
  1. completes routes whose shift has ended (demo time moves forward),
  2. loads every queued package (seller pickup requests + traditional-shipper
     orders) and every driver block that has no route yet,
  3. plans them together with `planner.plan` (Valhalla travel times, clustered),
  4. FINALIZES each block whose commit time has arrived: the route, its stops,
     pickup windows and payout are written, and the orders are assigned,
  5. saves a provisional placement for blocks still forming (so you can see
     where a freshly booked pickup will land before the route locks),
  6. opens "last-minute" requests for packages no open block can take.

Timing (the 12 hour promise): a block locks for the driver at `start - 12h`
(`locks_at`). The engine commits it FINALIZE_MARGIN earlier, so the finished
route already exists when the lock moment arrives. That makes the effective
intake cutoff `start - 12h30m`; anything booked later goes to last-minute.
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from psycopg.types.json import Jsonb

from app import demo_clock, planner
from app.database import get_db_connection, initialize_database

DEFAULT_DRIVER_ID = "driver-17"
DEFAULT_DRIVER_NAME = "Jordan Lee"
LOCK_BEFORE_START = timedelta(hours=12)
FINALIZE_MARGIN = timedelta(minutes=30)
LAST_MINUTE_HORIZON = timedelta(hours=24)
BUSINESS_DAY = (8, 18)  # local hours a traditional shipper's orders are ready
ADVISORY_LOCK = 727001


# ==========================================
# RECORDS
# ==========================================

@dataclass
class BlockRec:
    id: str
    doc: dict[str, Any]
    start_at: datetime
    end_at: datetime
    route_id: Optional[str]

    @property
    def locks_at(self) -> datetime:
        return datetime.fromisoformat(self.doc["locks_at"])

    @property
    def commit_at(self) -> datetime:
        return self.locks_at - FINALIZE_MARGIN

    @property
    def driver_id(self) -> str:
        return self.doc.get("driver_id") or DEFAULT_DRIVER_ID

    @property
    def driver_name(self) -> str:
        return self.doc.get("driver_name") or DEFAULT_DRIVER_NAME


@dataclass
class PkgRec:
    pkg: planner.Pkg
    table: str                      # "shipper_orders" | "seller_pickup_requests"
    doc: dict[str, Any]
    shipper: str
    product: str
    created_at: datetime


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


def _epoch(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


def _pt(place: dict) -> planner.Point:
    return (float(place["lat"]), float(place["lon"]))


def _info(place: dict) -> dict:
    return {k: place.get(k) for k in ("label", "address", "lat", "lon")}


SOURCE_NAMES = {"etsy": "Etsy seller", "facebook_marketplace": "Marketplace seller", "direct": "Direct"}


def _day_window(moment: datetime) -> tuple[float, float]:
    local = moment.astimezone(demo_clock.LOCAL_TZ)
    day = local.date()
    start = datetime(day.year, day.month, day.day, BUSINESS_DAY[0], tzinfo=demo_clock.LOCAL_TZ)
    end = datetime(day.year, day.month, day.day, BUSINESS_DAY[1], tzinfo=demo_clock.LOCAL_TZ)
    return start.timestamp(), end.timestamp()


def pkg_from_order(doc: dict, order_date: datetime) -> Optional[PkgRec]:
    if not doc.get("pickup") or not doc.get("delivery"):
        return None
    window = doc.get("ready_window")
    ready = (_epoch(window["start"]), _epoch(window["end"])) if window else _day_window(order_date)
    created = datetime.fromisoformat(doc.get("created_at") or order_date.isoformat())
    pkg = planner.Pkg(
        id=doc["id"], spots=planner.package_spots(doc.get("dimensions") or {"length": 12, "width": 10, "height": 8, "unit": "in"}, doc.get("quantity", 1)),
        pick=_pt(doc["pickup"]), drop=_pt(doc["delivery"]), ready_start=ready[0], ready_end=ready[1],
        pick_info=_info(doc["pickup"]), drop_info=_info(doc["delivery"]),
        reserved_block=doc.get("reserved_availability_id"),
    )
    shipper = doc.get("shipper_name") or doc["pickup"].get("label") or "Shipper"
    return PkgRec(pkg, "shipper_orders", doc, shipper, doc.get("product_name", "Order"), created)


def pkg_from_request(doc: dict) -> PkgRec:
    window = doc["available_window"]
    pkg = planner.Pkg(
        id=doc["id"], spots=planner.package_spots(doc["dimensions"], doc.get("quantity", 1)),
        pick=_pt(doc["pickup"]), drop=_pt(doc["destination"]),
        ready_start=_epoch(window["start"]), ready_end=_epoch(window["end"]),
        pick_info=_info(doc["pickup"]), drop_info=_info(doc["destination"]),
        reserved_block=doc.get("reserved_availability_id"),
    )
    shipper = doc.get("seller_name") or SOURCE_NAMES.get(doc.get("source", ""), "Seller")
    return PkgRec(pkg, "seller_pickup_requests", doc, shipper, doc.get("product_name", "Package"), datetime.fromisoformat(doc["created_at"]))


# ==========================================
# LOADING
# ==========================================

def load_blocks(conn, now: datetime) -> list[BlockRec]:
    rows = conn.execute(
        "SELECT id, data, start_at, end_at, route_id FROM driver_availability "
        "WHERE route_id IS NULL AND end_at > %s AND COALESCE(data->>'no_work', 'false') = 'false' ORDER BY start_at",
        (now,),
    ).fetchall()
    return [BlockRec(r[0], r[1], r[2], r[3], r[4]) for r in rows]


def load_queue(conn, now: datetime) -> list[PkgRec]:
    """Everything waiting for a driver, created by `now`, still pickup-able."""
    records: list[PkgRec] = []
    for order_id, order_date, doc in conn.execute(
        "SELECT id, order_date, data FROM shipper_orders WHERE fulfillment_type = 'local_driver' "
        "AND data->>'status' IN ('unassigned', 'pending') AND COALESCE(data->>'pickup_run_id', '') = ''"
    ).fetchall():
        rec = pkg_from_order(doc, order_date)
        if rec:
            records.append(rec)
    for (doc,) in conn.execute(
        "SELECT data FROM seller_pickup_requests WHERE data->>'status' = 'awaiting_driver'"
    ).fetchall():
        records.append(pkg_from_request(doc))
    return [r for r in records if r.created_at <= now and r.pkg.ready_end > now.timestamp()]


def fetch_pkgrecs(conn, ids: list[str]) -> dict[str, PkgRec]:
    """Records for specific packages regardless of status (route amendments)."""
    found: dict[str, PkgRec] = {}
    for pkg_id, order_date, doc in conn.execute(
        "SELECT id, order_date, data FROM shipper_orders WHERE id = ANY(%s)", (ids,)
    ).fetchall():
        rec = pkg_from_order(doc, order_date)
        if rec:
            found[pkg_id] = rec
    for (doc,) in conn.execute("SELECT data FROM seller_pickup_requests WHERE id = ANY(%s)", (ids,)).fetchall():
        found[doc["id"]] = pkg_from_request(doc)
    return found


def _planner_block(rec: BlockRec) -> planner.Blk:
    place = rec.doc["start_location"]
    return planner.Blk(
        id=rec.id, start=_pt(place), t0=rec.start_at.timestamp(), t1=rec.end_at.timestamp(),
        capacity=int(rec.doc["capacity"]["value"]),
    )


# ==========================================
# PERSISTING PLANS
# ==========================================

def _save(conn, table: str, doc: dict) -> None:
    conn.execute(f"UPDATE {table} SET data = %s WHERE id = %s", (Jsonb(doc), doc["id"]))


def _stop_label(kind: str, stop: planner.BuiltStop) -> str:
    place = stop.pkgs[0].pick_info if kind == "pickup" else stop.pkgs[0].drop_info
    name = place.get("label") or place.get("address") or "Stop"
    return f"{'Pickup' if kind == 'pickup' else 'Drop-off'} · {name}"


def _built_index(built: list[planner.BuiltStop]) -> dict[str, dict[str, int]]:
    """pkg id -> {"pickup": n, "dropoff": n} using 1-based numbering (start = 0)."""
    index: dict[str, dict[str, int]] = {}
    for n, stop in enumerate(built, start=1):
        for pkg in stop.pkgs:
            index.setdefault(pkg.id, {})[stop.kind] = n
    return index


def _provisional_for(blk: planner.Blk, rec: BlockRec) -> dict:
    built = planner.build_stops(blk)
    fill = round(100 * blk.timeline.peak_load / blk.capacity) if blk.timeline else 0
    return {
        "package_ids": [p.id for p in blk.pkgs],
        "stops": [
            {"n": n, "type": s.kind, "label": _stop_label(s.kind, s), "eta": _iso(s.window_start), "order_ids": [p.id for p in s.pkgs]}
            for n, s in enumerate(built, start=1)
        ],
        "fill_percent": fill,
        "drive_minutes": round(planner.drive_seconds(blk) / 60),
    }


def _commit_block(conn, rec: BlockRec, blk: planner.Blk, recs: dict[str, PkgRec], now: datetime, amending: bool = False) -> str:
    """Write the route, assign every package on it, and (re)create pickup runs."""
    built = planner.build_stops(blk)
    route_id = rec.route_id or f"route-{rec.id.removeprefix('availability-')}"
    fill = round(100 * blk.timeline.peak_load / blk.capacity) if blk.timeline else 0

    stops = [{
        "id": "stop-1", "type": "start", "label": f"Start · {rec.doc['start_location'].get('label') or rec.doc['start_location'].get('address')}",
        "address": rec.doc["start_location"].get("address"), "lat": blk.start[0], "lon": blk.start[1],
        "order_ids": [], "instructions": None, "completed_at": None,
    }]
    windows = [{"stop_id": "stop-1", "start": rec.start_at.isoformat(), "end": rec.start_at.isoformat()}]
    placement: dict[str, dict[str, Any]] = {}
    for n, s in enumerate(built, start=2):
        stop_id = f"stop-{n}"
        place = s.pkgs[0].pick_info if s.kind == "pickup" else s.pkgs[0].drop_info
        stops.append({
            "id": stop_id, "type": s.kind, "label": _stop_label(s.kind, s), "address": place.get("address"),
            "lat": s.pt[0], "lon": s.pt[1], "order_ids": [p.id for p in s.pkgs],
            "instructions": f"{len(s.pkgs)} packages at this stop" if len(s.pkgs) > 1 else None,
            "eta": _iso(s.window_start), "completed_at": None,
        })
        windows.append({"stop_id": stop_id, "start": _iso(s.window_start), "end": _iso(s.window_end)})
        for pkg in s.pkgs:
            placement.setdefault(pkg.id, {})[s.kind] = (stop_id, s.window_start, s.window_end)

    receipt_lines = []
    for pkg in blk.pkgs:
        r = recs[pkg.id]
        receipt_lines.append({"order_id": pkg.id, "shipper": r.shipper, "item": f"{r.product} · {pkg.id}", "amount": planner.payout(pkg)})

    route_doc = {
        "id": route_id, "availability_id": rec.id, "stops": stops, "windows": windows,
        "geometry": None,  # filled from Valhalla on first read
        "fill_percent": fill, "confirmed_package_spots": blk.timeline.peak_load if blk.timeline else 0,
        "drive_minutes": round(planner.drive_seconds(blk) / 60), "finalized_at": now.isoformat(),
        "receipt": {"lines": receipt_lines, "total": round(sum(l["amount"] for l in receipt_lines), 2)},
        "fuel_stops": [], "sequence": [{"kind": s.kind, "pkg": s.pkg.id} for s in blk.seq],
    }

    exists = conn.execute("SELECT 1 FROM driver_routes WHERE id = %s", (route_id,)).fetchone()
    if exists:
        conn.execute("UPDATE driver_routes SET data = %s WHERE id = %s", (Jsonb(route_doc), route_id))
    else:
        conn.execute("INSERT INTO driver_routes (id, availability_id, data) VALUES (%s, %s, %s)", (route_id, rec.id, Jsonb(route_doc)))

    avail = dict(rec.doc)
    avail.pop("provisional", None)
    avail.update({"estimated_fill_percent": fill, "finalized_at": now.isoformat()})
    conn.execute("UPDATE driver_availability SET route_id = %s, data = %s WHERE id = %s", (route_id, Jsonb(avail), rec.id))

    # pickup runs: one per pickup stop, so shippers see what to have ready
    conn.execute("DELETE FROM pickup_runs WHERE data->>'route_id' = %s", (route_id,))
    run_for_stop: dict[str, str] = {}
    for stop, s in zip(stops[1:], built):
        if s.kind != "pickup":
            continue
        run_id = f"run-{route_id.removeprefix('route-')}-{stop['id'].removeprefix('stop-')}"
        run_for_stop[stop["id"]] = run_id
        local_date = datetime.fromtimestamp(s.window_start, tz=demo_clock.LOCAL_TZ).date()
        place = s.pkgs[0].pick_info
        conn.execute(
            "INSERT INTO pickup_runs (id, run_date, data) VALUES (%s, %s, %s)",
            (run_id, local_date, Jsonb({
                "id": run_id, "label": f"Pickup run · {place.get('label') or place.get('address')}", "date": local_date.isoformat(),
                "driver_id": rec.driver_id, "driver_name": rec.driver_name, "vehicle_name": rec.doc.get("vehicle_name"),
                "pickup_window": {"start": _iso(s.window_start), "end": _iso(s.window_end)},
                "order_ids": [p.id for p in s.pkgs], "route_id": route_id, "stop_id": stop["id"],
            })),
        )

    for pkg in blk.pkgs:
        r = recs[pkg.id]
        pick_stop, p_start, p_end = placement[pkg.id]["pickup"]
        drop_stop, d_start, _ = placement[pkg.id]["dropoff"]
        doc = r.doc
        for stale in ("provisional", "reserved_availability_id"):
            doc.pop(stale, None)
        window = {"start": _iso(p_start), "end": _iso(p_end)}
        doc.update({
            "status": "assigned", "pickup_run_id": run_for_stop[pick_stop], "driver_id": rec.driver_id, "driver_name": rec.driver_name,
            "route_id": route_id, "availability_id": rec.id, "pickup_stop_id": pick_stop, "dropoff_stop_id": drop_stop,
            "dropoff_eta": _iso(d_start), "assigned_at": now.isoformat(),
        })
        doc["confirmed_pickup_window" if r.table == "seller_pickup_requests" else "pickup_window"] = window
        _save(conn, r.table, doc)
    return route_id


# ==========================================
# LAST-MINUTE POOL
# ==========================================

def _group_key(rec: PkgRec) -> str:
    local_day = datetime.fromtimestamp(rec.pkg.ready_start, tz=demo_clock.LOCAL_TZ).date().isoformat()
    return f"{round(rec.pkg.pick[0], 4)},{round(rec.pkg.pick[1], 4)}|{local_day}"


def _lm_doc(group: list[PkgRec], key: str, now: datetime) -> dict:
    first = group[0]
    reason = "same_day" if any((r.pkg.ready_start - r.created_at.timestamp()) < (LOCK_BEFORE_START + FINALIZE_MARGIN).total_seconds() for r in group) else "capacity"
    return {
        "status": "open", "group_key": key, "reason": reason, "shipper_name": first.shipper,
        "pickup": first.pkg.pick_info,
        "pickup_window": {"start": _iso(min(r.pkg.ready_start for r in group)), "end": _iso(max(r.pkg.ready_end for r in group))},
        "package_ids": [r.pkg.id for r in group], "package_count": len(group), "package_spots": sum(r.pkg.spots for r in group),
        "packages": [{"id": r.pkg.id, "product_name": r.product, "spots": r.pkg.spots, "destination": r.pkg.drop_info} for r in group],
        "created_at": now.isoformat(),
    }


def refresh_last_minute(conn, now: datetime, unplaced: list[PkgRec]) -> list[str]:
    events: list[str] = []
    horizon = (now + LAST_MINUTE_HORIZON).timestamp()
    at_risk = [r for r in unplaced if r.pkg.ready_start <= horizon]

    open_rows = conn.execute("SELECT id, data FROM last_minute_requests WHERE status = 'open'").fetchall()
    open_by_key = {doc["group_key"]: (rid, doc) for rid, doc in open_rows}

    groups: dict[str, list[PkgRec]] = {}
    for rec in at_risk:
        groups.setdefault(_group_key(rec), []).append(rec)

    for key, group in groups.items():
        if key in open_by_key:
            rid, doc = open_by_key.pop(key)
            fresh = _lm_doc(group, key, now)
            fresh.update({"id": rid, "created_at": doc["created_at"], "reason": doc["reason"]})
            doc = fresh
            conn.execute("UPDATE last_minute_requests SET data = %s WHERE id = %s", (Jsonb(doc), rid))
        else:
            rid = f"lmr-{hashlib.sha1(f'{key}{uuid.uuid4()}'.encode()).hexdigest()[:8]}"
            doc = {**_lm_doc(group, key, now), "id": rid}
            conn.execute("INSERT INTO last_minute_requests (id, status, created_at, data) VALUES (%s, 'open', %s, %s)", (rid, now, Jsonb(doc)))
            events.append(f"LAST-MINUTE  {doc['shipper_name']}: {len(group)} package(s) need a driver ({doc['reason']})")
        for rec in group:
            if rec.doc.get("last_minute_request_id") != rid:
                rec.doc["last_minute_request_id"] = rid
                _save(conn, rec.table, rec.doc)

    # open requests whose packages got placed (or whose window passed) are done
    for rid, doc in open_by_key.values():
        doc["status"] = "resolved"
        conn.execute("UPDATE last_minute_requests SET status = 'resolved', data = %s WHERE id = %s", (Jsonb(doc), rid))
        events.append(f"RESOLVED     last-minute request {rid} ({doc['shipper_name']}) now covered or past")
    return events


# ==========================================
# TICK
# ==========================================

def complete_routes(conn, now: datetime) -> list[str]:
    """Routes whose shift is over: mark stops done and orders delivered."""
    events = []
    rows = conn.execute(
        "SELECT r.id, r.data, a.data, a.end_at, a.start_at FROM driver_routes r JOIN driver_availability a ON a.id = r.availability_id "
        "WHERE a.end_at <= %s AND COALESCE(r.data->>'completed_at', '') = ''", (now,)
    ).fetchall()
    for route_id, route_doc, avail_doc, end_at, start_at in rows:
        route_doc["started_at"] = route_doc.get("started_at") or start_at.isoformat()
        for stop in route_doc["stops"]:
            stop["completed_at"] = stop.get("completed_at") or stop.get("eta") or start_at.isoformat()
        route_doc["completed_at"] = end_at.isoformat()
        conn.execute("UPDATE driver_routes SET data = %s WHERE id = %s", (Jsonb(route_doc), route_id))
        ids = [i for s in route_doc["stops"] for i in s["order_ids"]]
        for rec in fetch_pkgrecs(conn, ids).values():
            rec.doc["status"] = "delivered"
            _save(conn, rec.table, rec.doc)
        events.append(f"COMPLETED    {route_id}: {len(set(ids))} package(s) delivered")
    return events


def plan_and_commit(conn, now: datetime, finalize: bool = True) -> list[str]:
    events: list[str] = []
    blocks = load_blocks(conn, now)
    queue = load_queue(conn, now)
    blks = [_planner_block(b) for b in blocks]
    by_pkg = {r.pkg.id: r for r in queue}
    for rec in queue:  # reservations only count while the block is still open
        if rec.pkg.reserved_block and rec.pkg.reserved_block not in {b.id for b in blocks}:
            rec.pkg.reserved_block = None
    unplaced_pkgs = planner.plan(blks, [r.pkg for r in queue])

    committed: set[str] = set()
    placed_provisionally: set[str] = set()
    for rec, blk in zip(blocks, blks):
        if finalize and rec.commit_at <= now:
            if blk.seq:
                route_id = _commit_block(conn, rec, blk, by_pkg, now)
                committed.update(p.id for p in blk.pkgs)
                events.append(
                    f"FINALIZED   {route_id}: {rec.driver_name}, {len(blk.pkgs)} package(s), "
                    f"{round(planner.drive_seconds(blk) / 60)} min driving, {round(100 * blk.timeline.peak_load / blk.capacity)}% full "
                    f"(driver notified {round((rec.start_at - now).total_seconds() / 3600, 1)}h before start)"
                )
            else:
                rec.doc["no_work"] = True
                conn.execute("UPDATE driver_availability SET data = %s WHERE id = %s", (Jsonb(rec.doc), rec.id))
                events.append(f"NO WORK     {rec.id}: nothing to carry for {rec.driver_name}")
            continue
        provisional = _provisional_for(blk, rec) if blk.seq else None
        before = rec.doc.get("provisional")
        if provisional != before or rec.doc.get("estimated_fill_percent") != (provisional or {}).get("fill_percent", 0):
            rec.doc["provisional"] = provisional
            rec.doc["estimated_fill_percent"] = (provisional or {}).get("fill_percent", 0)
            conn.execute("UPDATE driver_availability SET data = %s WHERE id = %s", (Jsonb(rec.doc), rec.id))
        if blk.seq:
            index = _built_index(planner.build_stops(blk))
            stops_by_n = {s["n"]: s for s in provisional["stops"]}
            for pkg in blk.pkgs:
                placed_provisionally.add(pkg.id)
                r = by_pkg[pkg.id]
                placement = {
                    "availability_id": rec.id, "driver_name": rec.driver_name, "total_stops": len(provisional["stops"]),
                    "pickup_stop": index[pkg.id]["pickup"], "dropoff_stop": index[pkg.id]["dropoff"],
                    "pickup_eta": stops_by_n[index[pkg.id]["pickup"]]["eta"], "dropoff_eta": stops_by_n[index[pkg.id]["dropoff"]]["eta"],
                    "route_locks_at": rec.doc["locks_at"], "finalizes_at": rec.commit_at.isoformat(),
                }
                if r.doc.get("provisional") != placement:
                    r.doc["provisional"] = placement
                    _save(conn, r.table, r.doc)

    placed_ids = committed | placed_provisionally
    unplaced = [r for r in queue if r.pkg.id not in placed_ids]
    for rec in unplaced:
        if rec.doc.pop("provisional", None) is not None:
            _save(conn, rec.table, rec.doc)

    events.extend(refresh_last_minute(conn, now, unplaced))
    return events


def tick(now: Optional[datetime] = None, finalize: bool = True) -> list[str]:
    initialize_database()
    now = now or demo_clock.now()
    with get_db_connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK,))
        events = complete_routes(conn, now)
        events += plan_and_commit(conn, now, finalize)
    return events


def next_event_after(now: datetime, limit: datetime) -> Optional[datetime]:
    """Next moment (<= limit) something must happen: a block commit or a shift end."""
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT data->>'locks_at', end_at, route_id FROM driver_availability WHERE end_at > %s", (now,)
        ).fetchall()
    times: list[datetime] = []
    for locks_at, end_at, route_id in rows:
        if route_id is None:
            times.append(datetime.fromisoformat(locks_at) - FINALIZE_MARGIN)
        times.append(end_at)
    times = [t for t in times if now < t <= limit]
    return min(times) if times else None


# ==========================================
# NEW BOOKINGS + LAST-MINUTE ACCEPTANCE
# ==========================================

def on_new_booking(now: Optional[datetime] = None) -> list[str]:
    """Called right after a seller books: provisional placement or last-minute, never finalizes."""
    return tick(now, finalize=False)


def _simulate_fit(conn, rec: BlockRec, request_recs: list[PkgRec], now: datetime) -> Optional[tuple[float, planner.Blk, dict[str, PkgRec]]]:
    """Could this block also carry the requested packages? Returns (extra seconds, plan, all records)."""
    blk = _planner_block(rec)
    recs: dict[str, PkgRec] = {r.pkg.id: r for r in request_recs}
    if rec.route_id:
        route = conn.execute("SELECT data FROM driver_routes WHERE id = %s", (rec.route_id,)).fetchone()[0]
        if route.get("started_at"):
            return None
        existing = fetch_pkgrecs(conn, list({s["pkg"] for s in route["sequence"]}))
        recs.update(existing)
        blk.seq = [planner.Stop(s["kind"], existing[s["pkg"]].pkg) for s in route["sequence"]]
        blk.timeline = planner.evaluate(blk, blk.seq)
        if not blk.timeline:
            return None
        base = planner.drive_seconds(blk)
        for r in request_recs:
            result = planner.best_insertion(blk, r.pkg)
            if not result:
                return None
            blk.seq, blk.timeline = result[1], result[2]
        return planner.drive_seconds(blk) - base, blk, recs
    # forming block: re-plan it together with what is provisionally on it
    ids = (rec.doc.get("provisional") or {}).get("package_ids", [])
    existing = fetch_pkgrecs(conn, ids) if ids else {}
    recs.update(existing)
    planner.MATRIX.ensure([blk.start] + [p for r in recs.values() for p in (r.pkg.pick, r.pkg.drop)])
    base_blk = _planner_block(rec)
    planner.plan([base_blk], [r.pkg for r in existing.values()])
    base = planner.drive_seconds(base_blk)
    for r in request_recs:
        r.pkg.reserved_block = rec.id
    unplaced = planner.plan([blk], [r.pkg for r in recs.values()])
    if any(p.id in {r.pkg.id for r in request_recs} for p in unplaced):
        return None
    return planner.drive_seconds(blk) - base, blk, recs


def find_fits(conn, request_doc: dict, now: datetime, availability_id: Optional[str] = None) -> list[tuple[BlockRec, float, planner.Blk, dict[str, PkgRec]]]:
    recs = list(fetch_pkgrecs(conn, request_doc["package_ids"]).values())
    if not recs:
        return []
    rows = conn.execute(
        "SELECT id, data, start_at, end_at, route_id FROM driver_availability WHERE end_at > %s ORDER BY start_at", (now,)
    ).fetchall()
    fits = []
    for row in rows:
        rec = BlockRec(*row)
        if rec.driver_id != DEFAULT_DRIVER_ID or (availability_id and rec.id != availability_id):
            continue
        fresh = [PkgRec(r.pkg, r.table, r.doc, r.shipper, r.product, r.created_at) for r in recs]
        fit = _simulate_fit(conn, rec, fresh, now)
        if fit:
            fits.append((rec, fit[0], fit[1], fit[2]))
    fits.sort(key=lambda f: f[1])
    return fits


def accept_last_minute(request_id: str, availability_id: Optional[str] = None, now: Optional[datetime] = None) -> dict:
    """The driver agrees to cover a last-minute request; fold it into their route."""
    initialize_database()
    now = now or demo_clock.now()
    with get_db_connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK,))
        row = conn.execute("SELECT data FROM last_minute_requests WHERE id = %s AND status = 'open' FOR UPDATE", (request_id,)).fetchone()
        if row is None:
            raise LookupError("Last-minute request not found or already taken")
        request_doc = row[0]
        fits = find_fits(conn, request_doc, now, availability_id)
        if not fits:
            raise ValueError("None of your routes can take these packages (capacity, hours, or distance)")
        rec, extra, blk, recs = fits[0]
        if rec.route_id:
            route_id = _commit_block(conn, rec, blk, recs, now, amending=True)
            outcome = "added_to_route"
        else:
            route_id = None
            for r in recs.values():
                if r.pkg.id in request_doc["package_ids"]:
                    r.doc["reserved_availability_id"] = rec.id
                    _save(conn, r.table, r.doc)
            outcome = "reserved"
        request_doc.update({"status": "accepted", "accepted_at": now.isoformat(), "availability_id": rec.id, "route_id": route_id})
        conn.execute("UPDATE last_minute_requests SET status = 'accepted', data = %s WHERE id = %s", (Jsonb(request_doc), request_id))
    if outcome == "reserved":
        tick(now, finalize=False)
    return {
        "request_id": request_id, "outcome": outcome, "availability_id": rec.id, "route_id": route_id,
        "extra_drive_minutes": round(extra / 60),
    }
