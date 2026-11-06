"""Define input records, dispatch snapshots and immutable simulation events.

Absolute times are minutes after midnight; distances are kilometres.
"""

from dataclasses import dataclass
from typing import ClassVar, Dict, Literal, Optional, TypedDict, Union


class Config(TypedDict):
    """Fleet settings loaded from config.json."""

    number_of_vehicles: int
    capacity: float  # Capacity units per vehicle
    vehicle_range_km: float  # Full per-trip range, including return to the hub


class Parcel(TypedDict):
    """Parcel inputs and assignment/delivery state; size is in capacity units."""

    arrival_time: float
    delivery_location_id: str
    delivery_window_start: float
    delivery_window_end: float
    size: float
    delivery_time: Optional[float]  # None until delivery
    vehicle_assigned: Optional[int]  # Set when the trip is committed; retained after delivery
    trip_id: Optional[int]  # Set with vehicle_assigned; each vehicle numbers its trips 1, 2, ...


class FleetVehicle(TypedDict):
    """Vehicle availability and range supplied to a dispatch policy."""

    available_at: float  # Time the vehicle is ready at the hub for another trip (minutes after midnight)
    max_trip_distance_km: float  # Full per-trip limit, not remaining battery range


class SimulationContext(TypedDict):
    """Snapshot passed to the policy for one dispatch decision."""

    current_time: float
    vehicle_id: int
    capacity: float  # Capacity of the vehicle being planned
    travel_distance: Dict[str, Dict[str, float]]  # [origin][destination], directed
    departure_delay: float  # Minutes from current_time until departure
    return_cutoff: float  # Latest time the vehicle may be back at the hub
    max_trip_distance_km: float  # Includes the return to the hub
    fleet: Dict[int, FleetVehicle]  # Keyed by vehicle ID


@dataclass(frozen=True)
class ParcelArrival:
    """Make a parcel available at the hub."""

    time: float
    parcel_id: str
    event_type: ClassVar[Literal["parcel_arrival"]] = "parcel_arrival"


@dataclass(frozen=True)
class VehicleReturn:
    """Make a vehicle available at the hub, including at the start of the day."""

    time: float
    vehicle_id: int
    event_type: ClassVar[Literal["vehicle_return"]] = "vehicle_return"


@dataclass(frozen=True)
class DispatchPlanning:
    """Trigger a planning round for all idle vehicles."""

    time: float
    event_type: ClassVar[Literal["dispatch_planning"]] = "dispatch_planning"


@dataclass(frozen=True)
class HubDeparture:
    """Start a committed trip after its preparation delay."""

    time: float
    vehicle_id: int
    trip_id: int
    event_type: ClassVar[Literal["hub_departure"]] = "hub_departure"


@dataclass(frozen=True)
class Delivery:
    """Record one parcel delivery on a vehicle's trip."""

    time: float
    vehicle_id: int
    trip_id: int
    parcel_id: str
    event_type: ClassVar[Literal["delivery"]] = "delivery"


Event = Union[ParcelArrival, VehicleReturn, DispatchPlanning, HubDeparture, Delivery]
