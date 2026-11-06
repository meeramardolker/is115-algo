"""Run one day's parcel deliveries using a selectable dispatch policy."""

import argparse
import copy
import json
import math
from dataclasses import asdict
from importlib import import_module
from inspect import signature
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple, Union

from solution.policies.baseline import dispatch
from .loaders import FilePath, day_input_paths, load_config, load_distances, load_parcels
from .models import (
    Delivery, DispatchPlanning, Event, HubDeparture, Parcel, ParcelArrival, SimulationContext,
    VehicleReturn,
)

DispatchPolicy = Callable[[Dict[str, Parcel], SimulationContext], List[str]]

# Absolute times are minutes after midnight; delays and intervals are minutes.
DAY_START = 8 * 60.0
DEPARTURE_DELAY = 30.0
PLANNING_INTERVAL = 10.0
RETURN_CUTOFF = 18 * 60.0
HUB = "HUB"
MINUTES_PER_KM = 1.0  # Fixed speed of 60 km/h; no service time at stops.

# Events at the same time are processed in this order.
EVENT_PRIORITY = {
    "parcel_arrival": 0,
    "vehicle_return": 1,
    "dispatch_planning": 2,
    "hub_departure": 3,
    "delivery": 4,
}


def _event_order(event: Event) -> Tuple[float, int, Union[str, int]]:
    """Order events by time, priority, then parcel or vehicle ID."""

    entity = (event.parcel_id if isinstance(event, ParcelArrival)
              else 0 if isinstance(event, DispatchPlanning) else event.vehicle_id)
    return event.time, EVENT_PRIORITY[event.event_type], entity


