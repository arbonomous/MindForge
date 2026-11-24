from mindforge.strategy.trend_follower import EMACrossTrendFollower


def test_trend_follower_warmup_and_deadband() -> None:
    tf = EMACrossTrendFollower(symbols=("BTC",), fast_hours=3, slow_hours=10, deadband_pct=0.005)
    # Not enough bars yet -> stays FLAT
    tf.seed_history("BTC", [100.0] * 9)
    assert tf.desired_side("BTC") == 0

    # Flat prices -> within deadband -> FLAT
    tf.update_ema("BTC", 100.0)
    assert tf.desired_side("BTC") == 0

    # Uptrend pushes fast EMA > slow EMA * 1.005 -> LONG
    for p in [102.0, 104.0, 106.0, 108.0]:
        tf.update_ema("BTC", p)
    assert tf.desired_side("BTC") == 1


def test_trend_follower_long_and_short_accounting_and_funding() -> None:
    tf = EMACrossTrendFollower(
        symbols=("BTC", "ETH"),
        starting_equity_usd=100.0,
        fast_hours=2,
        slow_hours=5,
        deadband_pct=0.003,
        leverage=2.0,
        taker_fee_bps=4.5,
        slippage_bps=4.0,
        bridge_gas_usd=0.15,
    )
    # BTC trending up -> LONG; ETH trending down -> SHORT
    tf.seed_history("BTC", [100.0, 100.0, 100.0, 105.0, 110.0])
    tf.seed_history("ETH", [100.0, 100.0, 100.0, 95.0, 90.0])
    actions = tf.rebalance({"BTC": 110.0, "ETH": 90.0})
    assert len(actions) == 2
    assert tf.positions["BTC"].side == 1
    assert tf.positions["ETH"].side == -1

    # Positive funding: BTC long pays, ETH short receives
    funding_delta = tf.apply_hourly_funding({"BTC": 110.0, "ETH": 90.0}, {"BTC": 0.0001, "ETH": 0.0002})
    assert tf.positions["BTC"].funding_pnl_usd < 0
    assert tf.positions["ETH"].funding_pnl_usd > 0
    assert funding_delta > 0  # ETH received more than BTC paid

    # BTC rises +10%, ETH falls -10% -> both positions profit
    eq_after = tf.equity_usd({"BTC": 121.0, "ETH": 81.0})
    assert eq_after > 118.0


def test_trend_follower_auto_compounds_on_flip() -> None:
    tf = EMACrossTrendFollower(
        symbols=("BTC",),
        starting_equity_usd=100.0,
        fast_hours=2,
        slow_hours=5,
        leverage=2.0,
    )
    tf.seed_history("BTC", [100.0, 100.0, 100.0, 110.0, 120.0])
    tf.rebalance({"BTC": 120.0})
    initial_ntl = tf.positions["BTC"].notional_usd(120.0)
    assert initial_ntl < 200.0  # ~ (100 - 0.15) * 2

    # Price rallies to 180 (+50%), stabilizes, then pulls back to 150 so fast EMA crosses below slow EMA
    for p in [180.0, 180.0, 180.0, 180.0, 180.0, 165.0, 150.0]:
        tf.update_ema("BTC", p)
    actions = tf.rebalance({"BTC": 150.0})
    assert any(a.action == "FLIP_SHORT" for a in actions)
    # New short notional should be compounded above initial notional (locked in ~+$50 profit on 2x)
    assert tf.positions["BTC"].notional_usd(150.0) > initial_ntl * 1.3
