"""Tests for FairValueEngine (microprice, signal weighting, and shift clamping)."""

from mindforge.config import SignalWeightsConfig
from mindforge.models.fair_value import FairValueEngine
from mindforge.types import (
    ForecastResult,
    FundingSnapshot,
    OrderBookLevel,
    OrderBookSnapshot,
    SignalSnapshot,
)


def _sample_book(bid: float = 1.00, ask: float = 1.02) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol="AI16Z",
        bids=(OrderBookLevel(price=bid, size=100.0),),
        asks=(OrderBookLevel(price=ask, size=100.0),),
    )


def test_fair_value_shifts_up_with_positive_mindshare_and_onchain() -> None:
    cfg = SignalWeightsConfig(
        mindshare_weight_bps=10.0,
        onchain_weight_bps=12.0,
        forecast_weight=0.5,
        max_fv_shift_bps=50.0,
    )
    engine = FairValueEngine(cfg)
    book = _sample_book(1.00, 1.02)  # microprice = 1.01

    signal = SignalSnapshot(
        symbol="AI16Z",
        mindshare_score=1.0,
        onchain_score=1.0,
        hype_divergence=0.0,
        attention_instability=0.2,
    )
    forecast = ForecastResult(bias_bps=10.0, confidence=0.8)
    funding = FundingSnapshot(symbol="AI16Z", hourly_rate=0.0002)

    est = engine.estimate(book, signal=signal, funding=funding, forecast=forecast)
    assert est.microprice == 1.01
    assert est.alpha_bps > 20.0
    assert est.fair_value > est.microprice


def test_fair_value_clamps_extreme_signal_shift() -> None:
    cfg = SignalWeightsConfig(
        mindshare_weight_bps=30.0,
        onchain_weight_bps=30.0,
        max_fv_shift_bps=40.0,
    )
    engine = FairValueEngine(cfg)
    book = _sample_book(1.00, 1.00)
    extreme = SignalSnapshot(
        symbol="AI16Z",
        mindshare_score=3.0,
        onchain_score=3.0,
        hype_divergence=0.0,
        attention_instability=0.5,
    )

    est = engine.estimate(book, signal=extreme)
    assert est.alpha_bps == 40.0
    assert abs(est.fair_value - 1.004) < 1e-9
