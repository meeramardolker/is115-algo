from typing import Dict, Any, List

## shared helpers (dist, route length, arrival times, final check) live in helpers.py ##
from solution.policies.helpers import (
    dist,
    stops_route_length as route_length,
    stops_arrival_times as arrival_times,
    verify_and_return,
)

# Tunable parameters at module level
FILL_THRESHOLD = 0.7
SAFETY_MARGIN = 15.0  # in minutes


## stage 1 "snapshot" ##

def prepare_candidates(parcel, context):
    ## Filters waiting parcels and calculates dispatch windows and filler status.
    candidates = []
    departure_delay = context['departure_delay']
    current_time = context['current_time']

    for parcel_id, record in parcel.items():
        # 1. Skip if vehicle_assigned is not None or delivery_time is not None
        if record.get('vehicle_assigned') is not None or record.get('delivery_time') is not None:
            continue

        # 2. Let loc = its delivery_location_id and d_hub = dist(context 'HUB', loc)
        loc = record['delivery_location_id']
        d_hub = dist(context, 'HUB', loc)

        # Extract window times for calculations
        window_start = record['delivery_window_start']
        window_end = record['delivery_window_end']

        # 3. Compute latest_dispatch
        latest_dispatch = window_end - d_hub - departure_delay

        # 4. Compute earliest_dispatch
        earliest_dispatch = window_start - d_hub - departure_delay

        # 5. Set is_filler = True if latest_dispatch < current_time, otherwise False
        is_filler = latest_dispatch < current_time

        # 6. Store and append the record
        candidates.append({
            'parcel_id': parcel_id,
            'location': loc,
            'size': record['size'],
            'window_start': window_start,
            'window_end': window_end,
            'earliest_dispatch': earliest_dispatch,
            'latest_dispatch': latest_dispatch,
            'is_filler': is_filler,
        })

    return candidates


## stage 2 triggers ##

def should_send(candidates, context):

    ## Evaluates whether the current candidates should trigger a dispatch
    ### based on deadlines, return-cutoff, and fill conditions.

    # 1. Return False if candidates is empty
    if not candidates:
        return False

    # 2. Let now = current_time and next_round = now + 10
    now = context['current_time']
    next_round = now + 10.0

    # 3. Deadline trigger
    non_fillers = [c for c in candidates if not c['is_filler']]
    if non_fillers:
        smallest_latest_dispatch = min(c['latest_dispatch'] for c in non_fillers)
        if (smallest_latest_dispatch - SAFETY_MARGIN) < next_round:
            return True

    # 4. Return-cutoff trigger
    for c in candidates:
        loc = c['location']
        # Calculate cutoff_latest
        cutoff_latest = context['return_cutoff'] - (2 * dist(context, 'HUB', loc)) - context['departure_delay']
        if cutoff_latest < next_round:
            return True

    # 5. Fill trigger
    fill_candidates_size = sum(
        c['size'] for c in candidates if c['earliest_dispatch'] <= now
    )
    if fill_candidates_size >= FILL_THRESHOLD * context['capacity']:
        return True

    # 6. Otherwise return False
    return False

## sorting by deadline this is the actual edf ##

def order_candidates(candidates, current_time):
    # 1. Remove every candidate whose earliest_dispatch > current_time
    valid_candidates = [c for c in candidates if c['earliest_dispatch'] <= current_time]

    # 2. Split what remains into non_filler (is_filler false) and filler (is_filler true)
    non_filler = [c for c in valid_candidates if not c.get('is_filler')]
    filler = [c for c in valid_candidates if c.get('is_filler')]

    # 3. Sort each list by window_end ascending, breaking ties by earliest_dispatch ascending
    def sort_key(candidate):
        return (candidate['window_end'], candidate['earliest_dispatch'])

    non_filler.sort(key=sort_key)
    filler.sort(key=sort_key)

    # 4. Return non_filler followed by filler
    return non_filler + filler

## greedy 1 nearest neighbour for route building ##

import copy

def build_route(ordered, context):
    """
    Constructs a route by iteratively evaluating and inserting candidates.
    Returns a list of stops: [[location, [parcel_id_1, ...]], ...]
    """
    # Setup
    depart = context['current_time'] + context['departure_delay']
    records = {c['parcel_id']: c for c in ordered}
    stops = []
    load = 0.0

    # 1. If ordered is empty, return an empty list.
    if not ordered:
        return []

    # 2. Seed: take the first candidate
    seed = ordered[0]
    stops = [[seed['location'], [seed['parcel_id']]]]
    load = seed['size']

    # 3. For each remaining candidate in order
    for candidate in ordered[1:]:
        c_id = candidate['parcel_id']
        c_loc = candidate['location']
        c_size = candidate['size']

        # a. Skip it if load + size > capacity
        if load + c_size > context['capacity']:
            continue

        trial_route = None

        # Check if its location is already a stop in stops
        existing_stop_idx = -1
        for i, stop in enumerate(stops):
            if stop[0] == c_loc:
                existing_stop_idx = i
                break

        if existing_stop_idx != -1:
            # b. Make a trial route that is a copy of stops with its parcel_id appended
            trial_route = copy.deepcopy(stops)
            trial_route[existing_stop_idx][1].append(c_id)
        else:
            # c. Try inserting a new stop at every position to find the smallest extra_km
            best_pos = -1
            min_extra_km = float('inf')

            for i in range(len(stops) + 1):
                prev_loc = 'HUB' if i == 0 else stops[i-1][0]
                next_loc = 'HUB' if i == len(stops) else stops[i][0]

                extra_km = (dist(context, prev_loc, c_loc)
                          + dist(context, c_loc, next_loc)
                          - dist(context, prev_loc, next_loc))

                if extra_km < min_extra_km:
                    min_extra_km = extra_km
                    best_pos = i

            # Make a trial route from the position with the smallest extra_km
            trial_route = copy.deepcopy(stops)
            trial_route.insert(best_pos, [c_loc, [c_id]])

        # d. Accept the trial route only if all hard constraints hold
        length = route_length(context, trial_route)

        if length > context['max_trip_distance_km']:
            continue

        if depart + length > context['return_cutoff']:
            continue

        arr_times = arrival_times(context, trial_route, depart)
        timing_ok = True

        for stop_idx, stop in enumerate(trial_route):
            stop_arrival = arr_times[stop_idx]
            for p_id in stop[1]:
                p_record = records[p_id]
                if not p_record['is_filler']:
                    # Timing check for non-filler parcels
                    if stop_arrival < p_record['window_start'] or stop_arrival > p_record['window_end']:
                        timing_ok = False
                        break
            if not timing_ok:
                break

        # e. If accepted, replace stops with the trial route and add the size to load
        if timing_ok:
            stops = trial_route
            load += c_size

    # 4. Return stops
    return stops

## final dispatch ##

def dispatch(parcel, context):
    """
    Main dispatch orchestrator wrapping the helper functions.
    """
    # 1. Prepare candidates
    candidates = prepare_candidates(parcel, context)

    # 2. Check if we should send
    if not should_send(candidates, context):
        return []

    # 3. Order the candidates
    ordered = order_candidates(candidates, context['current_time'])

    # 4. Build the route
    stops = build_route(ordered, context)

    # 5. Make a dict lookup from parcel_id to candidate record
    lookup = {c['parcel_id']: c for c in candidates}

    # 6. Verify constraints and return the final route
    return verify_and_return(stops, lookup, context)