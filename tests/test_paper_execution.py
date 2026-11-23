"""Tests for PaperExecutionVenue and PaperInventory accounting."""

import pytest

from mindforge.execution.inventory import PaperInventory
from mindforge.execution.paper import PaperExecutionVenue
from mindforge.types import OrderBookLevel, OrderBookSnapshot, QuoteOrder, QuotePlan, Side


@pytest.mark.asyncio
async def test_crossing_quotes_fill_and_update_inventory_pnl() -> None:
    inv = PaperInventory(starting_equity_usd=1000.0)
    venue = PaperExecutionVenue(inventory=inv, maker_fee_bps=-1.0, seed=1)

    book = OrderBookSnapshot(
        symbol="VIRTUAL",
        bids=(OrderBookLevel(price=1.99, size=100.0),),
        asks=(OrderBookLevel(price=2.01, size=100.0),),
    )

    # Buy 10 units by crossing best ask (2.01)
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
    assert inv.get_position("VIRTUAL").size == 10.0

    # Sell 10 units at 2.05 (crossing a higher bid) -> realize +$0.40 profit + maker rebates
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
    assert inv.get_position("VIRTUAL").size == 0.0
    assert inv.total_equity_usd({"VIRTUAL": 2.06}) > 1000.40
