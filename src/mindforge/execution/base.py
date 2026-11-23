"""Abstract interfaces for ExecutionVenue, InventorySource, and live OrderSigner.

Separating `InventorySource` from the strategy core is what makes MindForge
capital-light: the exact same FairValue, Quoting, and Risk engines can run on:
  - PaperInventory ($1,000 simulated equity)
  - Tiny personal wallet capital
  - A Hyperliquid-style community vault (where external LPs supply capital)
  - A token-loan inventory pool (where a project lends tokens for market making)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from mindforge.types import FillEvent, OrderBookSnapshot, Position, QuoteOrder, QuotePlan


class InventorySource(ABC):
    """Provides capital equity, positions, and gross exposure to the strategy."""

    @abstractmethod
    def get_position(self, symbol: str) -> Position:
        """Return current Position for `symbol`."""

    @abstractmethod
    def apply_fill(self, fill: FillEvent) -> None:
        """Update cash/positions/PnL from an executed fill."""

    @abstractmethod
    def total_equity_usd(self, mark_prices: dict[str, float]) -> float:
        """Return total mark-to-market equity available to the strategy."""

    @abstractmethod
    def gross_exposure_usd(self, mark_prices: dict[str, float]) -> float:
        """Return sum of absolute USD notionals across all open positions."""

    async def sync(self) -> None:
        """Optional async refresh hook (e.g., querying a Hyperliquid vault's live equity)."""


class ExecutionVenue(ABC):
    """Abstract venue interface for placing/canceling quotes and receiving fills."""

    @abstractmethod
    async def place_quotes(
        self,
        plan: QuotePlan,
        book: OrderBookSnapshot,
    ) -> list[FillEvent]:
        """Replace active quotes for `plan.symbol` and return any immediate/simulated fills."""

    @abstractmethod
    async def cancel_all(self, symbol: str | None = None) -> int:
        """Cancel open quotes for `symbol` (or all symbols if None). Returns canceled count."""

    @abstractmethod
    def get_open_orders(self, symbol: str | None = None) -> list[QuoteOrder]:
        """Return currently resting orders."""

    async def close(self) -> None:
        """Optional cleanup hook."""


class OrderSigner(ABC):
    """Optional signing interface for live exchange order placement."""

    @abstractmethod
    def sign_action(self, action: dict[str, Any], nonce: int, vault_address: str | None) -> dict[str, Any]:
        """Sign a venue payload and return the wire-ready request dict."""
