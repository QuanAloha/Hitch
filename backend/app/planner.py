"""Route planner: clusters packages onto driver blocks using Valhalla travel times.

Pure logic (no database). `plan()` takes driver blocks and queued packages and
returns an assignment where each block has an ordered stop sequence.

How it keeps routes compact:
  * Travel times come from Valhalla's /sources_to_targets matrix (real roads),
    fetched once for all points in 50x50 tiles; haversine is only a fallback.
  * Packages are grouped by region (Logan, Ogden, SLC, ...) and processed
    densest-region first, so each block fills up with its neighbours.
  * A package joins the block where it adds the least driving time (cheapest
    insertion, pickup always before its drop-off), subject to:
      - vehicle capacity at every point of the route (peak on-board load),
      - the seller's pickup window and the driver's shift end,
      - MAX_MARGINAL_S extra driving per added package,
      - MAX_FROM_START_S: no stop farther than this from the driver's start,
        which is what stops a route zig-zagging Provo -> Ogden -> Park City.
  * A relocation pass then moves packages between blocks if that saves driving.
"""

import math
import os
from dataclasses import dataclass, field
from typing import Optional

import httpx

VALHALLA_URL = os.getenv("VALHALLA_URL", "http://localhost:8002").rstrip("/")

# --- tunables (seconds unless noted) ---------------------------------------
SERVICE_PICKUP_S = 5 * 60
SERVICE_DROP_S = 3 * 60
STOP_WINDOW_S = 30 * 60          # width of the window shown to sellers
MAX_STOPS = 18                   # per route, so one Valhalla /route call can draw it
MAX_MARGINAL_S = 25 * 60         # extra driving one added package may cost
MAX_DEADHEAD_S = 35 * 60         # driver start -> first pickup
MAX_FROM_START_S = 80 * 60       # no stop farther than this from the driver's start
MAX_CANDIDATE_BLOCKS = 8
CANDIDATE_RADIUS_KM = 90.0
SAME_PLACE_M = 60.0              # stops closer than this share one stop

REGIONS = {
    "Logan": (41.7370, -111.8338),
    "Ogden": (41.2230, -111.9738),
    "Salt Lake City": (40.7608, -111.8910),
    "Park City": (40.6461, -111.4980),
    "Tooele": (40.5308, -112.2983),
    "Lehi": (40.3916, -111.8508),
    "Provo": (40.2338, -111.6585),
}

Point = tuple[float, float]


def haversine_km(a: Point, b: Point) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def nearest_region(point: Point) -> str:
    return min(REGIONS, key=lambda name: haversine_km(point, REGIONS[name]))


def key(point: Point) -> Point:
    return (round(point[0], 5), round(point[1], 5))


# ==========================================
# TRAVEL TIMES (Valhalla matrix)
# ==========================================

