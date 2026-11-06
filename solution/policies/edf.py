from typing import Dict, Any, List

# Tunable parameters at module level
FILL_THRESHOLD = 0.7
SAFETY_MARGIN = 15.0  # in minutes


## setup helpers ##

def dist(context, a, b):
    return context['travel_distance'][a][b]


def route_length(context, stops):
    ## Total route length from HUB through all stops and back to HUB.
    ## 'stops' is a list of [location_id, [parcel_id_1, ...]].
    if not stops:
        return 0.0

    length = dist(context, 'HUB', stops[0][0])
    for i in range(len(stops) - 1):
        length += dist(context, stops[i][0], stops[i + 1][0])
    length += dist(context, stops[-1][0], 'HUB')

    return length


def arrival_times(context, stops, depart_time):
    ## Arrival time at each stop, in the same order as stops.
    if not stops:
        return []

    times = []
    current_time = depart_time

    current_time += dist(context, 'HUB', stops[0][0])
    times.append(current_time)

    for i in range(len(stops) - 1):
        current_time += dist(context, stops[i][0], stops[i + 1][0])
        times.append(current_time)

    return times


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

        # 2. Let loc = its delivery_location_id and d_hub = dist('HUB', loc)
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
        cutoff_latest = context['return_cutoff'] - (2 * dist('HUB', loc)) - context['departure_delay']
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


## dispatch (temporary, keep this LAST) ##

def dispatch(parcel: Dict[str, Any], context: Dict[str, Any]) -> List[str]:
    # TEMPORARY: always waits. Replace with the Gem's glue once stages 3-5 exist.
    return []