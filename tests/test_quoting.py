"""Tests for QuotingEngine (inventory skew, dynamic spread widening, unwind-only)."""

from mindforge.config import MarketConfig, QuotingConfig
from mindforge.quoting.engine import QuotingEngine
from mindforge.types import FairValueEstimate, Position, RiskAction, RiskDecision


def _market() -> MarketConfig:
    return MarketConfig(
        symbol="VIRTUAL",
        tick_size=0.0005,
        lot_size=1.0,
        min_notional_usd=10.0,
        base_order_usd=40.0,
        max_position_usd=100.0,
        base_half_spread_bps=15.0,
    )


def _fv(instability: float = 0.0, vol_bps: float = 10.0) -> FairValueEstimate:
    return FairValueEstimate(
        symbol="VIRTUAL",
        microprice=2.0,
        fair_value=2.0,
        alpha_bps=0.0,
        realized_vol_bps=vol_bps,
        attention_instability=instability,
    )


def test_inventory_skew_leans_asks_when_long_and_bids_when_short() -> None:
    engine = QuotingEngine(QuotingConfig(inventory_skew_bps=30.0, levels_per_side=1))
    mkt = _market()
    fv = _fv()
    allow = RiskDecision(action=RiskAction.ALLOW)

    flat_plan = engine.build_quotes(mkt, fv, Position(symbol="VIRTUAL", size=0.0), allow)
    long_plan = engine.build_quotes(mkt, fv, Position(symbol="VIRTUAL", size=25.0), allow)  # +$50 long
    short_plan = engine.build_quotes(mkt, fv, Position(symbol="VIRTUAL", size=-25.0), allow)  # -$50 short

    assert long_plan.reservation_price < flat_plan.reservation_price
    assert short_plan.reservation_price > flat_plan.reservation_price
    assert long_plan.asks[0].price < flat_plan.asks[0].price
    assert short_plan.bids[0].price > flat_plan.bids[0].price


def test_dynamic_spread_widens_and_size_shrinks_under_attention_instability() -> None:
    engine = QuotingEngine(
        QuotingConfig(
            instability_spread_bps=25.0,
            instability_size_decay=0.8,
            levels_per_side=1,
        )
    )
    mkt = _market()
    pos = Position(symbol="VIRTUAL", size=0.0)
    allow = RiskDecision(action=RiskAction.ALLOW)

    calm = engine.build_quotes(mkt, _fv(instability=0.0), pos, allow)
    noisy = engine.build_quotes(mkt, _fv(instability=1.5), pos, allow)

    assert noisy.half_spread_bps > calm.half_spread_bps
    assert noisy.bids[0].size < calm.bids[0].size
    assert noisy.asks[0].size < calm.asks[0].size


def test_max_inventory_switches_to_one_sided_unwind() -> None:
    engine = QuotingEngine(QuotingConfig(levels_per_side=1))
    mkt = _market()
    # +$100 long == 100% of max_position_usd -> should stop quoting bids, keep asks
    max_long = Position(symbol="VIRTUAL", size=50.0)
    plan = engine.build_quotes(
        mkt,
        _fv(),
        max_long,
        RiskDecision(action=RiskAction.UNWIND_ONLY),
    )

    assert len(plan.bids) == 0
    assert len(plan.asks) == 1
