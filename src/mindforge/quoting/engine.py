"""Capital-light QuotingEngine (Avellaneda-Stoikov inspired, attention-aware).

Core mechanics:
1. Inventory-based skew:
   - Computes normalized inventory ratio q = position_usd / max_position_usd in [-1, 1].
   - Shifts reservation price DOWN when long (leaning asks aggressively, backing off bids)
     and UP when short (leaning bids aggressively, backing off asks).
2. Dynamic spread widening:
   - Widens half-spread with realized price volatility AND attention_instability
     (plus any toxic-flow or risk spread multipliers).
3. Capital-light size scaling:
   - Scales base_order_usd down as attention_instability increases.
   - Tapers size on the inventory-increasing side as |q| -> 1.0, and switches to
     one-sided unwind quoting once |q| >= 1.0 or when RiskEngine sets UNWIND_ONLY.
4. Venue-safe rounding:
   - Rounds bids down and asks up to exact tick_size, and sizes to lot_size while
     enforcing min_notional_usd.
"""

from __future__ import annotations

import math

import numpy as np

from mindforge.config import MarketConfig, QuotingConfig
from mindforge.types import (
    FairValueEstimate,
    Position,
    QuoteOrder,
    QuotePlan,
    RiskAction,
    RiskDecision,
    Side,
)


def _round_down_tick(price: float, tick: float) -> float:
    return math.floor(price / tick + 1e-12) * tick


def _round_up_tick(price: float, tick: float) -> float:
    return math.ceil(price / tick - 1e-12) * tick


def _round_lot(size: float, lot: float) -> float:
    return math.floor(size / lot + 1e-12) * lot


class QuotingEngine:
    """Generates two-sided (or one-sided unwind) QuotePlans from fair value, inventory, and risk."""

    def __init__(self, config: QuotingConfig) -> None:
        self.config = config

    def build_quotes(
        self,
        market: MarketConfig,
        fv: FairValueEstimate,
        position: Position,
        risk: RiskDecision,
        best_bid: float | None = None,
        best_ask: float | None = None,
    ) -> QuotePlan:
        """Compute target bid/ask ladders for a single market."""
        if not risk.can_quote or fv.fair_value <= 0:
            return QuotePlan(
                symbol=market.symbol,
                reservation_price=fv.fair_value,
                half_spread_bps=self.config.max_half_spread_bps,
                inventory_ratio=0.0,
                bids=(),
                asks=(),
                timestamp=fv.timestamp,
            )

        # 1. Normalized inventory ratio q in [-1, 1] (clamped for skew formula)
        pos_usd = position.notional_usd(fv.microprice)
        raw_q = pos_usd / market.max_position_usd
        q = float(np.clip(raw_q, -1.5, 1.5))

        # 2. Reservation price with inventory skew:
        #    Long (q > 0) -> lower reservation price (encourage sells, discourage buys)
        #    Short (q < 0) -> higher reservation price (encourage buys, discourage sells)
        skew_bps = self.config.inventory_skew_bps * float(np.clip(q, -1.0, 1.0))
        reservation_price = fv.fair_value * (1.0 - skew_bps / 10_000.0)

        # 3. Dynamic half-spread (baseline + realized vol + mindshare instability + toxic flow)
        raw_half_spread = (
            market.base_half_spread_bps
            + self.config.vol_spread_multiplier * fv.realized_vol_bps
            + self.config.instability_spread_bps * fv.attention_instability
            + risk.toxic_flow_addon_bps
        ) * risk.spread_multiplier
        half_spread_bps = float(
            np.clip(
                raw_half_spread,
                self.config.min_half_spread_bps,
                self.config.max_half_spread_bps,
            )
        )

        # 4. Capital-light size scaling under attention instability & risk reduction
        instability_size_factor = 1.0 / (
            1.0 + self.config.instability_size_decay * max(0.0, fv.attention_instability)
        )
        base_clip_usd = market.base_order_usd * instability_size_factor * risk.size_multiplier

        # Taper the inventory-increasing side as |q| approaches 1.0
        bid_side_scale = max(0.0, 1.0 - max(0.0, raw_q))
        ask_side_scale = max(0.0, 1.0 - max(0.0, -raw_q))

        # Enforce UNWIND_ONLY mode if risk engine requires inventory reduction
        if risk.action == RiskAction.UNWIND_ONLY:
            if pos_usd > 0:
                bid_side_scale = 0.0
            elif pos_usd < 0:
                ask_side_scale = 0.0
            else:
                bid_side_scale = 0.0
                ask_side_scale = 0.0

        bids: list[QuoteOrder] = []
        asks: list[QuoteOrder] = []

        for level in range(self.config.levels_per_side):
            level_offset_bps = half_spread_bps + level * self.config.level_spacing_bps

            # --- Bid level ---
            if bid_side_scale > 0.05:
                bid_px = _round_down_tick(
                    reservation_price * (1.0 - level_offset_bps / 10_000.0),
                    market.tick_size,
                )
                # Post-only guard: never cross best_ask and pay taker fees
                if self.config.post_only and best_ask is not None and best_ask > market.tick_size:
                    max_passive_bid = _round_down_tick(
                        best_ask - market.tick_size * (1 + level),
                        market.tick_size,
                    )
                    bid_px = min(bid_px, max_passive_bid)
                bid_usd = base_clip_usd * bid_side_scale
                if bid_px > 0 and bid_usd >= market.min_notional_usd:
                    bid_sz = _round_lot(bid_usd / bid_px, market.lot_size)
                    if bid_sz * bid_px >= market.min_notional_usd:
                        bids.append(
                            QuoteOrder(
                                symbol=market.symbol,
                                side=Side.BUY,
                                price=round(bid_px, 8),
                                size=round(bid_sz, 8),
                                level=level,
                                client_order_id=f"{market.symbol}-B{level}",
                            )
                        )

            # --- Ask level ---
            if ask_side_scale > 0.05:
                ask_px = _round_up_tick(
                    reservation_price * (1.0 + level_offset_bps / 10_000.0),
                    market.tick_size,
                )
                # Post-only guard: never cross best_bid and pay taker fees
                if self.config.post_only and best_bid is not None and best_bid > 0:
                    min_passive_ask = _round_up_tick(
                        best_bid + market.tick_size * (1 + level),
                        market.tick_size,
                    )
                    ask_px = max(ask_px, min_passive_ask)
                ask_usd = base_clip_usd * ask_side_scale
                if ask_px > 0 and ask_usd >= market.min_notional_usd:
                    ask_sz = _round_lot(ask_usd / ask_px, market.lot_size)
                    if ask_sz * ask_px >= market.min_notional_usd:
                        asks.append(
                            QuoteOrder(
                                symbol=market.symbol,
                                side=Side.SELL,
                                price=round(ask_px, 8),
                                size=round(ask_sz, 8),
                                level=level,
                                client_order_id=f"{market.symbol}-A{level}",
                            )
                        )

        return QuotePlan(
            symbol=market.symbol,
            reservation_price=reservation_price,
            half_spread_bps=half_spread_bps,
            inventory_ratio=float(raw_q),
            bids=tuple(bids),
            asks=tuple(asks),
            timestamp=fv.timestamp,
        )
