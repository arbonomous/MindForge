"""Performance tracking for PnL, drawdown, fill rate, inventory, and mindshare regimes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from mindforge.types import FillEvent


@dataclass(frozen=True)
class TickRecord:
    step: int
    timestamp: datetime
    equity_usd: float
    realized_pnl_usd: float
    unrealized_pnl_usd: float
    gross_exposure_usd: float
    drawdown_pct: float
    avg_instability: float
    avg_half_spread_bps: float
    regime: str


@dataclass(frozen=True)
class PerformanceSummary:
    starting_equity_usd: float
    ending_equity_usd: float
    total_pnl_usd: float
    total_return_pct: float
    realized_pnl_usd: float
    unrealized_pnl_usd: float
    total_fees_usd: float
    max_drawdown_pct: float
    sharpe_ratio: float
    total_quotes: int
    total_fills: int
    fill_rate_pct: float
    total_volume_usd: float
    avg_half_spread_bps: float
    steps: int


class PerformanceTracker:
    """Records per-tick portfolio curves and fill statistics."""

    def __init__(self, starting_equity_usd: float) -> None:
        self.starting_equity_usd = starting_equity_usd
        self.peak_equity_usd = starting_equity_usd
        self.max_drawdown_pct = 0.0
        self.total_quotes = 0
        self.total_fills = 0
        self.total_volume_usd = 0.0
        self.total_fees_usd = 0.0
        self.history: list[TickRecord] = []
        self.fills: list[FillEvent] = []

    def record_quotes(self, count: int) -> None:
        self.total_quotes += count

    def record_fills(self, fills: list[FillEvent]) -> None:
        for f in fills:
            self.fills.append(f)
            self.total_fills += 1
            self.total_volume_usd += f.notional_usd
            self.total_fees_usd += f.fee_usd

    def record_tick(
        self,
        step: int,
        timestamp: datetime,
        equity_usd: float,
        realized_pnl_usd: float,
        unrealized_pnl_usd: float,
        gross_exposure_usd: float,
        avg_instability: float,
        avg_half_spread_bps: float,
        regime: str,
    ) -> TickRecord:
        if equity_usd > self.peak_equity_usd:
            self.peak_equity_usd = equity_usd
        dd = (
            (self.peak_equity_usd - equity_usd) / self.peak_equity_usd
            if self.peak_equity_usd > 0
            else 0.0
        )
        if dd > self.max_drawdown_pct:
            self.max_drawdown_pct = dd

        rec = TickRecord(
            step=step,
            timestamp=timestamp,
            equity_usd=equity_usd,
            realized_pnl_usd=realized_pnl_usd,
            unrealized_pnl_usd=unrealized_pnl_usd,
            gross_exposure_usd=gross_exposure_usd,
            drawdown_pct=dd,
            avg_instability=avg_instability,
            avg_half_spread_bps=avg_half_spread_bps,
            regime=regime,
        )
        self.history.append(rec)
        return rec

    def summary(self) -> PerformanceSummary:
        if not self.history:
            return PerformanceSummary(
                starting_equity_usd=self.starting_equity_usd,
                ending_equity_usd=self.starting_equity_usd,
                total_pnl_usd=0.0,
                total_return_pct=0.0,
                realized_pnl_usd=0.0,
                unrealized_pnl_usd=0.0,
                total_fees_usd=0.0,
                max_drawdown_pct=0.0,
                sharpe_ratio=0.0,
                total_quotes=self.total_quotes,
                total_fills=self.total_fills,
                fill_rate_pct=0.0,
                total_volume_usd=0.0,
                avg_half_spread_bps=0.0,
                steps=0,
            )

        last = self.history[-1]
        total_pnl = last.equity_usd - self.starting_equity_usd
        ret_pct = (total_pnl / self.starting_equity_usd) * 100.0 if self.starting_equity_usd > 0 else 0.0

        equities = np.asarray([h.equity_usd for h in self.history], dtype=float)
        if len(equities) >= 3:
            rets = np.diff(equities) / np.maximum(equities[:-1], 1e-9)
            std = float(np.std(rets))
            sharpe = float((np.mean(rets) / std) * np.sqrt(len(rets))) if std > 1e-12 else 0.0
        else:
            sharpe = 0.0

        fill_rate = (self.total_fills / self.total_quotes * 100.0) if self.total_quotes > 0 else 0.0
        avg_spread = float(np.mean([h.avg_half_spread_bps for h in self.history]))

        return PerformanceSummary(
            starting_equity_usd=self.starting_equity_usd,
            ending_equity_usd=last.equity_usd,
            total_pnl_usd=total_pnl,
            total_return_pct=ret_pct,
            realized_pnl_usd=last.realized_pnl_usd,
            unrealized_pnl_usd=last.unrealized_pnl_usd,
            total_fees_usd=self.total_fees_usd,
            max_drawdown_pct=self.max_drawdown_pct * 100.0,
            sharpe_ratio=sharpe,
            total_quotes=self.total_quotes,
            total_fills=self.total_fills,
            fill_rate_pct=fill_rate,
            total_volume_usd=self.total_volume_usd,
            avg_half_spread_bps=avg_spread,
            steps=len(self.history),
        )
