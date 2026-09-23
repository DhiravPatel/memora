"""Analytics derived from memory: health, signals, recommendations and portfolio views."""

from memory_engine.analytics.health import HealthFactor, HealthScore, band_for, compute, explain
from memory_engine.analytics.recommend import (
    MAX_RECOMMENDATIONS,
    Recommendation,
    priority_for,
    recommend,
    summarise,
)
from memory_engine.analytics.signals import (
    WINDOW_DAYS,
    ActivityWindow,
    Baseline,
    GoalSnapshot,
    Signal,
    SignalReport,
)
from memory_engine.analytics.signals import compute as compute_signals

__all__ = [
    "MAX_RECOMMENDATIONS",
    "WINDOW_DAYS",
    "ActivityWindow",
    "Baseline",
    "GoalSnapshot",
    "HealthFactor",
    "HealthScore",
    "Recommendation",
    "Signal",
    "SignalReport",
    "band_for",
    "compute",
    "compute_signals",
    "explain",
    "priority_for",
    "recommend",
    "summarise",
]
