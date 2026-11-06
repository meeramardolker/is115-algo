"""Region-based dispatch policy module.

Groups delivery locations into regions with union-find, queues waiting parcels by
region and window type, sends a queue at its last valid planning round, loads only
parcels that stay on time, serves the biggest loads first when trucks are short
(truck load algorithm), and carries expired parcels as filler.

Time Complexities:
------------------
UnionFind.find(i):
    Amortised O(alpha(N)), where N is the number of locations and alpha is
    the Inverse Ackermann function (effectively O(1) for practical sizes).

UnionFind.union(i, j):
    Amortised O(alpha(N)) to find roots and merge sets by rank.

build_regions(distances, threshold_km):
    - Location extraction & sorting: O(N log N), where N is the number of locations.
    - Graph edge evaluation & unions: O(N^2 * alpha(N)).
    - Region aggregation & sorting by distance from HUB: O(R * (M log M + log R)),
      where R is the number of regions and M is the maximum size of a region.
    - Overall Complexity: O(N^2) time complexity, computed once and cached.

Queue, timer, trigger, loading and filler functions list their own time complexity
in their docstrings.

dispatch(parcel, context):
    Calls plan_trip once. See plan_trip for its time complexity.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import math

HUB = "HUB"
PLANNING_INTERVAL = 10.0
rounding_off = 1e-9


@dataclass(frozen=True)
class RegionConfig:
    """Configuration parameters for region building and dispatch triggers."""
    region_threshold_km: float = 10.0
    fill_threshold_share: Optional[float] = None


class UnionFind:
    """Disjoint-set data structure with path compression and union by rank."""

    def __init__(self, elements: List[str]) -> None:
        self.parent: Dict[str, str] = {elem: elem for elem in elements}
        self.rank: Dict[str, int] = {elem: 0 for elem in elements}

    def find(self, i: str) -> str:
        """Find the representative root of element i with path compression."""
        if self.parent[i] != i:
            self.parent[i] = self.find(self.parent[i])
        return self.parent[i]

    def union(self, i: str, j: str) -> None:
        """Merge the sets containing elements i and j using union by rank."""
        root_i = self.find(i)
        root_j = self.find(j)
        if root_i != root_j:
            if self.rank[root_i] < self.rank[root_j]:
                self.parent[root_i] = root_j
            elif self.rank[root_i] > self.rank[root_j]:
                self.parent[root_j] = root_i      
            else:
                self.parent[root_j] = root_i
                self.rank[root_i] += 1


_REGION_CACHE: Dict[float, Dict[str, str]] = {}


def build_regions(distances: Dict[str, Dict[str, float]], threshold_km: float) -> Dict[str, str]:
    """Group locations into regions based on minimum directional distance threshold.

    Locations within threshold_km of each other are linked into connected components.
    Regions are labelled 'A', 'B', 'C', ... in order of their closest member's
    distance from HUB.
    """
    if threshold_km in _REGION_CACHE:
        return _REGION_CACHE[threshold_km]

    locations = sorted([loc for loc in distances.keys() if loc != HUB])
    uf = UnionFind(locations)

    # Link locations if min(dist[a][b], dist[b][a]) <= threshold_km
    for i, a in enumerate(locations):
        for b in locations[i + 1:]:
            d_ab = distances.get(a, {}).get(b, float("inf"))
            d_ba = distances.get(b, {}).get(a, float("inf"))
            if min(d_ab, d_ba) <= threshold_km:
                uf.union(a, b)

    # Group locations by root representative
    components: Dict[str, List[str]] = {}
    for loc in locations:
        root = uf.find(loc)
        components.setdefault(root, []).append(loc)

    # Sort each component's locations and find the closest member to HUB
    region_info = []
    for root, locs in components.items():
        sorted_locs = sorted(locs)
        min_hub_dist = min(distances[HUB][loc] for loc in sorted_locs)
        region_info.append((min_hub_dist, sorted_locs))

    # Sort regions by their closest member's distance from HUB
    region_info.sort(key=lambda item: (item[0], item[1][0]))

    # Label regions "A", "B", "C", ...
    location_to_region: Dict[str, str] = {}
    for idx, (_, locs) in enumerate(region_info):
        label = chr(ord("A") + idx)
        for loc in locs:
            location_to_region[loc] = label

    _REGION_CACHE[threshold_km] = location_to_region
    return location_to_region


def window_type(record: Dict[str, float], return_cutoff: float) -> str:
    """Classify delivery window into 'afternoon', 'open', or 'tight'. Time complexity: O(1)."""
    if record["delivery_window_start"] > record["arrival_time"] + rounding_off:
        return "afternoon"
    if record["delivery_window_end"] >= return_cutoff - rounding_off:
        return "open"
    return "tight"


def is_waiting(record: Dict[str, Optional[float]]) -> bool:
    """Check if a parcel is waiting for assignment and delivery. Time complexity: O(1)."""
    return record["vehicle_assigned"] is None and record["delivery_time"] is None


def is_expired(record: Dict[str, float], context: Dict) -> bool:
    """Check if a parcel cannot make a feasible direct trip. Time complexity: O(1)."""
    departure = context["current_time"] + context["departure_delay"]
    location = record["delivery_location_id"]
    arrival = departure + context["travel_distance"][HUB][location]
    if arrival > record["delivery_window_end"] + rounding_off:
        return True
    return arrival + context["travel_distance"][location][HUB] > context["return_cutoff"] + rounding_off

def route_length(order: List[str], distances: Dict[str, Dict[str, float]]) -> float:
    """Calculate total trip distance from HUB through ordered stops and back to HUB. Time complexity: O(S), where S is len(order)."""
    if not order:
        return 0.0
    total_dist = distances[HUB][order[0]]
    for i in range(len(order) - 1):
        total_dist += distances[order[i]][order[i + 1]]
    total_dist += distances[order[-1]][HUB]
    return total_dist


def nearest_neighbour(locations: List[str], distances: Dict[str, Dict[str, float]]) -> List[str]:
    """Build a route starting from HUB by repeatedly visiting the closest unvisited location. Time complexity: O(L^2), where L is len(locations)."""
    unvisited = set(locations)
    current = HUB
    route = []
    while unvisited:
        next_loc = min(unvisited, key=lambda loc: (distances[current][loc], loc))
        route.append(next_loc)
        unvisited.remove(next_loc)
        current = next_loc
    return route

def arrival_offsets(order: List[str], distances: Dict[str, Dict[str, float]]) -> Dict[str, float]:
    """Compute travel time offsets in minutes from HUB departure to each stop. Time complexity: O(S), where S is len(order)."""
    offsets: Dict[str, float] = {}
    if not order:
        return offsets
    elapsed = distances[HUB][order[0]]
    offsets[order[0]] = elapsed
    for i in range(len(order) - 1):
        elapsed += distances[order[i]][order[i + 1]]
        offsets[order[i + 1]] = elapsed
    return offsets


def build_route(locations: List[str], distances: Dict[str, Dict[str, float]]) -> List[str]:
    """Construct an initial route over a set of distinct delivery locations. Time complexity: O(L^2), where L is len(locations)."""
    return nearest_neighbour(locations, distances)

@dataclass
class Batch:
    """Queue of waiting parcels sharing a region and window type."""
    region: str
    kind: str
    parcel_ids: List[str] = field(default_factory=list)
    load: float = 0.0
    route: List[str] = field(default_factory=list)
    route_time: float = 0.0
    latest_dispatch: float = math.inf


def build_queues(
    parcel: Dict[str, Dict],
    context: Dict,
    region_of: Dict[str, str]
) -> Tuple[Dict[Tuple[str, str], Batch], Dict[str, List[str]]]:
    """Group waiting parcels into queues by region and window type (expired parcels are collected per region for filler), then compute route timers. Time complexity: O(P + B * L^2), where P is total parcels, B is number of batches, and L is max distinct locations per batch."""
    queues: Dict[Tuple[str, str], Batch] = {}
    return_cutoff = context["return_cutoff"]
    departure_delay = context["departure_delay"]
    distances = context["travel_distance"]

    expired: Dict[str, List[str]] = {}

    for pid in sorted(parcel.keys()):
        record = parcel[pid]
        if not is_waiting(record):
            continue
        if is_expired(record, context):
            expired.setdefault(region_of[record["delivery_location_id"]], []).append(pid)
            continue

        loc = record["delivery_location_id"]
        reg = region_of[loc]
        w_type = window_type(record, return_cutoff)
        key = (reg, w_type)

        if key not in queues:
            queues[key] = Batch(region=reg, kind=w_type)

        batch = queues[key]
        batch.parcel_ids.append(pid)
        batch.load += record["size"]

    for batch in queues.values():
        distinct_locations = sorted({parcel[pid]["delivery_location_id"] for pid in batch.parcel_ids})
        batch.route = build_route(distinct_locations, distances)
        batch.route_time = route_length(batch.route, distances)
        offsets = arrival_offsets(batch.route, distances)

        max_dispatch_return = return_cutoff - batch.route_time - departure_delay
        max_dispatch_parcels = min(
            parcel[pid]["delivery_window_end"] - offsets[parcel[pid]["delivery_location_id"]] - departure_delay
            for pid in batch.parcel_ids
        )
        batch.latest_dispatch = min(max_dispatch_return, max_dispatch_parcels)

    return queues, expired


def parcel_latest_dispatch(record: Dict[str, float], context: Dict) -> float:
    """Compute the latest hub dispatch time for a parcel on a direct trip. Time complexity: O(1)."""
    loc = record["delivery_location_id"]
    return record["delivery_window_end"] - context["travel_distance"][HUB][loc] - context["departure_delay"]

def deadline_due(batch: Batch, now: float) -> bool:
    """Check if a batch must be dispatched in the current planning round. Time complexity: O(1)."""
    return now + PLANNING_INTERVAL > batch.latest_dispatch + rounding_off

def add_filler(
    order: List[str],
    members: Dict[str, List[str]],
    expired_ids: List[str],
    parcel: Dict[str, Dict],
    capacity: float
) -> None:
    """In-place helper to update members with eligible expired parcels.

    Time complexity: O(S + P + E), where S is len(order), P is parcels already on the
    truck, and E is len(expired_ids).
    """
    if not expired_ids or not order:
        return

    stops = set(order)
    on_board = set()
    used = 0.0

    for stop in order:
        for pid in members.get(stop, []):
            on_board.add(pid)
            used += parcel[pid]["size"]

    remaining = capacity - used
    if remaining <= rounding_off:
        return

    for pid in expired_ids:
        if pid in on_board:
            continue

        info = parcel[pid]
        stop = info["delivery_location_id"]
        if stop not in stops:
            continue

        size = info["size"]
        if size > remaining + rounding_off:
            continue

        members.setdefault(stop, []).append(pid)
        on_board.add(pid)
        remaining -= size
        if remaining <= rounding_off:
            break

def is_full_enough(batch: Batch, capacity: float, share: Optional[float]) -> bool:
    """Check if a batch meets or exceeds the fill capacity share threshold. Time complexity: O(1)."""
    if share is None:
        return False
    return batch.load >= (share * capacity) - rounding_off


def plan_trip(parcel: Dict[str, Dict], context: Dict, config: RegionConfig) -> List[str]:
    """Select and plan a parcel trip for an available vehicle given a region configuration.
    
    Time complexity: O(B * L^2 + B log B + P_cand * S^2), where B is the number of batches,
    L is max locations per batch, P_cand is candidates in a batch, and S is route stops.
    """
    region_of = build_regions(context["travel_distance"], config.region_threshold_km)
    queues, expired = build_queues(parcel, context, region_of)
    if not queues:
        return []

    now = context["current_time"]
    capacity = context["capacity"]

    # Partition batches into due queues and fill-triggered queues
    due = [b for b in queues.values() if deadline_due(b, now)]
    full = [
        b for b in queues.values()
        if b not in due and is_full_enough(b, capacity, config.fill_threshold_share)
    ]

    if not due and not full:
        return []

    # Sort due batches by urgency
    due.sort(key=lambda b: b.latest_dispatch)

    # Re-sort due batches by load if vehicle fleet is constrained
    needed = sum(math.ceil(b.load / capacity - rounding_off) for b in due)
    idle_count = sum(
        1 for v_info in context["fleet"].values()
        if v_info["available_at"] <= now + rounding_off
    )
    if needed > idle_count:
        due.sort(key=lambda b: (-min(b.load, capacity), b.latest_dispatch))

    # Sort full-triggered batches (earliest deadline first, tie-break by larger load)
    full.sort(key=lambda b: (b.latest_dispatch, -b.load))

    # Combine prioritized queues
    candidate_batches = due + full

    for batch in candidate_batches:
        core = sorted(
            batch.parcel_ids,
            key=lambda pid: (parcel_latest_dispatch(parcel[pid], context), pid)
        )
        order, members = load_trip(core, parcel, context)
        if order:
            add_filler(order, members, expired.get(batch.region, []), parcel, capacity)
            return flatten(order, members)

    return []


def _feasible(order: List[str], context: Dict, departure: float) -> bool:
    """Check if a route respects vehicle range and hub return cutoff limits. Time complexity: O(S), where S is len(order)."""
    r_len = route_length(order, context["travel_distance"])
    if r_len > context["max_trip_distance_km"] + rounding_off:
        return False
    return departure + r_len <= context["return_cutoff"] + rounding_off


def _all_on_time(
    order: List[str],
    members: Dict[str, List[str]],
    parcel: Dict[str, Dict],
    context: Dict,
    departure: float
) -> bool:
    """Check if all parcels delivered along the route arrive within their delivery windows. Time complexity: O(S + P_route), where S is len(order) and P_route is total loaded parcels."""
    offsets = arrival_offsets(order, context["travel_distance"])
    for loc, pids in members.items():
        arr = departure + offsets[loc]
        for pid in pids:
            p = parcel[pid]
            if arr < p["delivery_window_start"] - rounding_off or arr > p["delivery_window_end"] + rounding_off:
                return False
    return True


def _candidate_orders(
    order: List[str],
    new_location: str,
    distances: Dict[str, Dict[str, float]],
    deadline_of: Dict[str, float]
) -> List[List[str]]:
    """Generate candidate route permutations when introducing a new stop. Time complexity: O(S^2), where S is len(order)."""
    candidates = []
    # Insert new_location at every position
    for i in range(len(order) + 1):
        candidates.append(order[:i] + [new_location] + order[i:])
    # Rebuilt nearest-neighbour tour
    candidates.append(build_route(order + [new_location], distances))
    # Sorted by earliest deadline then HUB distance
    sorted_stops = sorted(
        order + [new_location],
        key=lambda loc: (deadline_of[loc], distances[HUB][loc])
    )
    candidates.append(sorted_stops)
    return candidates


def load_trip(core: List[str], parcel: Dict[str, Dict], context: Dict) -> Tuple[List[str], Dict[str, List[str]]]:
    """Greedily insert parcels into a trip, returning the feasible route and stop assignments. Time complexity: O(P_cand * S^2), where P_cand is len(core) and S is number of stops."""
    distances = context["travel_distance"]
    departure = context["current_time"] + context["departure_delay"]
    capacity = context["capacity"]

    order: List[str] = []
    members: Dict[str, List[str]] = {}
    deadline_of: Dict[str, float] = {}
    current_load = 0.0

    for pid in core:
        p = parcel[pid]
        p_size = p["size"]
        if current_load + p_size > capacity + rounding_off:
            continue

        loc = p["delivery_location_id"]
        p_win_end = p["delivery_window_end"]

        # Copy structures to test insertion
        cand_members = {stop: list(pids) for stop, pids in members.items()}
        cand_members.setdefault(loc, []).append(pid)

        cand_deadlines = dict(deadline_of)
        cand_deadlines[loc] = min(cand_deadlines.get(loc, math.inf), p_win_end)

        if loc in order:
            options = [order]
        else:
            options = _candidate_orders(order, loc, distances, cand_deadlines)

        valid_options = []
        for opt in options:
            if _feasible(opt, context, departure) and _all_on_time(opt, cand_members, parcel, context, departure):
                valid_options.append((route_length(opt, distances), opt))

        if not valid_options:
            continue

        # Keep shortest valid route option
        valid_options.sort(key=lambda item: item[0])
        best_route = valid_options[0][1]

        order = best_route
        members = cand_members
        deadline_of = cand_deadlines
        current_load += p_size

    return order, members


def flatten(order: List[str], members: Dict[str, List[str]]) -> List[str]:
    """Flatten members map into an ordered list of parcel IDs stop by stop. Time complexity: O(P_route), where P_route is total loaded parcels."""
    result = []
    for loc in order:
        if loc in members:
            result.extend(members[loc])
    return result


CONFIG = RegionConfig()


def dispatch(parcel: Dict[str, Dict], context: Dict) -> List[str]:
    """Dispatch entry point delegating trip planning to plan_trip."""
    return plan_trip(parcel, context, CONFIG)
