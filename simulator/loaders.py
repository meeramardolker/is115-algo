"""Load and validate parcel, distance and fleet input files.

Invalid input values raise ValueError; file access errors propagate as OSError.
"""

import csv
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterator, Tuple, Union

from .models import Config, Parcel

FilePath = Union[str, Path]


def day_input_paths(data_directory: FilePath, day: str) -> Tuple[Path, Path, Path]:
    """Return demand, distance and config paths for a day ID such as day_001."""

    if not re.fullmatch(r"day_\d{3}", day):
        raise ValueError("day must have the form day_001")
    data = Path(data_directory)
    return data / "demand" / f"{day}.csv", data / "dist.csv", data / "config.json"


def parse_time(value: str) -> float:
    """Convert an HH:MM time to minutes after midnight."""

    if not re.fullmatch(r"\d{2}:\d{2}", value):
        raise ValueError(f"expected HH:MM, got {value!r}")
    hour, minute = map(int, value.split(":"))
    if hour > 23 or minute > 59:
        raise ValueError(f"invalid time {value!r}")
    return float(hour * 60 + minute)


def _check_number(number: float, field: str, *, positive: bool = False) -> float:
    """Return a finite, non-negative number as a float; positive=True also rejects zero."""

    if not math.isfinite(number) or number < 0 or (positive and number == 0):
        rule = "positive" if positive else "non-negative"
        raise ValueError(f"{field} must be finite and {rule}")
    return float(number)


def _csv_number(value: str, field: str, *, positive: bool = False) -> float:
    """Parse a CSV number and validate it using _check_number."""

    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a number") from exc
    return _check_number(number, field, positive=positive)


def _config_number(
    config: Dict[str, Any], field: str, path: FilePath
) -> float:
    """Read and validate a positive number from the configuration."""

    value = config.get(field)
    if type(value) not in (int, float):
        raise ValueError(f"{path}: {field} must be a number")
    try:
        return _check_number(value, field, positive=True)
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc


def _rows(path: FilePath, required: Tuple[str, ...]) -> Iterator[Tuple[int, Dict[str, str]]]:
    """Yield each CSV row's line number and trimmed required fields."""

    with Path(path).open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        fields = reader.fieldnames or []
        missing = set(required) - set(fields)
        if missing:
            raise ValueError(f"{path}: missing columns: {', '.join(sorted(missing))}")
        if len(fields) != len(set(fields)):
            raise ValueError(f"{path}: duplicate column names")
        for row in reader:
            if None in row:  # Values beyond the header have no column name.
                raise ValueError(f"{path}: row {reader.line_num}: too many values")
            # Ignore additional named columns.
            values = {field: (row[field] or "").strip() for field in required}
            blank = next((field for field, value in values.items() if not value), None)
            if blank:
                raise ValueError(f"{path}: row {reader.line_num}: missing {blank}")
            yield reader.line_num, values


def load_parcels(path: FilePath) -> Dict[str, Parcel]:
    """Load parcels by ID, with assignment and delivery fields set to None."""

    required = (
        "parcel_id", "arrival_time", "delivery_location_id",
        "delivery_window_start", "delivery_window_end", "size",
    )
    parcels: Dict[str, Parcel] = {}
    for line, row in _rows(path, required):
        try:
            parcel_id = row["parcel_id"]
            if parcel_id in parcels:
                raise ValueError(f"duplicate parcel_id {parcel_id!r}")
            window_start = parse_time(row["delivery_window_start"])
            window_end = parse_time(row["delivery_window_end"])
            if window_start > window_end:
                raise ValueError("delivery_window_start must not exceed delivery_window_end")
            parcels[parcel_id] = Parcel(
                arrival_time=parse_time(row["arrival_time"]),
                delivery_location_id=row["delivery_location_id"],
                delivery_window_start=window_start,
                delivery_window_end=window_end,
                size=_csv_number(row["size"], "size", positive=True),
                delivery_time=None,
                vehicle_assigned=None,
                trip_id=None,
            )
        except ValueError as exc:
            raise ValueError(f"{path}: row {line}: {exc}") from exc
    return parcels


def load_distances(path: FilePath) -> Dict[str, Dict[str, float]]:
    """Load directed distances in kilometres as distances[origin][destination].

    Every ordered pair of locations, including HUB and each location to itself, is required.
    """

    distances: Dict[str, Dict[str, float]] = {}
    for line, row in _rows(path, ("location_1", "location_2", "dist")):
        try:
            origin, destination = row["location_1"], row["location_2"]
            outgoing = distances.setdefault(origin, {})
            if destination in outgoing:
                raise ValueError(f"duplicate distance pair {origin!r}, {destination!r}")
            outgoing[destination] = _csv_number(row["dist"], "dist")
        except ValueError as exc:
            raise ValueError(f"{path}: row {line}: {exc}") from exc
    locations = sorted(set(distances).union(*distances.values(), {"HUB"}))
    for origin in locations:
        for destination in locations:
            if destination not in distances.get(origin, {}):
                raise ValueError(f"{path}: missing distance {origin!r} -> {destination!r}")
    return distances


def load_config(path: FilePath) -> Config:
    """Load fleet size, per-vehicle capacity and per-trip range from JSON."""

    with Path(path).open(encoding="utf-8-sig") as source:
        try:
            config = json.load(source)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: invalid JSON: {exc.msg}") from exc
    if not isinstance(config, dict):
        raise ValueError(f"{path}: config must be a JSON object")
    count = config.get("number_of_vehicles")
    if type(count) is not int or count < 1:
        raise ValueError(f"{path}: number_of_vehicles must be a positive integer")
    capacity = _config_number(config, "capacity", path)
    vehicle_range = _config_number(config, "vehicle_range_km", path)
    return Config(number_of_vehicles=count, capacity=capacity, vehicle_range_km=vehicle_range)
