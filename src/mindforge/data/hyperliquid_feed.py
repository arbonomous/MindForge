"""Hyperliquid public market data feed (REST + WebSocket L2 order book & funding).

Uses Hyperliquid's public endpoints — no API keys or private keys required:
- Mainnet REST: https://api.hyperliquid.xyz/info
- Mainnet WS:   wss://api.hyperliquid.xyz/ws
- Testnet REST: https://api.hyperliquid-testnet.xyz/info
- Testnet WS:   wss://api.hyperliquid-testnet.xyz/ws
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

import httpx
import websockets

from mindforge.data.base import MarketDataFeed
from mindforge.types import FundingSnapshot, OrderBookLevel, OrderBookSnapshot, utc_now

logger = logging.getLogger(__name__)


class HyperliquidDataFeed(MarketDataFeed):
    """Streams live L2 order books via WebSocket and polls funding via REST."""

    def __init__(self, symbols: list[str], testnet: bool = False) -> None:
        self.symbols = symbols
        self.testnet = testnet
        self.rest_url = (
            "https://api.hyperliquid-testnet.xyz/info"
            if testnet
            else "https://api.hyperliquid.xyz/info"
        )
        self.ws_url = (
            "wss://api.hyperliquid-testnet.xyz/ws"
            if testnet
            else "wss://api.hyperliquid.xyz/ws"
        )

        self._books: dict[str, OrderBookSnapshot] = {}
        self._funding: dict[str, FundingSnapshot] = {}
        self._client = httpx.AsyncClient(timeout=10.0)
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        # Prime order books and funding immediately via REST so the first tick has data
        await asyncio.gather(
            self._poll_funding_once(),
            *(self._fetch_book_rest(sym) for sym in self.symbols),
            return_exceptions=True,
        )
        self._tasks.append(asyncio.create_task(self._ws_loop(), name="hl-ws-loop"))
        self._tasks.append(asyncio.create_task(self._funding_loop(), name="hl-funding-loop"))

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self._client.aclose()

    async def get_order_book(self, symbol: str) -> OrderBookSnapshot | None:
        if symbol not in self._books:
            await self._fetch_book_rest(symbol)
        return self._books.get(symbol)

    async def get_funding(self, symbol: str) -> FundingSnapshot | None:
        return self._funding.get(symbol)

    async def _fetch_book_rest(self, symbol: str) -> None:
        try:
            resp = await self._client.post(
                self.rest_url,
                json={"type": "l2Book", "coin": symbol},
            )
            resp.raise_for_status()
            data = resp.json()
            self._ingest_l2_payload(symbol, data)
        except Exception as exc:
            logger.warning("Hyperliquid REST l2Book failed for %s: %s", symbol, exc)

    async def _poll_funding_once(self) -> None:
        try:
            resp = await self._client.post(
                self.rest_url,
                json={"type": "metaAndAssetCtxs"},
            )
            resp.raise_for_status()
            payload = resp.json()
            if not isinstance(payload, list) or len(payload) < 2:
                return
            universe = payload[0].get("universe", [])
            ctxs = payload[1]
            now = utc_now()
            for meta, ctx in zip(universe, ctxs, strict=False):
                coin = meta.get("name")
                if coin in self.symbols:
                    funding_rate = float(ctx.get("funding", 0.0))
                    oi = float(ctx.get("openInterest", 0.0)) * float(ctx.get("markPx", 0.0) or 0.0)
                    self._funding[coin] = FundingSnapshot(
                        symbol=coin,
                        hourly_rate=funding_rate,
                        open_interest_usd=oi,
                        timestamp=now,
                    )
        except Exception as exc:
            logger.warning("Hyperliquid funding poll failed: %s", exc)

    async def _funding_loop(self) -> None:
        while self._running:
            await asyncio.sleep(30.0)
            await self._poll_funding_once()

    async def _ws_loop(self) -> None:
        backoff = 1.0
        while self._running:
            try:
                async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20) as ws:
                    backoff = 1.0
                    for sym in self.symbols:
                        sub_msg = {
                            "method": "subscribe",
                            "subscription": {"type": "l2Book", "coin": sym},
                        }
                        await ws.send(json.dumps(sub_msg))

                    async for raw in ws:
                        if not self._running:
                            break
                        msg = json.loads(raw)
                        if msg.get("channel") == "l2Book":
                            data = msg.get("data", {})
                            coin = data.get("coin")
                            if coin:
                                self._ingest_l2_payload(coin, data)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Hyperliquid WS disconnected (%s); reconnecting in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)

    def _ingest_l2_payload(self, symbol: str, data: dict) -> None:
        levels = data.get("levels", [[], []])
        if len(levels) < 2:
            return
        bids = tuple(
            OrderBookLevel(price=float(lv["px"]), size=float(lv["sz"]))
            for lv in levels[0][:5]
        )
        asks = tuple(
            OrderBookLevel(price=float(lv["px"]), size=float(lv["sz"]))
            for lv in levels[1][:5]
        )
        if not bids or not asks:
            return
        ts_ms = data.get("time")
        ts = (
            datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC)
            if ts_ms
            else utc_now()
        )
        self._books[symbol] = OrderBookSnapshot(
            symbol=symbol,
            bids=bids,
            asks=asks,
            timestamp=ts,
            venue="hyperliquid_testnet" if self.testnet else "hyperliquid",
        )
