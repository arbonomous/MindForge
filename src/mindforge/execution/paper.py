"""Realistic paper-trading ExecutionVenue simulator.

Supports two modes:
1. `realistic_live_fills=True` (for live market data, e.g. Hyperliquid WS):
   - Checks previously resting quotes against real incoming `TradeTick` prints
     and live `OrderBookSnapshot` moves (`check_resting_fills`).
   - A resting BUY fills ONLY if real trades print at/below its price or best_ask <= bid_px.
   - A resting SELL fills ONLY if real trades print at/above its price or best_bid >= ask_px.
   - Charges `maker_fee_bps` (+1.5 bps Hyperliquid Tier-0) on passive fills,
     `taker_fee_bps` (+4.5 bps) if a quote crosses the spread, plus `gas_fee_per_tx_usd`.
2. `realistic_live_fills=False` (for synthetic offline backtests):
   - Crossing quotes fill immediately (taker fee); passive quotes near mid fill
     stochastically with exponential distance decay (maker fee).
"""

from __future__ import annotations

import math

import numpy as np

from mindforge.execution.base import ExecutionVenue, InventorySource
from mindforge.types import FillEvent, OrderBookSnapshot, QuoteOrder, QuotePlan, Side, TradeTick


class PaperExecutionVenue(ExecutionVenue):
    """Deterministic/seeded paper execution simulator with full fee & gas accounting."""

    def __init__(
        self,
        inventory: InventorySource,
        maker_fee_bps: float = 1.5,
        taker_fee_bps: float = 4.5,
        gas_fee_per_tx_usd: float = 0.0,
        realistic_live_fills: bool = False,
        fill_probability_base: float = 0.35,
        seed: int = 123,
    ) -> None:
        self.inventory = inventory
        self.maker_fee_bps = maker_fee_bps
        self.taker_fee_bps = taker_fee_bps
        self.gas_fee_per_tx_usd = gas_fee_per_tx_usd
        self.realistic_live_fills = realistic_live_fills
        self.fill_probability_base = fill_probability_base
        self.rng = np.random.default_rng(seed)
        self._open_orders: dict[str, list[QuoteOrder]] = {}
        self._fill_seq = 0
        self.total_quotes_placed = 0
        self.total_fills = 0
        self.maker_fills = 0
        self.taker_fills = 0
        self.total_trading_fees_usd = 0.0
        self.total_gas_fees_usd = 0.0

    def _record_fill(
        self,
        order: QuoteOrder,
        fill_price: float,
        book: OrderBookSnapshot,
        is_maker: bool,
    ) -> FillEvent:
        self._fill_seq += 1
        self.total_fills += 1
        if is_maker:
            self.maker_fills += 1
            fee_bps = self.maker_fee_bps
        else:
            self.taker_fills += 1
            fee_bps = self.taker_fee_bps

        notional = fill_price * order.size
        fee_usd = notional * (fee_bps / 10_000.0)
        gas_usd = self.gas_fee_per_tx_usd
        self.total_trading_fees_usd += fee_usd
        self.total_gas_fees_usd += gas_usd

        fill = FillEvent(
            symbol=order.symbol,
            side=order.side,
            price=fill_price,
            size=order.size,
            fee_usd=fee_usd,
            gas_usd=gas_usd,
            is_maker=is_maker,
            order_id=f"paper-{self._fill_seq}",
            mid_at_fill=book.mid_price,
            timestamp=book.timestamp,
        )
        self.inventory.apply_fill(fill)
        return fill

    def check_resting_fills(
        self,
        symbol: str,
        book: OrderBookSnapshot,
        recent_trades: list[TradeTick] | None = None,
    ) -> list[FillEvent]:
        """Check if previously resting quotes were filled by real live trades or book moves."""
        resting = self._open_orders.get(symbol, [])
        if not resting or book.mid_price <= 0:
            return []

        trades = recent_trades or []
        min_trade_px = min((t.price for t in trades), default=float("inf"))
        max_trade_px = max((t.price for t in trades), default=0.0)

        fills: list[FillEvent] = []
        still_open: list[QuoteOrder] = []

        for order in resting:
            filled = False
            if order.side == Side.BUY:
                # Filled passively as maker if real trades printed <= bid OR market ask moved <= bid
                if min_trade_px <= order.price or book.best_ask <= order.price:
                    filled = True
            else:
                # Filled passively as maker if real trades printed >= ask OR market bid moved >= ask
                if max_trade_px >= order.price or book.best_bid >= order.price:
                    filled = True

            if filled:
                fills.append(self._record_fill(order, order.price, book, is_maker=True))
            else:
                still_open.append(order)

        self._open_orders[symbol] = still_open
        return fills

    async def place_quotes(
        self,
        plan: QuotePlan,
        book: OrderBookSnapshot,
    ) -> list[FillEvent]:
        """Replace resting quotes for `plan.symbol` and simulate fills against `book`."""
        orders = plan.all_orders
        self.total_quotes_placed += len(orders)

        fills: list[FillEvent] = []
        remaining: list[QuoteOrder] = []
        mid = book.mid_price

        for order in orders:
            if mid <= 0:
                remaining.append(order)
                continue

            # 1. Check immediate crossing (taker fill)
            if order.side == Side.BUY and order.price >= book.best_ask:
                fills.append(self._record_fill(order, book.best_ask, book, is_maker=False))
                continue
            if order.side == Side.SELL and order.price <= book.best_bid:
                fills.append(self._record_fill(order, book.best_bid, book, is_maker=False))
                continue

            # 2. In strict live mode, passive quotes rest on the book until real trades/moves hit them
            if self.realistic_live_fills:
                remaining.append(order)
                continue

            # 3. Synthetic mode: stochastic queue-decay maker fill
            dist_bps = (
                max(0.0, (mid - order.price) / mid * 10_000.0)
                if order.side == Side.BUY
                else max(0.0, (order.price - mid) / mid * 10_000.0)
            )
            prob = self.fill_probability_base * math.exp(-dist_bps / 28.0)
            if bool(self.rng.random() < prob):
                fills.append(self._record_fill(order, order.price, book, is_maker=True))
            else:
                remaining.append(order)

        self._open_orders[plan.symbol] = remaining
        return fills

    async def cancel_all(self, symbol: str | None = None) -> int:
        if symbol is None:
            count = sum(len(v) for v in self._open_orders.values())
            self._open_orders.clear()
            return count
        count = len(self._open_orders.get(symbol, []))
        self._open_orders[symbol] = []
        return count

    def get_open_orders(self, symbol: str | None = None) -> list[QuoteOrder]:
        if symbol is not None:
            return list(self._open_orders.get(symbol, []))
        return [o for orders in self._open_orders.values() for o in orders]
