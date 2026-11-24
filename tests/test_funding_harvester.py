"""Tests for Delta-Neutral FundingHarvester (price-crash immunity, fees, funding accrual)."""

from mindforge.strategy.funding_harvester import FundingHarvester, FundingPairQuote


def test_delta_neutral_hedge_survives_35pct_crash_and_earns_funding() -> None:
    harvester = FundingHarvester(
        starting_equity_usd=100.0,
        maker_fee_bps=1.5,
        bridge_gas_usd=0.15,
    )
    q0 = FundingPairQuote(
        symbol="HYPE",
        spot_coin="@107",
        spot_mid=60.0,
        perp_mid=60.03,
        hourly_funding_rate=0.0000156,  # ~13.66% APY
    )
    pos = harvester.open_hedge(q0, notional_usd=65.0)
    assert pos is not None

    # Simulate a brutal -35% market crash while spot & perp stay tightly aligned
    q_crash = FundingPairQuote(
        symbol="HYPE",
        spot_coin="@107",
        spot_mid=39.0,
        perp_mid=39.02,
        hourly_funding_rate=0.0000156,
    )
    # Accrue 30 days of hourly funding
    earned = harvester.accrue_funding({"HYPE": q_crash}, dt_seconds=30 * 24 * 3600)
    assert earned > 0.40

    # Equity is STILL GREEN after -35% crash, $0.15 bridge gas, and entry maker fees!
    end_eq = harvester.total_equity_usd({"HYPE": q_crash})
    assert end_eq > 100.25
