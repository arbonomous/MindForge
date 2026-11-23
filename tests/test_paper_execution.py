"""Tests for PaperExecutionVenue (maker/taker fees, gas, realistic live fills) and PaperInventory."""

import pytest

from mindforge.execution.inventory import PaperInventory
from mindforge.execution.paper import PaperExecutionVenue
from mindforge.types import (
    OrderBookLevel,
    OrderBookSnapshot,
    QuoteOrder,
    QuotePlan,
    Side,
    TradeTick,
)


@pytest.mark.asyncio
async def test_crossing_quotes_charge_taker_fee_and_update_pnl() -> None:
    inv = PaperInventory(starting_equity_usd=1000.0)
    venue = PaperExecutionVenue(
        inventory=inv,
        maker_fee_bps=1.5,
        taker_fee_bps=4.5,
        gas_fee_per_tx_usd=0.01,
        seed=1,
    )

    book = OrderBookSnapshot(
        symbol="VIRTUAL",
        bids=(OrderBookLevel(price=1.99, size=100.0),),
        asks=(OrderBookLevel(price=2.01, size=100.0),),
    )

    # Buy 10 units by crossing best ask (2.01) -> taker fill
    buy_plan = QuotePlan(
        symbol="VIRTUAL",
        reservation_price=2.01,
        half_spread_bps=5.0,
        inventory_ratio=0.0,
        bids=(QuoteOrder(symbol="VIRTUAL", side=Side.BUY, price=2.01, size=10.0),),
        asks=(),
    )
    fills_buy = await venue.place_quotes(buy_plan, book)
    assert len(fills_buy) == 1
    assert not fills_buy[0].is_maker
    assert fills_buy[0].gas_usd == 0.01
    assert inv.get_position("VIRTUAL").size == 10.0

    # Sell 10 units at 2.05 (crossing a higher bid) -> realize +$0.40 minus taker fees & gas
    higher_book = OrderBookSnapshot(
        symbol="VIRTUAL",
        bids=(OrderBookLevel(price=2.05, size=100.0),),
        asks=(OrderBookLevel(price=2.07, size=100.0),),
    )
    sell_plan = QuotePlan(
        symbol="VIRTUAL",
        reservation_price=2.05,
        half_spread_bps=5.0,
        inventory_ratio=0.5,
        bids=(),
        asks=(QuoteOrder(symbol="VIRTUAL", side=Side.SELL, price=2.05, size=10.0),),
    )
    fills_sell = await venue.place_quotes(sell_plan, higher_book)
    assert len(fills_sell) == 1
    assert not fills_sell[0].is_maker
    assert inv.get_position("VIRTUAL").size == 0.0
    # Gross profit = $0.40; taker fees (~$0.01827) + gas ($0.02) -> net ~ $1000.36
    eq = inv.total_equity_usd({"VIRTUAL": 2.06})
    assert 1000.35 < eq < 1000.40


@pytest.mark.asyncio
async def test_realistic_live_fills_only_fill_when_real_trade_hits_quote() -> None:
    inv = PaperInventory(starting_equity_usd=100.0)
    venue = PaperExecutionVenue(
        inventory=inv,
        maker_fee_bps=1.5,
        taker_fee_bps=4.5,
        realistic_live_fills=True,
    )
    book = OrderBookSnapshot(
        symbol="KAITO",
        bids=(OrderBookLevel(price=0.1650, size=500.0),),
        asks=(OrderBookLevel(price=0.1660, size=500.0),),
    )
    plan = QuotePlan(
        symbol="KAITO",
        reservation_price=0.1655,
        half_spread_bps=30.0,
        inventory_ratio=0.0,
        bids=(QuoteOrder(symbol="KAITO", side=Side.BUY, price=0.1648, size=70.0),),
        asks=(QuoteOrder(symbol="KAITO", side=Side.SELL, price=0.1662, size=70.0),),
    )

    # Passive quotes rest without filling immediately
    immediate = await venue.place_quotes(plan, book)
    assert len(immediate) == 0

    # A trade at 0.1655 does not touch our 0.1648 bid or 0.1662 ask
    no_fills = venue.check_resting_fills(
        "KAITO",
        book,
        [TradeTick(symbol="KAITO", price=0.1655, size=50.0, side=Side.SELL)],
    )
    assert len(no_fills) == 0

    # A real sell trade at 0.1647 crosses our resting 0.1648 bid -> passive MAKER fill
    fills = venue.check_resting_fills(
        "KAITO",
        book,
        [TradeTick(symbol="KAITO", price=0.1647, size=50.0, side=Side.SELL)],
    )
    assert len(fills) == 1
    assert fills[0].is_maker
    assert fills[0].price == 0.1648
    assert inv.get_position("KAITO").size == 70.0