class TravelMatrix:
    TILE = 50

    def __init__(self) -> None:
        self.seconds: dict[tuple[Point, Point], float] = {}
        self.estimated_pairs = 0
        self.valhalla_pairs = 0

    def ensure(self, points: list[Point]) -> None:
        """Fetch every missing pair among `points` from Valhalla."""
        unique = list(dict.fromkeys(key(p) for p in points))
        self._load_persisted(unique)
        for i in range(0, len(unique), self.TILE):
            sources = unique[i:i + self.TILE]
            for j in range(0, len(unique), self.TILE):
                targets = unique[j:j + self.TILE]
                if all((s, t) in self.seconds or s == t for s in sources for t in targets):
                    continue
                self._fetch_tile(sources, targets)

    def _load_persisted(self, unique: list[Point]) -> None:
        """Reuse Valhalla results from earlier runs (CLI calls and the API loop share them)."""
        wanted = set(unique)
        if all((s, t) in self.seconds or s == t for s in unique for t in unique):
            return
        try:
            from app.database import get_db_connection
            with get_db_connection() as conn:
                rows = conn.execute(
                    "SELECT a_lat, a_lon, b_lat, b_lon, seconds, estimated FROM travel_times "
                    "WHERE a_lat = ANY(%s) AND a_lon = ANY(%s) AND b_lat = ANY(%s) AND b_lon = ANY(%s)",
                    ([k[0] for k in unique], [k[1] for k in unique], [k[0] for k in unique], [k[1] for k in unique]),
                ).fetchall()
        except Exception:
            return
        for a_lat, a_lon, b_lat, b_lon, seconds, estimated in rows:
            a, b = (a_lat, a_lon), (b_lat, b_lon)
            if a in wanted and b in wanted and (a, b) not in self.seconds:
                self.seconds[(a, b)] = float(seconds)
                if estimated:
                    self.estimated_pairs += 1

    def _persist(self, items: list[tuple[Point, Point, float, bool]]) -> None:
        if not items:
            return
        try:
            from app.database import get_db_connection
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO travel_times (a_lat, a_lon, b_lat, b_lon, seconds, estimated) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                        [(a[0], a[1], b[0], b[1], sec, est) for a, b, sec, est in items],
                    )
        except Exception:
            pass

    def _fetch_tile(self, sources: list[Point], targets: list[Point]) -> None:
        try:
            response = httpx.post(
                f"{VALHALLA_URL}/sources_to_targets",
                json={
                    "sources": [{"lat": p[0], "lon": p[1]} for p in sources],
                    "targets": [{"lat": p[0], "lon": p[1]} for p in targets],
                    "costing": "auto",
                },
                timeout=60.0,
            )
            response.raise_for_status()
            matrix = response.json()["sources_to_targets"]
        except (httpx.HTTPError, KeyError, ValueError):
            matrix = None

        fresh: list[tuple[Point, Point, float, bool]] = []
        for si, s in enumerate(sources):
            for ti, t in enumerate(targets):
                if s == t:
                    self.seconds[(s, t)] = 0.0
                    continue
                if (s, t) in self.seconds:
                    continue
                cell = matrix[si][ti] if matrix else None
                if cell and cell.get("time") is not None:
                    self.seconds[(s, t)] = float(cell["time"])
                    self.valhalla_pairs += 1
                    fresh.append((s, t, float(cell["time"]), False))
                else:
                    self.seconds[(s, t)] = self._estimate(s, t)
                    self.estimated_pairs += 1
                    if matrix is not None:  # Valhalla answered "no route": remember so we never re-ask
                        fresh.append((s, t, self.seconds[(s, t)], True))
        self._persist(fresh)

    @staticmethod
    def _estimate(a: Point, b: Point) -> float:
        # Fallback only: straight line x 1.35 at ~32 mph.
        return haversine_km(a, b) * 1.35 / (32 * 1.609344) * 3600.0

    def sec(self, a: Point, b: Point) -> float:
        ka, kb = key(a), key(b)
        if ka == kb:
            return 0.0
        value = self.seconds.get((ka, kb))
        if value is None:
            value = self._estimate(ka, kb)
            self.seconds[(ka, kb)] = value
            self.estimated_pairs += 1
        return value


MATRIX = TravelMatrix()


# ==========================================
# MODEL
# ==========================================

@dataclass
class Pkg:
    id: str
    spots: int
    pick: Point
    drop: Point
    ready_start: float            # epoch seconds; earliest pickup
    ready_end: float              # epoch seconds; latest pickup
    pick_info: dict = field(default_factory=dict)
    drop_info: dict = field(default_factory=dict)
    reserved_block: Optional[str] = None
    region: str = ""


@dataclass
class Stop:
    kind: str                     # "pickup" | "drop"
    pkg: Pkg

    @property
    def pt(self) -> Point:
        return self.pkg.pick if self.kind == "pickup" else self.pkg.drop


@dataclass
class Timeline:
    arrive: list[float]
    service_start: list[float]
    drive_s: float
    end: float
    peak_load: int


@dataclass
class Blk:
    id: str
    start: Point
    t0: float
    t1: float
    capacity: int
    seq: list[Stop] = field(default_factory=list)
    timeline: Optional[Timeline] = None

    @property
    def pkgs(self) -> list[Pkg]:
        return [s.pkg for s in self.seq if s.kind == "pickup"]


