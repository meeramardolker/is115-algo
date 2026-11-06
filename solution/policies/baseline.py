"""Greedy first-fit dispatch in arrival order, skipping parcels that break a hard constraint."""

from typing import Dict, List

from simulator.models import Parcel, SimulationContext

HUB = "HUB"


def dispatch(parcel: Dict[str, Parcel], context: SimulationContext) -> List[str]:
    """Build one trip by trying every waiting parcel in arrival order.

    A parcel that would break capacity, range or the return cutoff is skipped,
    and later parcels are still tried. Parcel IDs break arrival-time ties.
    Preferred delivery windows are ignored. Inputs are snapshots and are never modified.
    """

    distances = context["travel_distance"]
    departure = context["current_time"] + context["departure_delay"]
    max_distance = context["max_trip_distance_km"]
    waiting = sorted(
        (parcel_id for parcel_id, record in parcel.items() if record["vehicle_assigned"] is None),
        key=lambda parcel_id: (parcel[parcel_id]["arrival_time"], parcel_id),
    )
    route: List[str] = []
    load = 0.0
    # km from the hub to the route's last stop, not counting the return leg.
    travelled = 0.0
    location = HUB
    for parcel_id in waiting:
        record = parcel[parcel_id]
        if load + record["size"] > context["capacity"]:
            continue
        destination = record["delivery_location_id"]
        trip_distance = travelled + distances[location][destination]
        round_trip = trip_distance + distances[destination][HUB]
        # At 60 km/h, km also equals travel time in minutes.
        if round_trip > max_distance or departure + round_trip > context["return_cutoff"]:
            continue
        route.append(parcel_id)
        load += record["size"]
        travelled = trip_distance
        location = destination
    return route
