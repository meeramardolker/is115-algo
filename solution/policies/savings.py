from typing import Dict, Any, List

# Tunable parameters at module level
FILL_THRESHOLD = 0.7
SAFETY_MARGIN = 15.0  # in minutes


## setup helpers this is the same as edf bc no helper bc merging will be horrendous but we settle later if not this can Run ##

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


## setup ##

savings_cache = None

def get_savings(context):
    
    ## Computes and caches the savings for all pairs of locations.
    
    global savings_cache
    
    # 1. If savings_cache is not None, return it
    if savings_cache is not None:
        return savings_cache
        
    # 2. Let locations = every key of context['travel_distance'] except 'hub' (or 'HUB')
    locations = [
        loc for loc in context['travel_distance'].keys() 
        if loc != 'HUB' and loc != 'hub'
    ]
    
    savings_list = []
    
    # 3. For every ordered pair (i, j) of different locations
    for i in locations:
        for j in locations:
            if i != j:
                # Compute saving = dist(context, i, 'HUB') + dist(context, 'HUB', j) - dist(context, i, j)
                saving = dist(context, i, 'HUB') + dist(context, 'HUB', j) - dist(context, i, j)
                distance_ij = dist(context, i, j)
                
                # 4. Make a list of tuples (saving, i, j, dist(context, i, j))
                savings_list.append((saving, i, j, distance_ij))
                
    # 5. Sort it by saving from highest to lowest, breaking ties by the smaller dist(context, i, j) first
    # Using a key that negates the saving (for descending order) and keeps distance positive (for ascending order)
    savings_list.sort(key=lambda item: (-item[0], item[3]))
    
    # 6. Store the list in savings_cache and return it
    savings_cache = savings_list
    return savings_cache

## prep snapshot ##

def prepare_snapshot(parcel, context):
    candidates = []
    groups = {}
    locked = []

    # 1. & 2. Process parcels
    for parcel_id, p_info in parcel.items():
        if p_info.get("vehicle_assigned") is not None or p_info.get("delivery_time") is not None:
            continue

        loc = p_info["delivery_location_id"]
        d_hub = context["travel_distance"]["HUB"][loc]
        departure_delay = context["departure_delay"]

        latest_dispatch = p_info["delivery_window_end"] - d_hub - departure_delay
        earliest_dispatch = p_info["delivery_window_start"] - d_hub - departure_delay
        is_filler = latest_dispatch < context["current_time"]

        # 3. Store record
        record = {
            "parcel_id": parcel_id,
            "location": loc,
            "size": p_info["size"],
            "window_start": p_info["delivery_window_start"],
            "window_end": p_info["delivery_window_end"],
            "earliest_dispatch": earliest_dispatch,
            "latest_dispatch": latest_dispatch,
            "is_filler": is_filler,
        }
        candidates.append(record)

        # 4. Build groups
        if not is_filler and earliest_dispatch <= context["current_time"]:
            if loc not in groups:
                groups[loc] = {"parcels": [], "load": 0}
            groups[loc]["parcels"].append(record)
            groups[loc]["load"] += record["size"]

    # 5. Process overloaded groups
    locations_to_delete = []
    for loc, group in groups.items():
        if group["load"] > context["capacity"]:
            # Sort by window_end ascending
            group["parcels"].sort(key=lambda x: x["window_end"])
            
            while group["load"] > context["capacity"]:
                chunk = []
                chunk_load = 0
                remaining_parcels = []

                for p in group["parcels"]:
                    if chunk_load + p["size"] <= context["capacity"]:
                        chunk.append(p)
                        chunk_load += p["size"]
                    else:
                        remaining_parcels.append(p)

                if chunk:
                    locked.append({"location": loc, "parcels": chunk, "load": chunk_load})
                    group["parcels"] = remaining_parcels
                    group["load"] -= chunk_load
                else:
                    # Break to prevent infinite loop if no single item fits 
        
                    break
                    
            if not group["parcels"]:
                locations_to_delete.append(loc)

    for loc in locations_to_delete:
        del groups[loc]

    # 6. Return candidates, groups, locked
    return candidates, groups, locked

## merge routes ## 