def evaluate(blk: Blk, seq: list[Stop]) -> Optional[Timeline]:
    """Simulate the route; None if it breaks a window, the shift, or capacity."""
    if len(seq) > 2 * MAX_STOPS:
        return None
    t, cur = blk.t0, blk.start
    load = peak = 0
    drive = 0.0
    arrive, started = [], []
    for stop in seq:
        pt = stop.pt
        if MATRIX.sec(blk.start, pt) > MAX_FROM_START_S:
            return None
        leg = MATRIX.sec(cur, pt)
        drive += leg
        t += leg
        arrive.append(t)
        if stop.kind == "pickup":
            if t < stop.pkg.ready_start:
                t = stop.pkg.ready_start
            if t > stop.pkg.ready_end:
                return None
            load += stop.pkg.spots
            if load > blk.capacity:
                return None
            peak = max(peak, load)
            started.append(t)
            t += SERVICE_PICKUP_S
        else:
            load -= stop.pkg.spots
            started.append(t)
            t += SERVICE_DROP_S
        cur = pt
    if t > blk.t1:
        return None
    return Timeline(arrive, started, drive, t, peak)


def drive_seconds(blk: Blk, seq: Optional[list[Stop]] = None) -> float:
    seq = blk.seq if seq is None else seq
    total, cur = 0.0, blk.start
    for stop in seq:
        total += MATRIX.sec(cur, stop.pt)
        cur = stop.pt
    return total


# ==========================================
# INSERTION
# ==========================================

def best_insertion(blk: Blk, pkg: Pkg) -> Optional[tuple[float, list[Stop], Timeline]]:
    """Cheapest feasible way to add pkg to blk, as (extra seconds, new seq, timeline)."""
    if len(blk.pkgs) + 1 > MAX_STOPS:
        return None
    if blk.t1 <= pkg.ready_start or blk.t0 >= pkg.ready_end:
        return None
    if not blk.seq and MATRIX.sec(blk.start, pkg.pick) > MAX_DEADHEAD_S:
        return None

    m = len(blk.seq)
    pts = [blk.start] + [s.pt for s in blk.seq]
    p, q = pkg.pick, pkg.drop
    d = MATRIX.sec
    candidates = []
    for i in range(m + 1):
        a = pts[i]
        b = pts[i + 1] if i < m else None
        for j in range(i, m + 1):
            if i == j:
                delta = d(a, p) + d(p, q) + ((d(q, b) - d(a, b)) if b else 0.0)
            else:
                delta = d(a, p) + d(p, b) - d(a, b)
                c = pts[j]
                e = pts[j + 1] if j < m else None
                delta += d(c, q) + ((d(q, e) - d(c, e)) if e else 0.0)
            candidates.append((delta, i, j))
    candidates.sort()

    for delta, i, j in candidates[:40]:
        if blk.seq and delta > MAX_MARGINAL_S:
            return None  # sorted: nothing cheaper follows
        new_seq = list(blk.seq)
        new_seq.insert(j, Stop("drop", pkg))
        new_seq.insert(i, Stop("pickup", pkg))
        timeline = evaluate(blk, new_seq)
        if timeline:
            return delta, new_seq, timeline
    return None


def _candidate_blocks(blocks: list[Blk], pkg: Pkg) -> list[Blk]:
    scored = []
    for blk in blocks:
        if blk.t1 <= pkg.ready_start or blk.t0 >= pkg.ready_end:
            continue
        nearest = min([haversine_km(blk.start, pkg.pick)] + [haversine_km(s.pt, pkg.pick) for s in blk.seq])
        if nearest <= CANDIDATE_RADIUS_KM:
            scored.append((nearest, blk))
    scored.sort(key=lambda item: item[0])
    return [blk for _, blk in scored[:MAX_CANDIDATE_BLOCKS]]


def _apply(blk: Blk, seq: list[Stop], timeline: Timeline) -> None:
    blk.seq, blk.timeline = seq, timeline


# ==========================================
# PLAN
# ==========================================

