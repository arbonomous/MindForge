"""Signal aggregation and pluggable attention forecasting."""

from mindforge.signals.aggregator import SignalAggregator
from mindforge.signals.forecaster import (
    AttentionForecaster,
    CallableForecaster,
    StatisticalEWMAForecaster,
)

__all__ = [
    "SignalAggregator",
    "AttentionForecaster",
    "CallableForecaster",
    "StatisticalEWMAForecaster",
]
