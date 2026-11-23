"""Tests for RiskEngine (drawdown kill switch, mindshare shock halt, toxic-flow guard)."""

from datetime import timedelta

from mindforge.config import MarketConfig, RiskConfig
from mindforge.risk.engine import RiskEngine
from mindforge.types import (
    FillEvent,
    OrderBookLevel,
    OrderBookSnapshot,
    Position,
    RiskAction,
    Side,
    SignalSnapshot,
    utc_now,
)


def _book(ts=None, mid: float = 1.0) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol="AIXBT",
        bids=(OrderBookLevel(price=mid - 0.005, size=100.0),),
        asks=(OrderBookLevel(price=mid + 0.005, size=100.0),),
        timestamp=ts or utc_now(),
    )


def test_drawdown_kill_switch_halts_quoting() -> None:
    risk = RiskEngine(RiskConfig(max_drawdown_pct=0.05), starting_equity_usd=1000.0)
    mkt = MarketConfig(symbol="AIXBT")
    now = utc_now()

    # Peak rises to 1050, then drops >5% to 990
    risk.evaluate(mkt, _book(now), Position("AIXBT"), None, 1050.0, 0.0, now)
    decision = risk.evaluate(mkt, _book(now), Position("AIXBT"), None, 990.0, 0.0, now)

    assert decision.action == RiskAction.HALT
    assert not decision.can_quote
    assert risk.kill_switch_triggered


def test_mindshare_shock_halts_and_stale_feed_halts() -> None:
    risk = RiskEngine(
        RiskConfig(mindshare_halt_threshold=2.5, max_feed_age_sec=10.0),
        starting_equity_usd=1000.0,
    )
    mkt = MarketConfig(symbol="AIXBT")
    now = utc_now()

    shock_signal = SignalSnapshot(
        symbol="AIXBT",
        mindshare_score=2.8,
        onchain_score=-1.0,
        hype_divergence=3.8,
        attention_instability=2.9,
        timestamp=now,
    )
    shock_dec = risk.evaluate(mkt, _book(now), Position("AIXBT"), shock_signal, 1000.0, 0.0, now)
    assert shock_dec.action == RiskAction.HALT

    stale_book = _book(ts=now - timedelta(seconds=30))
    stale_dec = risk.evaluate(mkt, stale_book, Position("AIXBT"), None, 1000.0, 0.0, now)
    assert stale_dec.action == RiskAction.HALT


def test_toxic_flow_guard_widens_spread_after_negative_markouts() -> None:
    risk = RiskEngine(
        RiskConfig(toxic_flow_markout_bps=-10.0, toxic_flow_addon_bps=18.0),
        starting_equity_usd=1000.0,
    )
    mkt = MarketConfig(symbol="AIXBT")
    now = utc_now()

    # Simulate 3 adverse fills: bought at 1.00, mid immediately drops to 0.995 (-50 bps)
    for i in range(3):
        risk.record_fill(
            FillEvent(
                symbol="AIXBT",
                side=Side.BUY,
                price=1.00,
                size=10.0,
                fee_usd=0.0,
                order_id=f"f-{i}",
                mid_at_fill=1.00,
                timestamp=now,
            )
        )
        risk.update_markouts("AIXBT", current_mid=0.995)

    dec = risk.evaluate(mkt, _book(now, mid=0.995), Position("AIXBT"), None, 999.0, 0.0, now)
    assert dec.action == RiskAction.WIDEN
    assert dec.toxic_flow_addon_bps == 18.0
