"""Summarise performance metrics across simulation runs for each policy."""

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional


def find_metrics_files(results_dir: Path) -> Dict[str, List[Path]]:
    """Find all metrics.json files under results/<policy>/day_XXX/metrics.json."""
    policy_files: Dict[str, List[Path]] = {}
    if not results_dir.exists():
        return policy_files

    for policy_dir in sorted(results_dir.iterdir()):
        if policy_dir.is_dir():
            policy_name = policy_dir.name
            metrics_list = sorted(policy_dir.glob("day_*/metrics.json"))
            if metrics_list:
                policy_files[policy_name] = metrics_list
    return policy_files


def summarise_policy(metrics_files: List[Path]) -> Dict[str, float]:
    """Total the counts across all days for a policy and compute pooled rates."""
    total_parcels = 0
    on_time_count = 0
    early_count = 0
    late_count = 0
    undelivered_count = 0
    number_of_trips = 0

    for m_file in metrics_files:
        data = json.loads(m_file.read_text(encoding="utf-8"))
        total_parcels += data.get("total_parcels", 0)
        on_time_count += data.get("on_time_count", 0)
        early_count += data.get("early_count", 0)
        late_count += data.get("late_count", 0)
        undelivered_count += data.get("undelivered_count", 0)
        number_of_trips += data.get("number_of_trips", 0)

    delivered_count = on_time_count + early_count + late_count
    on_time_pct = (on_time_count / total_parcels * 100.0) if total_parcels > 0 else 0.0
    delivered_pct = (delivered_count / total_parcels * 100.0) if total_parcels > 0 else 0.0

    return {
        "on_time_pct": on_time_pct,
        "delivered_pct": delivered_pct,
        "early": early_count,
        "late": late_count,
        "undelivered": undelivered_count,
        "trips": number_of_trips,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Summarise simulation metrics across policies.")
    parser.add_argument("--results", type=Path, default=Path("results"),
                        help="directory containing the policy results folders")
    parser.add_argument("--per-day", action="store_true",
                        help="print each day's metrics alongside the pooled summary")
    args = parser.parse_args(argv)

    policy_files = find_metrics_files(args.results)
    if not policy_files:
        print(f"No metrics files found under {args.results}")
        return 0

    header = f"{'policy':<30} {'on-time %':>10} {'delivered %':>12} {'early':>8} {'late':>8} {'undelivered':>12} {'trips':>8}"
    print(header)
    print("-" * len(header))

    for policy, files in policy_files.items():
        if args.per_day:
            for m_file in files:
                day_name = m_file.parent.name
                data = json.loads(m_file.read_text(encoding="utf-8"))
                tp = data.get("total_parcels", 0)
                ot = data.get("on_time_count", 0)
                er = data.get("early_count", 0)
                lt = data.get("late_count", 0)
                ud = data.get("undelivered_count", 0)
                tr = data.get("number_of_trips", 0)
                dl = ot + er + lt

                ot_pct = (ot / tp * 100.0) if tp > 0 else 0.0
                dl_pct = (dl / tp * 100.0) if tp > 0 else 0.0

                label = f"{policy}/{day_name}"
                print(f"{label:<30} {ot_pct:>9.2f}% {dl_pct:>11.2f}% {er:>8} {lt:>8} {ud:>12} {tr:>8}")

        summary = summarise_policy(files)
        print(
            f"{policy:<30} "
            f"{summary['on_time_pct']:>9.2f}% "
            f"{summary['delivered_pct']:>11.2f}% "
            f"{summary['early']:>8} "
            f"{summary['late']:>8} "
            f"{summary['undelivered']:>12} "
            f"{summary['trips']:>8}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
