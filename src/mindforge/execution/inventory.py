"""Capital-light InventorySource implementations:
- PaperInventory: full local accounting for paper trading & backtesting
- VaultInventoryHook: queries a Hyperliquid-style community vault's equity (or
  token-loan inventory budget) so external LPs supply capital while MindForge
  executes the quoting & risk logic.
"""

from __future__ import annotations

import logging

import httpx

from mindforge.config import VaultConfig
from mindforge.execution.base import InventorySource
from mindforge.types import FillEvent, Position, Side

logger = logging.getLogger(__name__)


class PaperInventory(InventorySource):
    """In-memory portfolio & position ledger for paper trading and simulation."""

    def __init__(
        self,
        starting_equity_usd: float = 1000.0,
        initial_token_loans: dict[str, float] | None = None,
    ) -> None:
        self.starting_equity_usd = starting_equity_usd
        self.cash_usd = starting_equity_usd
        self._positions: dict[str, Position] = {}
        self.token_loans: dict[str, float] = dict(initial_token_loans or {})

    def get_position(self, symbol: str) -> Position:
        if symbol not in self._positions:
            self._positions[symbol] = Position(symbol=symbol)
        return self._positions[symbol]

    def all_positions(self) -> dict[str, Position]:
        return dict(self._positions)

    def apply_fill(self, fill: FillEvent) -> None:
        pos = self.get_position(fill.symbol)
        qty_delta = fill.size if fill.side == Side.BUY else -fill.size

        # Realize PnL if reducing or flipping an existing position
        if pos.size != 0.0 and (pos.size > 0) != (qty_delta > 0):
            closing_qty = min(abs(pos.size), abs(qty_delta))
            if pos.size > 0:
                realized = closing_qty * (fill.price - pos.avg_entry_price)
            else:
                realized = closing_qty * (pos.avg_entry_price - fill.price)
            pos.realized_pnl_usd += realized

        new_size = pos.size + qty_delta
        if abs(new_size) < 1e-12:
            pos.size = 0.0
            pos.avg_entry_price = 0.0
        elif pos.size == 0.0 or (pos.size > 0) == (qty_delta > 0):
            # Increasing existing position -> weighted average entry price
            total_cost = abs(pos.size) * pos.avg_entry_price + abs(qty_delta) * fill.price
            pos.avg_entry_price = total_cost / abs(new_size)
            pos.size = new_size
        else:
            # Position flipped across zero
            if (new_size > 0) != (pos.size > 0):
                pos.avg_entry_price = fill.price
            pos.size = new_size

        pos.fees_usd += fill.fee_usd + fill.gas_usd
        # Cash impact: buying spends cash, selling receives cash, minus trading fees & gas
        self.cash_usd -= qty_delta * fill.price
        self.cash_usd -= fill.fee_usd + fill.gas_usd

    def apply_funding(self, symbol: str, mark_price: float, hourly_rate: float, dt_seconds: float) -> float:
        """Apply pro-rated perp funding payment on an open position (positive rate = longs pay shorts)."""
        pos = self.get_position(symbol)
        if pos.size == 0.0 or dt_seconds <= 0 or hourly_rate == 0.0:
            return 0.0
        notional = pos.notional_usd(mark_price)
        # Long pays when funding > 0; short receives when funding > 0
        funding_cost = notional * hourly_rate * (dt_seconds / 3600.0)
        self.cash_usd -= funding_cost
        pos.fees_usd += funding_cost
        return funding_cost

    def total_realized_pnl_usd(self) -> float:
        return sum(p.realized_pnl_usd - p.fees_usd for p in self._positions.values())

    def total_unrealized_pnl_usd(self, mark_prices: dict[str, float]) -> float:
        return sum(
            p.unrealized_pnl_usd(mark_prices.get(sym, p.avg_entry_price))
            for sym, p in self._positions.items()
        )

    def total_equity_usd(self, mark_prices: dict[str, float]) -> float:
        mark_val = sum(
            p.notional_usd(mark_prices.get(sym, p.avg_entry_price))
            for sym, p in self._positions.items()
        )
        return self.cash_usd + mark_val

    def gross_exposure_usd(self, mark_prices: dict[str, float]) -> float:
        return sum(
            abs(p.notional_usd(mark_prices.get(sym, p.avg_entry_price)))
            for sym, p in self._positions.items()
        )


class VaultInventoryHook(PaperInventory):
    """Capital-light vault adapter that syncs allocated equity from a Hyperliquid vault.

    In `hyperliquid_vault` mode, `sync()` queries Hyperliquid's public `/info`
    `vaultDetails` endpoint to read live vault TVL and computes the strategy's
    allocated capital budget (`vault_tvl * vault_budget_fraction`).
    If no vault_address is configured or the network is offline, it safely falls
    back to `config.starting_equity_usd`.
    """

    def __init__(self, config: VaultConfig, testnet: bool = False) -> None:
        super().__init__(
            starting_equity_usd=config.starting_equity_usd,
            initial_token_loans=config.token_loan_inventory,
        )
        self.vault_config = config
        self.info_url = (
            "https://api.hyperliquid-testnet.xyz/info"
            if testnet
            else "https://api.hyperliquid.xyz/info"
        )
        self.last_synced_vault_equity_usd: float | None = None

    async def sync(self) -> None:
        if not self.vault_config.vault_address:
            return
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(
                    self.info_url,
                    json={
                        "type": "vaultDetails",
                        "vaultAddress": self.vault_config.vault_address,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                # Hyperliquid vaultDetails returns portfolio / follower equity fields
                raw_equity = float(data.get("portfolio", [[None, {"accountValueHistory": [[0, 0]]}]])[0][1].get("accountValueHistory", [[0, 0]])[-1][1])
                if raw_equity > 0:
                    allocated = raw_equity * self.vault_config.vault_budget_fraction
                    self.last_synced_vault_equity_usd = allocated
                    logger.info(
                        "Synced Hyperliquid vault %s: TVL=$%.2f, allocated=$%.2f",
                        self.vault_config.vault_address,
                        raw_equity,
                        allocated,
                    )
        except Exception as exc:
            logger.warning("VaultInventoryHook sync skipped/failed: %s", exc)

    def allocated_capital_usd(self) -> float:
        if self.last_synced_vault_equity_usd is not None:
            return self.last_synced_vault_equity_usd
        return self.starting_equity_usd
