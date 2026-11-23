"""RiskEngine enforcing capital-light limits, drawdown kill switch,
adverse-selection (toxic-flow) guards, and mindshare-shock circuit breakers.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime

import numpy as np

from mindforge.config import MarketConfig, RiskConfig
from mindforge.types import (
    FillEvent,
    OrderBookSnapshot,
    Position,
    RiskAction,
    RiskDecision,
    Side,
    SignalSnapshot,
)


class RiskEngine:
    """Evaluates portfolio & per-market safety before every quoting cycle."""

    def __init__(self, config: RiskConfig, starting_equity_usd: float) -> None:
        self.config = config
        self.starting_equity_usd = starting_equity_usd
        self.peak_equity_usd = starting_equity_usd
        self.kill_switch_triggered = False
        self.kill_switch_reason: str = ""

        # Track recent fills and their short-horizon markouts (bps) per symbol
        self._pending_fills: dict[str, deque[FillEvent]] = defaultdict(lambda: deque(maxlen=20))
        self._markouts_bps: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=15))

    def record_fill(self, fill: FillEvent) -> None:
        """Register a fill so subsequent ticks can measure adverse-selection markout."""
        self._pending_fills[fill.symbol].append(fill)

    def update_markouts(self, symbol: str, current_mid: float) -> None:
        """Evaluate short-horizon markout on pending fills for adverse-selection detection."""
        if current_mid <= 0:
            return
        pending = self._pending_fills[symbol]
        while pending:
            fill = pending.popleft()
            if fill.price <= 0:
                continue
            # Positive markout = favorable post-fill move; negative = adverse selection
            if fill.side == Side.BUY:
                markout_bps = (current_mid - fill.price) / fill.price * 10_000.0
            else:
                markout_bps = (fill.price - current_mid) / fill.price * 10_000.0
            self._markouts_bps[symbol].append(markout_bps)

    def avg_markout_bps(self, symbol: str) -> float:
        hist = self._markouts_bps[symbol]
        if not hist:
            return 0.0
        return float(np.mean(hist))

    def evaluate(
        self,
        market: MarketConfig,
        book: OrderBookSnapshot,
        position: Position,
        signal: SignalSnapshot | None,
        total_equity_usd: float,
        gross_exposure_usd: float,
        now: datetime,
    ) -> RiskDecision:
        """Run all risk checks and return a structured RiskDecision."""
        reasons: list[str] = []

        # 1. Update high-water mark & check drawdown kill switch (latched until reset)
        if total_equity_usd > self.peak_equity_usd:
            self.peak_equity_usd = total_equity_usd

        drawdown_pct = (
            (self.peak_equity_usd - total_equity_usd) / self.peak_equity_usd
            if self.peak_equity_usd > 0
            else 0.0
        )
        if drawdown_pct >= self.config.max_drawdown_pct:
            self.kill_switch_triggered = True
            self.kill_switch_reason = (
                f"Drawdown kill switch ({drawdown_pct:.1%} >= {self.config.max_drawdown_pct:.1%})"
            )

        daily_pnl = total_equity_usd - self.starting_equity_usd
        if daily_pnl <= -self.config.max_daily_loss_usd:
            self.kill_switch_triggered = True
            self.kill_switch_reason = (
                f"Max daily loss reached (${daily_pnl:.2f} <= -${self.config.max_daily_loss_usd:.2f})"
            )

        if self.kill_switch_triggered:
            return RiskDecision(
                action=RiskAction.HALT,
                size_multiplier=0.0,
                spread_multiplier=2.0,
                reasons=(self.kill_switch_reason,),
            )

        # 2. Stale market feed check
        feed_age = abs((now - book.timestamp).total_seconds())
        if feed_age > self.config.max_feed_age_sec:
            return RiskDecision(
                action=RiskAction.HALT,
                size_multiplier=0.0,
                spread_multiplier=2.0,
                reasons=(f"Stale orderbook ({feed_age:.1f}s > {self.config.max_feed_age_sec:.1f}s)",),
            )

        # 3. Mindshare shock circuit breaker
        action = RiskAction.ALLOW
        size_mult = 1.0
        spread_mult = 1.0
        toxic_addon_bps = 0.0

        if signal is not None:
            if signal.attention_instability >= self.config.mindshare_halt_threshold:
                return RiskDecision(
                    action=RiskAction.HALT,
                    size_multiplier=0.0,
                    spread_multiplier=2.5,
                    reasons=(
                        f"Mindshare shock halt (instability={signal.attention_instability:.2f})",
                    ),
                )
            if signal.attention_instability >= self.config.mindshare_widen_threshold:
                action = RiskAction.WIDEN
                size_mult *= 0.55
                spread_mult *= 1.45
                reasons.append(f"High attention instability ({signal.attention_instability:.2f})")

        # 4. Adverse-selection / toxic-flow guard based on recent fill markouts
        avg_markout = self.avg_markout_bps(market.symbol)
        if (
            len(self._markouts_bps[market.symbol]) >= 3
            and avg_markout <= self.config.toxic_flow_markout_bps
        ):
            action = RiskAction.WIDEN
            size_mult *= 0.65
            toxic_addon_bps = self.config.toxic_flow_addon_bps
            reasons.append(f"Toxic flow guard (avg markout {avg_markout:.1f} bps)")

        # 5. Per-market inventory cap & portfolio gross exposure cap
        pos_abs_usd = abs(position.notional_usd(book.microprice))
        if pos_abs_usd >= market.max_position_usd:
            action = RiskAction.UNWIND_ONLY
            reasons.append(
                f"Max position cap (${pos_abs_usd:.1f} >= ${market.max_position_usd:.1f})"
            )
        elif gross_exposure_usd >= self.config.max_gross_exposure_usd:
            action = RiskAction.UNWIND_ONLY
            reasons.append(
                f"Max gross exposure (${gross_exposure_usd:.1f} >= ${self.config.max_gross_exposure_usd:.1f})"
            )

        return RiskDecision(
            action=action,
            size_multiplier=size_mult,
            spread_multiplier=spread_mult,
            toxic_flow_addon_bps=toxic_addon_bps,
            reasons=tuple(reasons),
        )

    def reset_kill_switch(self, new_equity_usd: float) -> None:
        """Manual operator reset after reviewing a halt."""
        self.kill_switch_triggered = False
        self.kill_switch_reason = ""
        self.peak_equity_usd = new_equity_usd