class Simulator:
    """Simulate one day of parcel arrivals, dispatch decisions and deliveries."""

    def __init__(
        self, parcels_csv: FilePath, dist_csv: FilePath, config_json: FilePath,
        *, dispatch_policy: Optional[DispatchPolicy] = None,
    ) -> None:
        """Load inputs and schedule initial events; use baseline unless a policy is supplied."""

        self.config = load_config(config_json)
        self.dispatch_policy = dispatch_policy
        self.parcel = load_parcels(parcels_csv)
        self.distance = load_distances(dist_csv)
        for parcel_id, record in self.parcel.items():
            if record["delivery_location_id"] not in self.distance:
                raise ValueError(
                    f"{parcels_csv}: parcel {parcel_id!r}: unknown delivery_location_id "
                    f"{record['delivery_location_id']!r}"
                )

        # These sets contain arrived parcels only.
        self.unassigned: Set[str] = set()
        self.assigned: Set[str] = set()
        self.delivered: Set[str] = set()
        self.trips: Dict[int, Dict[int, List[str]]] = {}  # [vehicle][trip]: remaining parcel IDs
        self.idle_since: Dict[int, float] = {}  # Idle vehicles only; time they became ready
        self.available_at = {
            vehicle_id: DAY_START for vehicle_id in range(1, self.config["number_of_vehicles"] + 1)
        }

        self.events: List[Event] = [
            ParcelArrival(record["arrival_time"], parcel_id)
            for parcel_id, record in sorted(self.parcel.items())
        ]
        # Every vehicle starts the day by "returning" to the hub at DAY_START.
        self.events.extend(VehicleReturn(DAY_START, vehicle_id) for vehicle_id in self.available_at)
        # No decision after 17:30 can depart within the return cutoff.
        self.events.extend(
            DispatchPlanning(DAY_START + index * PLANNING_INTERVAL)
            for index in range(
                int((RETURN_CUTOFF - DEPARTURE_DELAY - DAY_START) / PLANNING_INTERVAL) + 1
            )
        )
        self.events.sort(key=_event_order)

    def run(self, log_path: FilePath) -> None:
        """Consume the event queue, writing each event as JSON before handling it."""

        handlers = {
            ParcelArrival: self.handle_parcel_arrival,
            VehicleReturn: self.handle_vehicle_return,
            DispatchPlanning: self.handle_dispatch_planning,
            HubDeparture: self.handle_hub_departure,
            Delivery: self.handle_delivery,
        }
        destination = Path(log_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8") as log:
            while self.events:
                event = self.events.pop(0)
                record = {"event_type": event.event_type, **asdict(event)}
                log.write(json.dumps(record, ensure_ascii=False) + "\n")
                log.flush()  # Preserve the triggering event if its handler fails.
                handlers[type(event)](event)

    def _schedule(self, event: Event) -> None:
        """Add an event to the queue, keeping it in processing order."""

        self.events.append(event)
        self.events.sort(key=_event_order)

    def _trip(self, vehicle_id: int, trip_id: int, context: str) -> List[str]:
        """Return a trip's mutable list of remaining parcel IDs; reject empty trips."""

        try:
            trip = self.trips[vehicle_id][trip_id]
        except KeyError as exc:
            raise ValueError(f"{context}: missing trip") from exc
        if not trip:
            raise ValueError(f"{context}: empty trip")
        return trip

    def handle_parcel_arrival(self, event: ParcelArrival) -> None:
        """Make the arriving parcel available for assignment."""

        self.unassigned.add(event.parcel_id)

    def handle_vehicle_return(self, event: VehicleReturn) -> None:
        """Mark the vehicle idle at the hub, ready for the next planning round."""

        if event.vehicle_id not in self.available_at:
            raise ValueError(f"vehicle {event.vehicle_id}: unknown vehicle")
        self.idle_since.setdefault(event.vehicle_id, event.time)  # Preserve earlier idle time.

    def handle_dispatch_planning(self, event: DispatchPlanning) -> None:
        """Consider each idle vehicle once, longest idle first (ID breaks ties)."""

        for vehicle_id in sorted(
            self.idle_since, key=lambda vehicle: (self.idle_since[vehicle], vehicle)
        ):
            self._plan_vehicle(event.time, vehicle_id)

    def _plan_vehicle(self, current_time: float, vehicle_id: int) -> None:
        """Ask the policy for one vehicle's trip, validate it, then assign it."""

        # Hide future arrivals and isolate simulator state from policy changes.
        arrived = self.unassigned | self.assigned | self.delivered
        parcel = {parcel_id: copy.deepcopy(self.parcel[parcel_id]) for parcel_id in sorted(arrived)}
        simulation_context: SimulationContext = {
            "current_time": current_time,
            "vehicle_id": vehicle_id,
            "capacity": self.config["capacity"],
            "travel_distance": copy.deepcopy(self.distance),
            "departure_delay": DEPARTURE_DELAY,
            "return_cutoff": RETURN_CUTOFF,
            "max_trip_distance_km": self.config["vehicle_range_km"],
            "fleet": {
                vehicle: {"available_at": available_at,
                          "max_trip_distance_km": self.config["vehicle_range_km"]}
                for vehicle, available_at in self.available_at.items()
            },
        }
        policy = dispatch if self.dispatch_policy is None else self.dispatch_policy
        selected = policy(parcel, simulation_context)
        if not isinstance(selected, list):
            raise ValueError(f"vehicle {vehicle_id}: dispatch must return a list")
        if not selected:
            return

        departure_time, return_time = self._validate_route(selected, current_time, vehicle_id)

        # Commit changes only after the complete route has passed validation.
        self.available_at[vehicle_id] = return_time
        vehicle_trips = self.trips.setdefault(vehicle_id, {})
        trip_id = max(vehicle_trips, default=0) + 1
        vehicle_trips[trip_id] = list(selected)
        self.unassigned.difference_update(selected)
        self.assigned.update(selected)
        for parcel_id in selected:
            self.parcel[parcel_id]["vehicle_assigned"] = vehicle_id
            self.parcel[parcel_id]["trip_id"] = trip_id
        del self.idle_since[vehicle_id]
        self._schedule(HubDeparture(departure_time, vehicle_id, trip_id))

    def _validate_route(
        self, selected: List[str], current_time: float, vehicle_id: int
    ) -> Tuple[float, float]:
        """Validate a route without changing state; return departure and return times."""

        context = f"vehicle {vehicle_id}"
        seen: Set[str] = set()
        total_size = 0.0
        for parcel_id in selected:
            if not isinstance(parcel_id, str) or parcel_id not in self.parcel:
                raise ValueError(f"{context}: unknown parcel {parcel_id!r}")
            if parcel_id in seen:
                raise ValueError(f"{context}: duplicate parcel {parcel_id!r}")
            if parcel_id not in self.unassigned:
                raise ValueError(f"{context}: parcel {parcel_id!r} is not unassigned")
            seen.add(parcel_id)
            total_size += self.parcel[parcel_id]["size"]
        # Tolerance absorbs float rounding when fractional sizes sum to exactly the capacity.
        if total_size > self.config["capacity"] + 1e-9:
            raise ValueError(f"{context}: trip exceeds capacity")

        locations = [HUB] + [
            self.parcel[parcel_id]["delivery_location_id"] for parcel_id in selected
        ] + [HUB]
        trip_distance = math.fsum(
            self.distance[origin][destination]
            for origin, destination in zip(locations, locations[1:])
        )
        if trip_distance > self.config["vehicle_range_km"] + 1e-9:
            raise ValueError(f"{context}: trip exceeds EV range")
        travel_time = trip_distance * MINUTES_PER_KM
        departure_time = current_time + DEPARTURE_DELAY
        return_time = departure_time + travel_time
        if return_time > RETURN_CUTOFF:
            raise ValueError(
                f"{context}: trip would return after 18:00 "
                f"({return_time:g} minutes since midnight)"
            )

        return departure_time, return_time

    def handle_hub_departure(self, event: HubDeparture) -> None:
        """Schedule the trip's first delivery without changing parcel state."""

        context = f"vehicle {event.vehicle_id}, trip {event.trip_id}"
        trip = self._trip(event.vehicle_id, event.trip_id, context)
        parcel_id = trip[0]
        if parcel_id not in self.parcel:
            raise ValueError(f"{context}: unknown parcel {parcel_id!r}")
        destination = self.parcel[parcel_id]["delivery_location_id"]
        travel_time = self.distance[HUB][destination] * MINUTES_PER_KM
        self._schedule(Delivery(
            event.time + travel_time, event.vehicle_id, event.trip_id, parcel_id
        ))

    def handle_delivery(self, event: Delivery) -> None:
        """Validate the next leg, record delivery and schedule the next event."""

        context = f"vehicle {event.vehicle_id}, trip {event.trip_id}"
        trip = self._trip(event.vehicle_id, event.trip_id, context)
        if event.parcel_id != trip[0]:
            raise ValueError(f"{context}: out-of-order parcel {event.parcel_id!r}")

        # Validate the current and next parcels before changing delivery state.
        seen: Set[str] = set()
        for parcel_id in trip[:2]:
            if parcel_id not in self.parcel:
                raise ValueError(f"{context}: unknown parcel {parcel_id!r}")
            if parcel_id in seen:
                raise ValueError(f"{context}: duplicate parcel {parcel_id!r}")
            seen.add(parcel_id)
            record = self.parcel[parcel_id]
            if (
                parcel_id not in self.assigned
                or record["vehicle_assigned"] != event.vehicle_id
                or record["trip_id"] != event.trip_id
                or parcel_id in self.delivered
                or record["delivery_time"] is not None
            ):
                raise ValueError(f"{context}: assignment mismatch for parcel {parcel_id!r}")

        current = self.parcel[event.parcel_id]
        origin = current["delivery_location_id"]
        next_parcel_id = trip[1] if len(trip) > 1 else None
        destination = (
            self.parcel[next_parcel_id]["delivery_location_id"]
            if next_parcel_id is not None else HUB
        )
        next_time = event.time + self.distance[origin][destination] * MINUTES_PER_KM
        next_event: Event
        if next_parcel_id is not None:
            next_event = Delivery(next_time, event.vehicle_id, event.trip_id, next_parcel_id)
        else:
            next_event = VehicleReturn(next_time, event.vehicle_id)

        current["delivery_time"] = event.time
        self.assigned.remove(event.parcel_id)
        self.delivered.add(event.parcel_id)
        trip.pop(0)
        self._schedule(next_event)


def load_policy(module_name: str) -> DispatchPolicy:
    """Import the named module and validate its dispatch entry point."""

    module = import_module(module_name)
    policy = getattr(module, "dispatch", None)
    if not callable(policy):
        raise ValueError(f"{module_name}: must define a callable dispatch(parcel, context)")
    try:
        signature(policy).bind({}, {})
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{module_name}: dispatch must accept parcel and context") from exc
    return policy


def main(argv: Optional[List[str]] = None) -> int:
    """Run one day from the command line and write the event log."""

    parser = argparse.ArgumentParser(description="Run one day with a selected dispatch policy.")
    parser.add_argument("--policy", default="solution.policies.baseline",
                        help="Python module exposing dispatch(parcel, context)")
    parser.add_argument("--data", type=Path, default=Path("data"),
                        help="directory containing dist.csv, config.json and demand/")
    parser.add_argument("--day", required=True, help="day identifier, for example day_001")
    parser.add_argument("--output", type=Path,
                        help="run directory (default: results/POLICY/DAY)")
    args = parser.parse_args(argv)
    try:
        parcels, distances, config = day_input_paths(args.data, args.day)
        output = args.output if args.output is not None else (
            Path("results") / args.policy.rsplit(".", 1)[-1] / args.day)
        if output.resolve() == args.data.resolve() or args.data.resolve() in output.resolve().parents:
            raise ValueError("output directory must be outside the input data directory")
        log = output / "log.txt"
        if (log.resolve() in {path.resolve() for path in (parcels, distances, config)}
                or args.data.resolve() in log.resolve().parents):
            raise ValueError("event log must not overwrite input data")
        policy = load_policy(args.policy)
        simulator = Simulator(parcels, distances, config, dispatch_policy=policy)
    except (OSError, ValueError, ImportError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(f"Parcels: {len(simulator.parcel)}")
    print(f"Vehicles: {simulator.config['number_of_vehicles']}")
    print(f"Vehicle capacity: {simulator.config['capacity']:g}")
    print(f"EV range per trip: {simulator.config['vehicle_range_km']:g} km (fully recharged at hub)")
    print(f"Scheduled events: {len(simulator.events)}")
    try:
        simulator.run(log)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(f"Delivered: {len(simulator.delivered)}")
    print(f"Event log: {log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