def plan(blocks: list[Blk], packages: list[Pkg]) -> list[Pkg]:
    """Assign packages to blocks in place. Returns the packages nothing could take."""
    points = [b.start for b in blocks] + [p.pick for p in packages] + [p.drop for p in packages]
    MATRIX.ensure(points)
    by_id = {b.id: b for b in blocks}

    for pkg in packages:
        pkg.region = nearest_region(pkg.pick)

    unassigned: list[Pkg] = []

    # 1. Packages a driver explicitly agreed to cover go first, into that block.
    free: list[Pkg] = []
    for pkg in packages:
        blk = by_id.get(pkg.reserved_block or "")
        result = best_insertion(blk, pkg) if blk else None
        if result:
            _apply(blk, result[1], result[2])
        else:
            pkg.reserved_block = None
            free.append(pkg)

    # 2. Densest region first, then by pickup time; neighbours land together.
    region_size: dict[str, int] = {}
    for pkg in free:
        region_size[pkg.region] = region_size.get(pkg.region, 0) + 1
    free.sort(key=lambda p: (-region_size[p.region], p.region, p.ready_start, p.pick[0]))

    for pkg in free:
        best = None
        for blk in _candidate_blocks(blocks, pkg):
            result = best_insertion(blk, pkg)
            if result and (best is None or result[0] < best[0]):
                best = (result[0], blk, result[1], result[2])
        if best:
            _apply(best[1], best[2], best[3])
        else:
            unassigned.append(pkg)

    _relocate(blocks)
    for blk in blocks:
        blk.timeline = evaluate(blk, blk.seq) if blk.seq else None
    return unassigned


def _relocate(blocks: list[Blk], passes: int = 2) -> None:
    for _ in range(passes):
        moved = False
        for blk in blocks:
            for pkg in list(blk.pkgs):
                if pkg.reserved_block:
                    continue
                without = [s for s in blk.seq if s.pkg is not pkg]
                saving = drive_seconds(blk) - drive_seconds(blk, without)
                if saving < 120:
                    continue
                best = None
                for other in _candidate_blocks(blocks, pkg):
                    if other is blk:
                        continue
                    result = best_insertion(other, pkg)
                    if result and result[0] < saving - 120 and (best is None or result[0] < best[0]):
                        best = (result[0], other, result[1], result[2])
                if best:
                    timeline = evaluate(blk, without) if without else None
                    if without and not timeline:
                        continue
                    _apply(blk, without, timeline)
                    _apply(best[1], best[2], best[3])
                    moved = True
        if not moved:
            return


# ==========================================
# STOPS + PAYOUT (for persisting a finished plan)
# ==========================================

@dataclass
class BuiltStop:
    kind: str                         # "pickup" | "dropoff"
    pt: Point
    pkgs: list[Pkg]
    arrive: float
    window_start: float
    window_end: float


def build_stops(blk: Blk) -> list[BuiltStop]:
    """Collapse consecutive same-kind stops at one place (e.g. a shared post office)."""
    if not blk.timeline:
        return []
    built: list[BuiltStop] = []
    for idx, stop in enumerate(blk.seq):
        kind = "pickup" if stop.kind == "pickup" else "dropoff"
        started = blk.timeline.service_start[idx]
        prev = built[-1] if built else None
        if prev and prev.kind == kind and haversine_km(prev.pt, stop.pt) * 1000 <= SAME_PLACE_M:
            prev.pkgs.append(stop.pkg)
            continue
        built.append(BuiltStop(kind, stop.pt, [stop.pkg], blk.timeline.arrive[idx], started, started + STOP_WINDOW_S))
    return built


def payout(pkg: Pkg) -> float:
    direct_min = MATRIX.sec(pkg.pick, pkg.drop) / 60.0
    return round(5.0 + 0.45 * direct_min + 1.5 * (pkg.spots - 1), 2)


def package_spots(dimensions: dict, quantity: int = 1) -> int:
    """Standard package spot ~ 3,000 cubic inches (e.g. a 18x14x12 box)."""
    unit = dimensions.get("unit", "in")
    scale = 1.0 if unit == "in" else 1 / 2.54
    volume = (dimensions["length"] * scale) * (dimensions["width"] * scale) * (dimensions["height"] * scale)
    return max(1, math.ceil(volume / 3000.0)) * max(1, int(quantity))
