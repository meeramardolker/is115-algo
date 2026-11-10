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

from solution.policies.helpers import (
    HUB,
    PLANNING_INTERVAL,
    rounding_off,
    UnionFind,
    build_regions,
    window_type,
    is_waiting,
    is_expired,
    route_length,
    nearest_neighbour,
    arrival_offsets,
    build_route,
    _feasible,
    _all_on_time,
    _candidate_orders,
    load_trip,
    flatten,
    parcel_latest_dispatch,
    add_filler,
)


@dataclass(frozen=True)
class RegionConfig:
    """Configuration parameters for region building and dispatch triggers."""
    region_threshold_km: float = 10.0
    fill_threshold_share: Optional[float] = None


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


def deadline_due(batch: Batch, now: float) -> bool:
    """Check if a batch must be dispatched in the current planning round. Time complexity: O(1)."""
    return now + PLANNING_INTERVAL > batch.latest_dispatch + rounding_off


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


CONFIG = RegionConfig()


def dispatch(parcel: Dict[str, Dict], context: Dict) -> List[str]:
    """Dispatch entry point delegating trip planning to plan_trip."""
    return plan_trip(parcel, context, CONFIG)

