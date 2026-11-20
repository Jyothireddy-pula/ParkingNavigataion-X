"""Compatibility imports for the optimizer now maintained in legacy.flat_optimizer."""

from legacy.flat_optimizer.active_engine import (
    ASSIGNMENT_BLOCK,
    GATE_NAMES,
    LOT_NAMES,
    PROACTIVE_CURRENT_MAX,
    PROACTIVE_FORECAST_THRESHOLD,
    PROFILE_LABELS,
    RECORDED_MAX_AGE_MIN,
    TERM_NAMES,
    WEIGHT_PROFILES,
    CostComponents,
    MultiObjectiveOptimizer,
    describe_profiles,
)

__all__ = [
    "ASSIGNMENT_BLOCK", "GATE_NAMES", "LOT_NAMES", "PROACTIVE_CURRENT_MAX",
    "PROACTIVE_FORECAST_THRESHOLD", "PROFILE_LABELS", "RECORDED_MAX_AGE_MIN",
    "TERM_NAMES", "WEIGHT_PROFILES", "CostComponents", "MultiObjectiveOptimizer",
    "describe_profiles",
]