def merge_routes(groups, locked, context):
    now = context['current_time']
    departure_delay = context['departure_delay']
    depart = now + departure_delay

    def check_route(stops, records):
        total_size = sum(r['size'] for r in records.values())
        if total_size > context['capacity']:
            return False
            
        length = route_length(context, stops)
        if length > context['max_trip_distance_km']:
            return False
            
        if depart + length > context['return_cutoff']:
            return False
            
        offsets = arrival_times(context, stops, 0)
        max_earliest = float('-inf')
        min_latest = float('inf')
        
        for s, stop in enumerate(stops):
            for pid in stop[1]:
                record = records[pid]
                earliest = record['window_start'] - offsets[s] - departure_delay
                latest = record['window_end'] - offsets[s] - departure_delay
                
                if earliest > max_earliest:
                    max_earliest = earliest
                if latest < min_latest:
                    min_latest = latest
                    
        if now < max_earliest or now > min_latest:
            return False
            
        return True

    routes = []
    
    # 1. Build unlocked routes from groups
    for loc, group in groups.items():
        parcel_ids = [p['parcel_id'] for p in group['parcels']]
        records = {p['parcel_id']: p for p in group['parcels']}
        
        route = {
            'stops': [[loc, parcel_ids]],
            'load': group['load'],
            'locked': False,
            'records': records
        }
        if check_route(route['stops'], route['records']):
            routes.append(route)
            
    # 2. Build locked routes from chunks
    for chunk in locked:
        loc = chunk['location']
        parcel_ids = [p['parcel_id'] for p in chunk['parcels']]
        records = {p['parcel_id']: p for p in chunk['parcels']}
        
        route = {
            'stops': [[loc, parcel_ids]],
            'load': chunk['load'],
            'locked': True,
            'records': records
        }
        if check_route(route['stops'], route['records']):
            routes.append(route)

    # 3. Setup tracking dicts and union-find for unlocked routes
    route_of = {}
    for r in routes:
        if not r['locked']:
            loc = r['stops'][0][0]
            route_of[loc] = r
            
    parent = {loc: loc for loc in route_of}
    
    def find(i):
        if parent[i] == i:
            return i
        parent[i] = find(parent[i])
        return parent[i]
        
    def union(i, j):
        root_i = find(i)
        root_j = find(j)
        if root_i != root_j:
            parent[root_j] = root_i

    # 4. Evaluate savings and merge
    for saving, i, j, d in get_savings(context):
        if i not in route_of or j not in route_of:
            continue
            
        if find(i) == find(j):
            continue
            
        a = route_of[i]
        b = route_of[j]
        
        if a['stops'][-1][0] != i or b['stops'][0][0] != j:
            continue
            
        # 5. Create trial merged route
        merged_stops = a['stops'] + b['stops']
        merged_records = {**a['records'], **b['records']}
        merged_load = a['load'] + b['load']
        
        if check_route(merged_stops, merged_records):
            # 6. Accept merge
            union(i, j)
            merged_route = {
                'stops': merged_stops,
                'load': merged_load,
                'locked': False,
                'records': merged_records
            }
            
            for stop in merged_stops:
                route_of[stop[0]] = merged_route
                
            if a in routes:
                routes.remove(a)
            if b in routes:
                routes.remove(b)
            routes.append(merged_route)

    # 7. Return remaining routes
    return routes

## timers per route ## 

def add_timers(routes, context):
    
    ## Calculates and assigns timing boundaries ('send_by' and 'not_before') 
    ## for each generated route.
    
    departure_delay = context['departure_delay']
    return_cutoff = context['return_cutoff']
    
    for route in routes:
        stops = route['stops']
        records = route['records']
        
        # 1. Calculate offsets assuming a departure at 0
        offsets = arrival_times(context, stops, 0)
        
        min_latest = float('inf')
        max_earliest = float('-inf')
        
        # 2. Compute timing limits for every parcel at each stop
        for s, stop in enumerate(stops):
            parcel_ids = stop[1]
            for pid in parcel_ids:
                record = records[pid]
                
                latest = record['window_end'] - offsets[s] - departure_delay
                earliest = record['window_start'] - offsets[s] - departure_delay
                
                if latest < min_latest:
                    min_latest = latest
                if earliest > max_earliest:
                    max_earliest = earliest
                    
        # 3. Set the 'send_by' deadline based on the tightest constraint
        route_len = route_length(context, stops)
        cutoff_limit = return_cutoff - route_len - departure_delay
        
        route['send_by'] = min(min_latest, cutoff_limit)
        
        # 4. Set 'not_before' to prevent arriving before the earliest delivery window
        route['not_before'] = max_earliest
        
    # 5. Return the modified routes list
    return routes

## triggers ##

def find_triggered(routes, context):
    
    ## Evaluates whether each formulated route should be triggered based on deadlines and fill threshold.
    
    now = context['current_time']
    next_round = now + 10.0
    
    triggered_routes = []
    
    for route in routes:
        # Route fires if the deadline minus safety margin is before the next planning round
        deadline_triggered = (route['send_by'] - SAFETY_MARGIN) < next_round
        
        # Route fires if it meets or exceeds the fill threshold capacity
        fill_triggered = route['load'] >= (FILL_THRESHOLD * context['capacity'])
        
        if deadline_triggered or fill_triggered:
            triggered_routes.append(route)
            
    return triggered_routes

## choose parcel load parcel ## 

