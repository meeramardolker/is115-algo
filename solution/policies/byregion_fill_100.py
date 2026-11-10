"""100% capacity fill trigger region policy variant."""

from typing import Dict, List
from solution.policies.byregion import CONFIG, RegionConfig, plan_trip

CONFIG_100 = RegionConfig(
    region_threshold_km=CONFIG.region_threshold_km,
    fill_threshold_share=1.00
)


def dispatch(parcel: Dict[str, Dict], context: Dict) -> List[str]:
    """Dispatch entry point configuring a 100% capacity fill trigger. Time complexity: O(plan_trip)."""
    return plan_trip(parcel, context, CONFIG_100)