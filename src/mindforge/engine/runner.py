"""Async MindForgeEngine orchestrating data -> signals -> fair value -> risk -> quoting -> execution."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from rich.live import Live

from mindforge.config import MindForgeConfig
from mindforge.data.base import MarketDataFeed, MindshareProvider, OnChainAgentProvider
from mindforge.data.synthetic import SyntheticEnvironment
from mindforge.execution.base import ExecutionVenue, InventorySource
from mindforge.execution.inventory import PaperInventory
from mindforge.models.fair_value import FairValueEngine
from mindforge.quoting.engine import QuotingEngine
from mindforge.reporting.dashboard import MarketDisplayRow, RichDashboard
from mindforge.reporting.html_report import generate_html_report
from mindforge.reporting.metrics import PerformanceSummary, PerformanceTracker
from mindforge.risk.engine import RiskEngine
from mindforge.signals.aggregator import SignalAggregator
from mindforge.signals.forecaster import AttentionForecaster, StatisticalEWMAForecaster
from mindforge.types import utc_now

logger = logging.getLogger(__name__)


class MindForgeEngine:
    """Core async market-making loop tying all pluggable components together."""

    def __init__(
        self,
        config: MindForgeConfig,
        market_feed: MarketDataFeed,
        execution: ExecutionVenue,
        inventory: InventorySource,
        mindshare_provider: MindshareProvider | None = None,
        onchain_provider: OnChainAgentProvider | None = None,
        forecaster: AttentionForecaster | None = None,
        mode_label: str = "PAPER",
    ) -> None:
        self.config = config
        self.market_feed = market_feed
        self.execution = execution
        self.inventory = inventory
        self.mindshare_provider = mindshare_provider
        self.onchain_provider = onchain_provider
        self.forecaster = forecaster or StatisticalEWMAForecaster()

        self.aggregator = SignalAggregator(rolling_window=config.signals.rolling_window)
        self.fair_value = FairValueEngine(config.signals)
        self.quoting = QuotingEngine(config.quoting)
        self.risk = RiskEngine(config.risk, starting_equity_usd=config.vault.starting_equity_usd)
        self.tracker = PerformanceTracker(starting_equity_usd=config.vault.starting_equity_usd)
        self.dashboard = RichDashboard(agent_name=config.agent_name, mode_label=mode_label)
        self._step = 0
        self._last_cycle_time: datetime | None = None
        self.total_funding_paid_usd = 0.0
        self.bridge_gas_paid_usd = 0.0

        # Account for one-time deposit/bridge gas upfront (e.g. Arbitrum -> Hyperliquid USDC)
        if config.execution.bridge_gas_usd > 0 and isinstance(self.inventory, PaperInventory):
            self.inventory.cash_usd -= config.execution.bridge_gas_usd
            self.tracker.total_fees_usd += config.execution.bridge_gas_usd
            self.bridge_gas_paid_usd = config.execution.bridge_gas_usd

    async def step_once(self, now: datetime | None = None) -> list[MarketDisplayRow]:
        """Run one complete quoting + risk + execution cycle across all configured markets."""
        self._step += 1
        mark_prices: dict[str, float] = {}
        books = {}

        # 1. Fetch latest order books & check resting fills against real live trades
        for m in self.config.markets:
            book = await self.market_feed.get_order_book(m.symbol)
            if book is not None and book.microprice > 0:
                books[m.symbol] = book
                mark_prices[m.symbol] = book.microprice
                self.risk.update_markouts(m.symbol, book.mid_price)

                # If feed streams live trades (e.g. HyperliquidDataFeed), check resting quotes first
                recent_trades = (
                    self.market_feed.drain_recent_trades(m.symbol)
                    if hasattr(self.market_feed, "drain_recent_trades")
                    else []
                )
                if hasattr(self.execution, "check_resting_fills"):
                    resting_fills = self.execution.check_resting_fills(m.symbol, book, recent_trades)
                    if resting_fills:
                        self.tracker.record_fills(resting_fills)
                        for f in resting_fills:
                            self.risk.record_fill(f)

        if not books:
            return []

        cycle_time = now or next(iter(books.values())).timestamp or utc_now()
        dt_sec = (
            max(0.0, (cycle_time - self._last_cycle_time).total_seconds())
            if self._last_cycle_time is not None
            else 0.0
        )
        self._last_cycle_time = cycle_time

        # Apply pro-rated perp funding on any held positions
        if dt_sec > 0 and isinstance(self.inventory, PaperInventory):
            for sym, book in books.items():
                funding = await self.market_feed.get_funding(sym)
                if funding is not None:
                    f_cost = self.inventory.apply_funding(
                        sym,
                        book.mid_price,
                        funding.hourly_rate,
                        dt_sec,
                    )
                    self.total_funding_paid_usd += f_cost
                    self.tracker.total_fees_usd += f_cost

        total_equity = self.inventory.total_equity_usd(mark_prices)
        gross_exposure = self.inventory.gross_exposure_usd(mark_prices)

        rows: list[MarketDisplayRow] = []
        instabilities: list[float] = []
        spreads: list[float] = []
        regimes: list[str] = []

        # 2. Per-market signal -> forecast -> fair value -> risk -> quote -> execute
        for m in self.config.markets:
            book = books.get(m.symbol)
            if book is None:
                continue

            funding = await self.market_feed.get_funding(m.symbol)
            raw_ms = (
                await self.mindshare_provider.fetch(m.symbol)
                if self.mindshare_provider is not None
                else None
            )
            raw_oc = (
                await self.onchain_provider.fetch(m.symbol)
                if self.onchain_provider is not None
                else None
            )

            signal = self.aggregator.update(m.symbol, mindshare=raw_ms, onchain=raw_oc)
            forecast = await self.forecaster.forecast(signal, book)
            fv = self.fair_value.estimate(book, signal=signal, funding=funding, forecast=forecast)
            position = self.inventory.get_position(m.symbol)

            decision = self.risk.evaluate(
                market=m,
                book=book,
                position=position,
                signal=signal,
                total_equity_usd=total_equity,
                gross_exposure_usd=gross_exposure,
                now=cycle_time,
            )

            plan = self.quoting.build_quotes(
                m,
                fv,
                position,
                decision,
                best_bid=book.best_bid,
                best_ask=book.best_ask,
            )

            if not decision.can_quote:
                await self.execution.cancel_all(m.symbol)
            else:
                self.tracker.record_quotes(len(plan.all_orders))
                fills = await self.execution.place_quotes(plan, book)
                if fills:
                    self.tracker.record_fills(fills)
                    for f in fills:
                        self.risk.record_fill(f)

            rows.append(
                MarketDisplayRow(
                    symbol=m.symbol,
                    mid_price=book.mid_price,
                    fv=fv,
                    signal=signal,
                    position=self.inventory.get_position(m.symbol),
                    plan=plan,
                    risk=decision,
                )
            )
            instabilities.append(signal.attention_instability)
            spreads.append(plan.half_spread_bps)
            regimes.append(signal.regime)

        # 3. Record portfolio metrics after fills
        new_equity = self.inventory.total_equity_usd(mark_prices)
        new_gross = self.inventory.gross_exposure_usd(mark_prices)
        realized = (
            self.inventory.total_realized_pnl_usd()
            if isinstance(self.inventory, PaperInventory)
            else 0.0
        )
        unrealized = (
            self.inventory.total_unrealized_pnl_usd(mark_prices)
            if isinstance(self.inventory, PaperInventory)
            else 0.0
        )

        dominant_regime = regimes[0] if regimes else "calm"
        self.tracker.record_tick(
            step=self._step,
            timestamp=cycle_time,
            equity_usd=new_equity,
            realized_pnl_usd=realized,
            unrealized_pnl_usd=unrealized,
            gross_exposure_usd=new_gross,
            avg_instability=sum(instabilities) / max(len(instabilities), 1),
            avg_half_spread_bps=sum(spreads) / max(len(spreads), 1),
            regime=dominant_regime,
        )
        return rows

    async def run(
        self,
        max_steps: int | None = None,
        live_dashboard: bool = True,
        sleep_between_steps: bool = True,
        report_path: str | Path | None = None,
    ) -> PerformanceSummary:
        """Start feeds and run the quoting loop for `max_steps` (or until Ctrl+C)."""
        await self.market_feed.start()
        await self.inventory.sync()

        try:
            if live_dashboard:
                with Live(console=self.dashboard.console, refresh_per_second=6) as live:
                    await self._loop(max_steps, sleep_between_steps, live)
            else:
                await self._loop(max_steps, sleep_between_steps, live=None)
        except KeyboardInterrupt:
            logger.info("MindForge stopped by user.")
        finally:
            await self.execution.cancel_all()
            await self.market_feed.stop()
            await self.execution.close()
            if self.mindshare_provider is not None:
                await self.mindshare_provider.close()
            if self.onchain_provider is not None:
                await self.onchain_provider.close()

        summary = self.tracker.summary()
        saved_report: str | None = None
        if report_path is not None:
            saved_report = str(
                generate_html_report(
                    output_path=report_path,
                    agent_name=self.config.agent_name,
                    summary=summary,
                    history=self.tracker.history,
                )
            )
        self.dashboard.print_final_summary(summary, report_path=saved_report)
        return summary

    async def _loop(
        self,
        max_steps: int | None,
        sleep_between_steps: bool,
        live: Live | None,
    ) -> None:
        step = 0
        while max_steps is None or step < max_steps:
            step += 1
            if isinstance(self.market_feed, SyntheticEnvironment):
                self.market_feed.step()
                now = self.market_feed.current_time
                regime = self.market_feed.current_regime
            else:
                now = utc_now()
                regime = "live"

            rows = await self.step_once(now=now)
            if live is not None and rows:
                live.update(
                    self.dashboard.render(
                        step=step,
                        summary=self.tracker.summary(),
                        rows=rows,
                        regime=regime,
                    )
                )

            if sleep_between_steps:
                await asyncio.sleep(self.config.execution.tick_interval_sec)
