"""Deterministic backtesting & offline simulation runner.

Supports:
1. Synthetic regime simulation (zero external data needed)
2. Historical CSV replay (columns: timestamp, symbol, mid_price, spread_bps,
   narrative_share, social_velocity, watchlist_delta, sentiment_dispersion,
   active_agent_wallets, inference_volume_proxy, agent_to_agent_transfers, new_agent_launches)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from mindforge.config import MindForgeConfig
from mindforge.data.base import MarketDataFeed, MindshareProvider, OnChainAgentProvider
from mindforge.data.synthetic import (
    SyntheticEnvironment,
    SyntheticMindshareAdapter,
    SyntheticOnChainAdapter,
)
from mindforge.engine.runner import MindForgeEngine
from mindforge.execution.inventory import PaperInventory
from mindforge.execution.paper import PaperExecutionVenue
from mindforge.reporting.html_report import generate_html_report
from mindforge.reporting.metrics import PerformanceSummary, TickRecord
from mindforge.signals.forecaster import AttentionForecaster
from mindforge.types import (
    FundingSnapshot,
    MindshareMetric,
    OnChainAgentMetric,
    OrderBookLevel,
    OrderBookSnapshot,
)


@dataclass(frozen=True)
class BacktestResult:
    summary: PerformanceSummary
    history: list[TickRecord]
    report_path: Path | None = None


class _CSVReplayFeed(MarketDataFeed, MindshareProvider, OnChainAgentProvider):
    """Replays historical rows from a pandas DataFrame into MindForge's data interfaces."""

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df.reset_index(drop=True)
        self._books: dict[str, OrderBookSnapshot] = {}
        self._ms: dict[str, MindshareMetric] = {}
        self._oc: dict[str, OnChainAgentMetric] = {}

    def load_row(self, row: pd.Series) -> datetime:
        sym = str(row["symbol"])
        ts = pd.to_datetime(row["timestamp"], utc=True).to_pydatetime()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        mid = float(row["mid_price"])
        spread_bps = float(row.get("spread_bps", 20.0))
        half = mid * (spread_bps / 20_000.0)
        self._books[sym] = OrderBookSnapshot(
            symbol=sym,
            bids=(OrderBookLevel(price=mid - half, size=100.0),),
            asks=(OrderBookLevel(price=mid + half, size=100.0),),
            timestamp=ts,
            venue="csv_replay",
        )
        self._ms[sym] = MindshareMetric(
            symbol=sym,
            narrative_share=float(row.get("narrative_share", 0.06)),
            social_velocity=float(row.get("social_velocity", 25.0)),
            watchlist_delta=float(row.get("watchlist_delta", 0.0)),
            sentiment_dispersion=float(row.get("sentiment_dispersion", 0.25)),
            timestamp=ts,
            source="csv",
        )
        self._oc[sym] = OnChainAgentMetric(
            symbol=sym,
            active_agent_wallets=int(row.get("active_agent_wallets", 400)),
            inference_volume_proxy=float(row.get("inference_volume_proxy", 10_000.0)),
            agent_to_agent_transfers=float(row.get("agent_to_agent_transfers", 80_000.0)),
            new_agent_launches=int(row.get("new_agent_launches", 2)),
            timestamp=ts,
            source="csv",
        )
        return ts

    async def get_order_book(self, symbol: str) -> OrderBookSnapshot | None:
        return self._books.get(symbol)

    async def get_funding(self, symbol: str) -> FundingSnapshot | None:
        return None

    async def fetch(self, symbol: str) -> MindshareMetric | None:  # type: ignore[override]
        return self._ms.get(symbol)


class _CSVOnChainAdapter(OnChainAgentProvider):
    def __init__(self, feed: _CSVReplayFeed) -> None:
        self._feed = feed

    async def fetch(self, symbol: str) -> OnChainAgentMetric | None:
        return self._feed._oc.get(symbol)


class BacktestRunner:
    """Runs fast, deterministic synthetic or CSV-replay backtests."""

    def __init__(
        self,
        config: MindForgeConfig,
        forecaster: AttentionForecaster | None = None,
        seed: int = 42,
    ) -> None:
        self.config = config
        self.forecaster = forecaster
        self.seed = seed

    async def run_synthetic(
        self,
        steps: int = 300,
        report_path: str | Path | None = None,
    ) -> BacktestResult:
        env = SyntheticEnvironment(self.config.markets, seed=self.seed)
        inventory = PaperInventory(
            starting_equity_usd=self.config.vault.starting_equity_usd,
            initial_token_loans=self.config.vault.token_loan_inventory,
        )
        venue = PaperExecutionVenue(
            inventory=inventory,
            maker_fee_bps=self.config.execution.maker_fee_bps,
            fill_probability_base=self.config.execution.fill_probability_base,
            seed=self.seed + 1,
        )
        engine = MindForgeEngine(
            config=self.config,
            market_feed=env,
            execution=venue,
            inventory=inventory,
            mindshare_provider=SyntheticMindshareAdapter(env),
            onchain_provider=SyntheticOnChainAdapter(env),
            forecaster=self.forecaster,
            mode_label="BACKTEST-SYNTH",
        )

        for _ in range(steps):
            env.step()
            await engine.step_once(now=env.current_time)

        summary = engine.tracker.summary()
        out_path: Path | None = None
        if report_path is not None:
            out_path = generate_html_report(
                output_path=report_path,
                agent_name=self.config.agent_name,
                summary=summary,
                history=engine.tracker.history,
            )
        return BacktestResult(summary=summary, history=engine.tracker.history, report_path=out_path)

    async def run_csv(
        self,
        csv_path: str | Path,
        report_path: str | Path | None = None,
    ) -> BacktestResult:
        df = pd.read_csv(csv_path)
        feed = _CSVReplayFeed(df)
        inventory = PaperInventory(starting_equity_usd=self.config.vault.starting_equity_usd)
        venue = PaperExecutionVenue(
            inventory=inventory,
            maker_fee_bps=self.config.execution.maker_fee_bps,
            fill_probability_base=self.config.execution.fill_probability_base,
            seed=self.seed + 1,
        )
        engine = MindForgeEngine(
            config=self.config,
            market_feed=feed,
            execution=venue,
            inventory=inventory,
            mindshare_provider=feed,
            onchain_provider=_CSVOnChainAdapter(feed),
            forecaster=self.forecaster,
            mode_label="BACKTEST-CSV",
        )

        for _, row in df.iterrows():
            ts = feed.load_row(row)
            await engine.step_once(now=ts)

        summary = engine.tracker.summary()
        out_path: Path | None = None
        if report_path is not None:
            out_path = generate_html_report(
                output_path=report_path,
                agent_name=self.config.agent_name,
                summary=summary,
                history=engine.tracker.history,
            )
        return BacktestResult(summary=summary, history=engine.tracker.history, report_path=out_path)
