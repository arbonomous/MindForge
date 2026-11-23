"""Polymarket CLOB public orderbook feed for prediction / outcome markets.

Demonstrates how easily MindForge extends beyond perpetuals (Hyperliquid)
to long-tail prediction and narrative/outcome contracts:
- Uses Polymarket's public CLOB REST endpoint:
  GET https://clob.polymarket.com/book?token_id=<outcome_token_id>
- Maps human-readable market symbols (e.g. "AI_AGENT_10B_MCAP_YES") to
  Polymarket CLOB `token_id`s.
"""

from __future__ import annotations

import logging

import httpx

from mindforge.data.base import MarketDataFeed
from mindforge.types import FundingSnapshot, OrderBookLevel, OrderBookSnapshot, utc_now

logger = logging.getLogger(__name__)


class PolymarketDataFeed(MarketDataFeed):
    """Fetches public L2 order books from Polymarket's CLOB API."""

    def __init__(
        self,
        symbol_to_token_id: dict[str, str],
        base_url: str = "https://clob.polymarket.com",
        timeout_sec: float = 8.0,
    ) -> None:
        self.symbol_to_token_id = dict(symbol_to_token_id)
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout_sec)

    async def stop(self) -> None:
        await self._client.aclose()

    @staticmethod
    def parse_clob_book(symbol: str, payload: dict) -> OrderBookSnapshot | None:
        """Parse a Polymarket `/book` JSON payload into an OrderBookSnapshot."""
        raw_bids = payload.get("bids", [])
        raw_asks = payload.get("asks", [])
        # Polymarket returns bids ascending or descending depending on endpoint; sort explicitly
        bids = tuple(
            sorted(
                (OrderBookLevel(price=float(b["price"]), size=float(b["size"])) for b in raw_bids),
                key=lambda lv: lv.price,
                reverse=True,
            )[:5]
        )
        asks = tuple(
            sorted(
                (OrderBookLevel(price=float(a["price"]), size=float(a["size"])) for a in raw_asks),
                key=lambda lv: lv.price,
            )[:5]
        )
        if not bids or not asks:
            return None
        return OrderBookSnapshot(
            symbol=symbol,
            bids=bids,
            asks=asks,
            timestamp=utc_now(),
            venue="polymarket_clob",
        )

    async def get_order_book(self, symbol: str) -> OrderBookSnapshot | None:
        token_id = self.symbol_to_token_id.get(symbol)
        if not token_id:
            return None
        try:
            resp = await self._client.get(
                f"{self.base_url}/book",
                params={"token_id": token_id},
            )
            resp.raise_for_status()
            return self.parse_clob_book(symbol, resp.json())
        except Exception as exc:
            logger.warning("Polymarket CLOB fetch failed for %s: %s", symbol, exc)
            return None

    async def get_funding(self, symbol: str) -> FundingSnapshot | None:
        # Prediction/outcome markets have no perp funding rate
        return None
