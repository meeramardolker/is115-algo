"""Student performance monitor with comprehensive project metrics calculation."""

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from simulator.loaders import day_input_paths, parse_time


def calculate_metrics(
    parcels: List[Dict[str, str]],
    distances: List[Dict[str, str]],
    config: Dict[str, Any],
    events: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Return a JSON-serializable dictionary for the chosen performance metric.

    parcels and distances are the rows of the day's CSV files, config is the
    contents of config.json, and events are the records from the run's log.
    CSV values are strings, so convert them before calculating. In particular,
    parcel times are "HH:MM" strings while event times are minutes after
    midnight: "17:00" is 17 * 60 = 1020.
    """
    # Build distance lookups: dist_map[origin][destination] -> float
    dist_map: Dict[str, Dict[str, float]] = {}
    for row in distances:
        orig = row["location_1"]
        dest = row["location_2"]
        d = float(row["dist"])
        dist_map.setdefault(orig, {})[dest] = d

    # Collect delivery events map: parcel_id -> delivery_time (float)
    delivery_events: Dict[str, float] = {}
    number_of_trips = 0

    # Track vehicle's last known location for distance calculation
    vehicle_locations: Dict[int, str] = {}
    total_distance_km = 0.0

    for ev in events:
        event_type = ev.get("event_type")

        if event_type == "hub_departure":
            number_of_trips += 1
            v_id = ev["vehicle_id"]
            vehicle_locations[v_id] = "HUB"

        elif event_type == "delivery":
            parcel_id = ev["parcel_id"]
            delivery_events[parcel_id] = float(ev["time"])
            v_id = ev["vehicle_id"]

            # Distance leg calculation
            current_loc = vehicle_locations.get(v_id, "HUB")
            # Find destination location from parcels list
            dest_loc = "HUB"
            for p in parcels:
                if p["parcel_id"] == parcel_id:
                    dest_loc = p["delivery_location_id"]
                    break
            total_distance_km += dist_map[current_loc][dest_loc]
            vehicle_locations[v_id] = dest_loc

        elif event_type == "vehicle_return":
            v_id = ev["vehicle_id"]
            current_loc = vehicle_locations.get(v_id, "HUB")
            total_distance_km += dist_map[current_loc]["HUB"]
            vehicle_locations[v_id] = "HUB"

    total_parcels = len(parcels)
    on_time_count = 0
    early_count = 0
    late_count = 0
    undelivered_count = 0

    parcels_by_window_type = {"afternoon": 0, "open": 0, "tight": 0}
    on_time_by_window_type = {"afternoon": 0, "open": 0, "tight": 0}

    tol = 1e-6

    for p in parcels:
        arrival_time = parse_time(p["arrival_time"])
        window_start = parse_time(p.get("delivery_window_start", "00:00"))
        window_end = parse_time(p["delivery_window_end"])

        # Window classification
        if window_start > arrival_time:
            w_type = "afternoon"
        elif window_end >= 1080.0:  # 18:00 = 1080 minutes
            w_type = "open"
        else:
            w_type = "tight"

        parcels_by_window_type[w_type] += 1

        p_id = p["parcel_id"]
        if p_id in delivery_events:
            deliv_time = delivery_events[p_id]
            if deliv_time < window_start - tol:
                early_count += 1
            elif deliv_time > window_end + tol:
                late_count += 1
            else:
                on_time_count += 1
                on_time_by_window_type[w_type] += 1
        else:
            undelivered_count += 1

    on_time_rate = on_time_count / total_parcels if total_parcels > 0 else 0.0
    delivered_count = on_time_count + early_count + late_count
    delivery_rate = round(delivered_count / total_parcels, 6) if total_parcels > 0 else 0.0

    on_time_rate_by_window_type = {}
    for w_type, total_count in parcels_by_window_type.items():
        if total_count > 0:
            rate = on_time_by_window_type[w_type] / total_count
        else:
            rate = 0.0
        on_time_rate_by_window_type[w_type] = rate

    return {
        "on_time_rate": on_time_rate,
        "delivery_rate": delivery_rate,
        "total_parcels": total_parcels,
        "on_time_count": on_time_count,
        "early_count": early_count,
        "late_count": late_count,
        "undelivered_count": undelivered_count,
        "on_time_rate_by_window_type": on_time_rate_by_window_type,
        "parcels_by_window_type": parcels_by_window_type,
        "number_of_trips": number_of_trips,
        "total_distance_km": total_distance_km,
    }


def main(argv: Optional[List[str]] = None) -> int:
    """Load one day's inputs and event log, then write the metrics report."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"),
                        help="directory containing dist.csv, config.json and demand/")
    parser.add_argument("--day", required=True, help="day identifier, for example day_001")
    parser.add_argument("--log", type=Path, required=True,
                        help="event log from a simulator run")
    parser.add_argument("--output", type=Path,
                        help="JSON report (default: metrics.json beside the selected log)")
    args = parser.parse_args(argv)
    try:
        parcel_path, distance_path, config_path = day_input_paths(args.data, args.day)
        output = args.output if args.output is not None else args.log.with_name("metrics.json")
        # The input files all live in the data directory, so this also protects them.
        if output.resolve() == args.data.resolve() or args.data.resolve() in output.resolve().parents:
            raise ValueError("report output must be outside the input data directory")
        if output.resolve() == args.log.resolve():
            raise ValueError("report output must not overwrite the log")
        with parcel_path.open(newline="", encoding="utf-8-sig") as stream:
            parcels = list(csv.DictReader(stream))
        with distance_path.open(newline="", encoding="utf-8-sig") as stream:
            distances = list(csv.DictReader(stream))
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        with args.log.open(encoding="utf-8") as stream:
            events = [json.loads(line) for line in stream if line.strip()]
        metrics = calculate_metrics(parcels, distances, config, events)
        report = json.dumps(metrics, indent=2, allow_nan=False) + "\n"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
    except (OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(f"Metrics report: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())