"""Observability: performance metrics, Rich terminal dashboard, and static HTML reports."""

from mindforge.reporting.dashboard import RichDashboard
from mindforge.reporting.html_report import generate_html_report
from mindforge.reporting.metrics import PerformanceSummary, PerformanceTracker, TickRecord

__all__ = [
    "PerformanceSummary",
    "PerformanceTracker",
    "TickRecord",
    "RichDashboard",
    "generate_html_report",
]
