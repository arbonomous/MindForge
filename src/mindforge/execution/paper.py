"""Realistic paper-trading ExecutionVenue simulator.

Simulates resting maker quotes against incoming order-book updates:
- Quotes that cross or touch the opposite side of the book fill immediately.
- Passive quotes near the touch fill stochastically with probability decaying
  exponentially with distance from mid-price (modeling queue priority).
- Supports maker rebates (negative maker_fee_bps) or fees.
"""

from __future__ import annotations

import math

import numpy as np

from mindforge.execution.base import ExecutionVenue, InventorySource
from mindforge.types import FillEvent, OrderBookSnapshot, QuoteOrder, QuotePlan, Side


class PaperExecutionVenue(ExecutionVenue):
    """Deterministic/seeded paper execution simulator."""

    def __init__(
        self,
        inventory: InventorySource,
        maker_fee_bps: float = -0.5,
        fill_probability_base: float = 0.35,
        seed: int = 123,
    ) -> None:
        self.inventory = inventory
        self.maker_fee_bps = maker_fee_bps
        self.fill_probability_base = fill_probability_base
        self.rng = np.random.default_rng(seed)
        self._open_orders: dict[str, list[QuoteOrder]] = {}
        self._fill_seq = 0
        self.total_quotes_placed = 0
        self.total_fills = 0

    async def place_quotes(
        self,
        plan: QuotePlan,
        book: OrderBookSnapshot,
    ) -> list[FillEvent]:
        """Replace resting quotes for `plan.symbol` and simulate fills against `book`."""
        orders = plan.all_orders
        self._open_orders[plan.symbol] = list(orders)
        self.total_quotes_placed += len(orders)

        fills: list[FillEvent] = []
        remaining: list[QuoteOrder] = []
        mid = book.mid_price

        for order in orders:
            if mid <= 0:
                remaining.append(order)
                continue

            filled = False
            if order.side == Side.BUY:
                # Immediate fill if our bid touches or crosses best ask
                if order.price >= book.best_ask:
                    filled = True
                else:
                    dist_bps = max(0.0, (mid - order.price) / mid * 10_000.0)
                    # Tighter quotes near mid get filled much more often than deep quotes
                    prob = self.fill_probability_base * math.exp(-dist_bps / 28.0)
                    filled = bool(self.rng.random() < prob)
            else:
                # Immediate fill if our ask touches or crosses best bid
                if order.price <= book.best_bid:
                    filled = True
                else:
                    dist_bps = max(0.0, (order.price - mid) / mid * 10_000.0)
                    prob = self.fill_probability_base * math.exp(-dist_bps / 28.0)
                    filled = bool(self.rng.random() < prob)

            if filled:
                self._fill_seq += 1
                self.total_fills += 1
                fee_usd = order.notional_usd * (self.maker_fee_bps / 10_000.0)
                fill = FillEvent(
                    symbol=order.symbol,
                    side=order.side,
                    price=order.price,
                    size=order.size,
                    fee_usd=fee_usd,
                    order_id=f"paper-{self._fill_seq}",
                    mid_at_fill=mid,
                    timestamp=book.timestamp,
                )
                self.inventory.apply_fill(fill)
                fills.append(fill)
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