def choose_and_load(triggered, candidates, context):
    
    ## Selects the most urgent route from the triggered list and attempts to pack 
    ## additional eligible candidate parcels into its existing stops without 
    ## exceeding vehicle capacity.
    
    now = context['current_time']
    capacity = context['capacity']
    
    # 1. Keep only routes in triggered where now >= route['not_before']
    valid_routes = [route for route in triggered if now >= route['not_before']]
    
    if not valid_routes:
        return []
        
    # 2. Pick the route with the smallest send_by
    best_route = min(valid_routes, key=lambda r: r['send_by'])
    
    # 3. Make a copy of its stops list so the original is not changed
    stops_copy = [[stop[0], list(stop[1])] for stop in best_route['stops']]
    load = best_route['load']
    
    # Track existing parcels and locations on the route for fast filtering
    route_parcel_ids = {pid for stop in best_route['stops'] for pid in stop[1]}
    route_locations = {stop[0] for stop in best_route['stops']}
    
    # 4. Collect candidates whose parcel_id is not already on the route and whose location is a stop
    eligible_candidates = [
        c for c in candidates 
        if c['parcel_id'] not in route_parcel_ids and c['location'] in route_locations
    ]
    
    # Filter and sort filler candidates
    fillers = [c for c in eligible_candidates if c['is_filler']]
    fillers.sort(key=lambda c: c['window_end'])
    
    # Add filler candidates
    for c in fillers:
        if load + c['size'] <= capacity:
            # Find the matching stop and append the parcel_id
            for stop in stops_copy:
                if stop[0] == c['location']:
                    stop[1].append(c['parcel_id'])
                    load += c['size']
                    break
                    
    # 5. Filter and sort non-filler candidates whose earliest_dispatch <= now
    non_fillers = [
        c for c in eligible_candidates 
        if not c['is_filler'] and c['earliest_dispatch'] <= now
    ]
    non_fillers.sort(key=lambda c: c['window_end'])
    
    # Add non-filler candidates
    for c in non_fillers:
        if load + c['size'] <= capacity:
            # Find the matching stop and append the parcel_id
            for stop in stops_copy:
                if stop[0] == c['location']:
                    stop[1].append(c['parcel_id'])
                    load += c['size']
                    break
                    
    # 6. Return the copied stops list
    return stops_copy

## verification purposes ## 

def verify_and_return(stops, lookup, context):
    # 1. Return [] if stops is empty.
    if not stops:
        return []

    depart = context['current_time'] + context['departure_delay']

    # 2. Loop
    while True:
        # 5. If stops becomes empty, return [].
        if not stops:
            return []

        # a. Compute arrival times for every stop.
        times = arrival_times(context, stops, depart)

        # b. Find any parcel whose is_filler is false and that arrives outside its window.
        violation_found = False
        for i, stop in enumerate(stops):
            arrival = times[i]
            parcel_list = stop[1]

            for j, parcel_id in enumerate(parcel_list):
                record = lookup[parcel_id]
                if not record['is_filler']:
                    if arrival < record['window_start'] or arrival > record['window_end']:
                        # Remove one parcel at a time
                        parcel_list.pop(j)
                        # Delete the stop if it is now empty
                        if not parcel_list:
                            stops.pop(i)
                        violation_found = True
                        break
            if violation_found:
                break
        
        # Go back to step 2a if a violation was found and removed.
        if violation_found:
            continue

        # c. If no such parcel is found, continue to step 3.
        
        # 3. Check all hard constraints.
        total_size = sum(lookup[pid]['size'] for stop in stops for pid in stop[1])
        length = route_length(context, stops)
        
        size_ok = total_size <= context['capacity']
        length_ok = length <= context['max_trip_distance_km']
        cutoff_ok = (depart + length) <= context['return_cutoff']

        # 4. If any check fails, remove the last stop and its parcels and go back to step 2a.
        if not (size_ok and length_ok and cutoff_ok):
            stops.pop()
            continue

        # All checks passed, break out of the validation loop.
        break

    # 6. Otherwise return the parcel ids in order, stop by stop.
    return [parcel_id for stop in stops for parcel_id in stop[1]]

## final dispatch ##

def dispatch(parcel, context):
    
    ## Main dispatch orchestrator for Alternative Dispatch 2.
    ## Evaluates waiting parcels and assigns a route based on consolidated groups and timing constraints.

    # 1. Prepare the snapshot of candidates, groups, and locked chunks
    candidates, groups, locked = prepare_snapshot(parcel, context)
    
    # 2. If groups and locked are both empty, return []
    if not groups and not locked:
        return []
        
    # 3. Merge eligible routes using the savings heuristic
    routes = merge_routes(groups, locked, context)
    
    # 4. Calculate and add timing boundaries (send_by and not_before)
    routes = add_timers(routes, context)
    
    # 5. Find routes that are triggered by deadlines or fill thresholds
    triggered = find_triggered(routes, context)
    
    # 6. If triggered is empty, return []
    if not triggered:
        return []
        
    # 7. Pick the most urgent route and try to pack additional eligible candidates
    stops = choose_and_load(triggered, candidates, context)
    
    # 8. Make a dict lookup from parcel_id to candidate record
    lookup = {c['parcel_id']: c for c in candidates}
    
    # 9. Verify hard constraints one last time and return the flattened list of parcel IDs
    return verify_and_return(stops, lookup, context)