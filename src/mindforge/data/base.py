"""Abstract interfaces for market data, mindshare feeds, and on-chain agent metrics."""

from __future__ import annotations

from abc import ABC, abstractmethod

from mindforge.types import (
    FundingSnapshot,
    MindshareMetric,
    OnChainAgentMetric,
    OrderBookSnapshot,
)


class MarketDataFeed(ABC):
    """Abstract market data feed (order book + funding)."""

    async def start(self) -> None:
        """Optional lifecycle hook to open websockets or HTTP clients."""

    async def stop(self) -> None:
        """Optional lifecycle hook to close connections cleanly."""

    @abstractmethod
    async def get_order_book(self, symbol: str) -> OrderBookSnapshot | None:
        """Return the latest order book snapshot for `symbol`."""

    @abstractmethod
    async def get_funding(self, symbol: str) -> FundingSnapshot | None:
        """Return the latest funding snapshot for `symbol` (None if spot/prediction market)."""


class MindshareProvider(ABC):
    """Pluggable interface for external or synthetic mindshare / narrative feeds."""

    @abstractmethod
    async def fetch(self, symbol: str) -> MindshareMetric | None:
        """Fetch the latest mindshare metric for `symbol`."""

    async def close(self) -> None:
        """Optional cleanup hook."""


class OnChainAgentProvider(ABC):
    """Pluggable interface for on-chain AI agent activity signals."""

    @abstractmethod
    async def fetch(self, symbol: str) -> OnChainAgentMetric | None:
        """Fetch the latest on-chain agent activity metric for `symbol`."""

    async def close(self) -> None:
        """Optional cleanup hook."""
