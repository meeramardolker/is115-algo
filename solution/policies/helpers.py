"""Shared helper functions for the dispatch policies: constants, union-find regions,
parcel checks, routing and on-time greedy insertion loading."""

import math
from typing import Dict, List, Optional, Tuple


HUB = "HUB"
PLANNING_INTERVAL = 10.0
rounding_off = 1e-9


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


def parcel_latest_dispatch(record: Dict[str, float], context: Dict) -> float:
    """Compute the latest hub dispatch time for a parcel on a direct trip. Time complexity: O(1)."""
    loc = record["delivery_location_id"]
    return record["delivery_window_end"] - context["travel_distance"][HUB][loc] - context["departure_delay"]


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


def flatten(order: List[str], members: Dict[str, List[str]]) -> List[str]:
    """Flatten members map into an ordered list of parcel IDs stop by stop. Time complexity: O(P_route), where P_route is total loaded parcels."""
    result = []
    for loc in order:
        if loc in members:
            result.extend(members[loc])
    return result


# ===== Stop-list helpers shared by edf.py and savings.py =====
# A route here is a list of stops: [[location_id, [parcel_id, ...]], ...]


def dist(context, a, b):
    """Directed distance from a to b in km. Time complexity: O(1)."""
    return context['travel_distance'][a][b]


def stops_route_length(context, stops):
    """Total km from HUB through every stop and back to HUB. Time complexity: O(S) for S stops."""
    if not stops:
        return 0.0
    length = dist(context, 'HUB', stops[0][0])
    for i in range(len(stops) - 1):
        length += dist(context, stops[i][0], stops[i + 1][0])
    length += dist(context, stops[-1][0], 'HUB')
    return length


def stops_arrival_times(context, stops, depart_time):
    """Arrival time at each stop, in route order. Time complexity: O(S) for S stops."""
    if not stops:
        return []
    times = []
    current_time = depart_time + dist(context, 'HUB', stops[0][0])
    times.append(current_time)
    for i in range(len(stops) - 1):
        current_time += dist(context, stops[i][0], stops[i + 1][0])
        times.append(current_time)
    return times


def verify_and_return(stops, lookup, context):
    """Remove parcels that would be outside their window, then trim stops until capacity,
    range and return cutoff hold. Returns parcel ids stop by stop.
    Time complexity: O(P * (S + P)) for P parcels and S stops on the route."""
    if not stops:
        return []
    depart = context['current_time'] + context['departure_delay']
    while True:
        if not stops:
            return []
        times = stops_arrival_times(context, stops, depart)
        violation_found = False
        for i, stop in enumerate(stops):
            arrival = times[i]
            parcel_list = stop[1]
            for j, parcel_id in enumerate(parcel_list):
                record = lookup[parcel_id]
                if not record['is_filler']:
                    if arrival < record['window_start'] or arrival > record['window_end']:
                        parcel_list.pop(j)
                        if not parcel_list:
                            stops.pop(i)
                        violation_found = True
                        break
            if violation_found:
                break
        if violation_found:
            continue
        total_size = sum(lookup[pid]['size'] for stop in stops for pid in stop[1])
        length = stops_route_length(context, stops)
        size_ok = total_size <= context['capacity'] + 1e-6  # float tolerance, like the simulator
        length_ok = length <= context['max_trip_distance_km']
        cutoff_ok = (depart + length) <= context['return_cutoff']
        if not (size_ok and length_ok and cutoff_ok):
            stops.pop()
            continue
        break
    return [parcel_id for stop in stops for parcel_id in stop[1]]
