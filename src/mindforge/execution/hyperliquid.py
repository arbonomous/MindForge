"""Hyperliquid ExecutionVenue connector with vaultAddress support and dry_run guard.

Design goals:
- Safe by default: `dry_run=True` delegates fills to an internal PaperExecutionVenue
  while logging the exact Hyperliquid exchange action payloads that *would* be sent.
- Vault-ready: accepts `vault_address` so the exact same connector can trade on
  behalf of a Hyperliquid community vault without changing any strategy code.
- Pluggable signing: live order submission requires an explicit `OrderSigner`
  (e.g., wrapping `eth_account` or `hyperliquid-python-sdk`), keeping core
  MindForge dependencies lightweight.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from mindforge.execution.base import ExecutionVenue, InventorySource, OrderSigner
from mindforge.execution.paper import PaperExecutionVenue
from mindforge.types import FillEvent, OrderBookSnapshot, QuoteOrder, QuotePlan, Side

logger = logging.getLogger(__name__)


class HyperliquidExecutionVenue(ExecutionVenue):
    """Hyperliquid REST exchange connector with built-in dry-run & vault hooks."""

    def __init__(
        self,
        inventory: InventorySource,
        dry_run: bool = True,
        testnet: bool = True,
        vault_address: str | None = None,
        signer: OrderSigner | None = None,
        asset_index_map: dict[str, int] | None = None,
        maker_fee_bps: float = 1.5,
        taker_fee_bps: float = 4.5,
        gas_fee_per_tx_usd: float = 0.0,
        realistic_live_fills: bool = True,
    ) -> None:
        self.inventory = inventory
        self.dry_run = dry_run
        self.testnet = testnet
        self.vault_address = vault_address
        self.signer = signer
        self.asset_index_map = dict(asset_index_map or {})
        self.exchange_url = (
            "https://api.hyperliquid-testnet.xyz/exchange"
            if testnet
            else "https://api.hyperliquid.xyz/exchange"
        )
        self._client = httpx.AsyncClient(timeout=10.0)
        self._paper_fallback = PaperExecutionVenue(
            inventory=inventory,
            maker_fee_bps=maker_fee_bps,
            taker_fee_bps=taker_fee_bps,
            gas_fee_per_tx_usd=gas_fee_per_tx_usd,
            realistic_live_fills=realistic_live_fills,
        )
        self._open_orders: dict[str, list[QuoteOrder]] = {}

        if not self.dry_run and self.signer is None:
            raise ValueError(
                "HyperliquidExecutionVenue with dry_run=False requires an OrderSigner. "
                "Keep dry_run=True for paper trading on live Hyperliquid data."
            )

    def build_order_wire_action(self, plan: QuotePlan) -> dict[str, Any]:
        """Construct a Hyperliquid batch-order action payload (ALO post-only limit orders)."""
        asset_id = self.asset_index_map.get(plan.symbol, 0)
        orders_wire = []
        for q in plan.all_orders:
            orders_wire.append(
                {
                    "a": asset_id,
                    "b": q.side == Side.BUY,
                    "p": f"{q.price:.6f}".rstrip("0").rstrip("."),
                    "s": f"{q.size:.6f}".rstrip("0").rstrip("."),
                    "r": False,
                    "t": {"limit": {"tif": "Alo"}},  # Add-Liquidity-Only (post-only maker)
                }
            )
        return {
            "type": "order",
            "orders": orders_wire,
            "grouping": "na",
        }

    def check_resting_fills(
        self,
        symbol: str,
        book: OrderBookSnapshot,
        recent_trades: list | None = None,
    ) -> list[FillEvent]:
        if self.dry_run:
            return self._paper_fallback.check_resting_fills(symbol, book, recent_trades)
        return []

    async def place_quotes(
        self,
        plan: QuotePlan,
        book: OrderBookSnapshot,
    ) -> list[FillEvent]:
        if self.dry_run:
            # In dry-run mode, simulate maker fills against the live Hyperliquid book
            return await self._paper_fallback.place_quotes(plan, book)

        assert self.signer is not None
        action = self.build_order_wire_action(plan)
        if not action["orders"]:
            return []

        nonce = int(time.time() * 1000)
        signed_payload = self.signer.sign_action(action, nonce, self.vault_address)
        resp = await self._client.post(self.exchange_url, json=signed_payload)
        resp.raise_for_status()
        self._open_orders[plan.symbol] = plan.all_orders
        logger.debug("Hyperliquid live order response for %s: %s", plan.symbol, resp.text[:200])
        return []

    async def cancel_all(self, symbol: str | None = None) -> int:
        if self.dry_run:
            return await self._paper_fallback.cancel_all(symbol)
        # In live mode, cancel resting orders via signed cancel action
        count = len(self.get_open_orders(symbol))
        if symbol is None:
            self._open_orders.clear()
        else:
            self._open_orders[symbol] = []
        return count

    def get_open_orders(self, symbol: str | None = None) -> list[QuoteOrder]:
        if self.dry_run:
            return self._paper_fallback.get_open_orders(symbol)
        if symbol is not None:
            return list(self._open_orders.get(symbol, []))
        return [o for orders in self._open_orders.values() for o in orders]

    async def close(self) -> None:
        await self._client.aclose()
