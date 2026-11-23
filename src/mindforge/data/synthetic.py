"""Coupled synthetic market, mindshare, and on-chain agent activity environment.

Designed for deterministic offline simulations, unit tests, and demonstrating
how MindForge behaves across realistic AI-agent market regimes:
  - calm: steady baseline attention and mean-reverting order flow
  - narrative_pump: rising mindshare backed by genuine on-chain agent usage
  - hype_divergence_shock: viral social spike *without* confirming on-chain activity
    (high attention instability -> spreads widen, quote size shrinks)
  - agent_launch_wave: strong on-chain wallet cluster & launch surge leading price
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np

from mindforge.config import MarketConfig
from mindforge.data.base import MarketDataFeed, MindshareProvider, OnChainAgentProvider
from mindforge.types import (
    FundingSnapshot,
    MindshareMetric,
    OnChainAgentMetric,
    OrderBookLevel,
    OrderBookSnapshot,
)


@dataclass
class _SyntheticMarketState:
    config: MarketConfig
    price: float
    narrative_share: float
    social_velocity: float
    watchlist_delta: float
    sentiment_dispersion: float
    active_wallets: float
    inference_proxy: float
    agent_transfers: float
    new_launches: float
    funding_rate: float = 0.0001


class SyntheticEnvironment(MarketDataFeed, MindshareProvider, OnChainAgentProvider):
    """Deterministic coupled simulator implementing all three data-layer interfaces."""

    def __init__(
        self,
        markets: list[MarketConfig],
        seed: int = 42,
        step_seconds: float = 5.0,
    ) -> None:
        self.rng = np.random.default_rng(seed)
        self.step_seconds = step_seconds
        self.step_index = 0
        self.current_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        self.current_regime = "calm"

        self._states: dict[str, _SyntheticMarketState] = {}
        self._books: dict[str, OrderBookSnapshot] = {}
        self._funding: dict[str, FundingSnapshot] = {}
        self._mindshare: dict[str, MindshareMetric] = {}
        self._onchain: dict[str, OnChainAgentMetric] = {}

        for m in markets:
            self._states[m.symbol] = _SyntheticMarketState(
                config=m,
                price=m.initial_price,
                narrative_share=0.06,
                social_velocity=25.0,
                watchlist_delta=5.0,
                sentiment_dispersion=0.22,
                active_wallets=420.0,
                inference_proxy=12_000.0,
                agent_transfers=85_000.0,
                new_launches=3.0,
            )
        self.step()  # Populate initial snapshots

    def _regime_for_step(self, step: int) -> str:
        """Cycle through realistic narrative & on-chain regimes."""
        phase = step % 240
        if phase < 70:
            return "calm"
        if phase < 120:
            return "narrative_pump"
        if phase < 165:
            return "hype_divergence_shock"
        if phase < 210:
            return "agent_launch_wave"
        return "calm"

    def step(self) -> None:
        """Advance all synthetic markets and coupled signals by one discrete tick."""
        self.step_index += 1
        self.current_time += timedelta(seconds=self.step_seconds)
        self.current_regime = self._regime_for_step(self.step_index)

        for sym, st in self._states.items():
            # 1. Evolve mindshare & on-chain activity according to active regime
            if self.current_regime == "calm":
                target_share, target_vel, target_disp = 0.06, 25.0, 0.22
                target_wallets, target_inf, target_a2a, target_launch = 420.0, 12_000.0, 85_000.0, 3.0
                vol_scale = 0.0014
                drift = 0.0
            elif self.current_regime == "narrative_pump":
                target_share, target_vel, target_disp = 0.13, 62.0, 0.35
                target_wallets, target_inf, target_a2a, target_launch = 640.0, 22_000.0, 150_000.0, 7.0
                vol_scale = 0.0024
                drift = 0.00055
            elif self.current_regime == "hype_divergence_shock":
                # Viral social buzz with polarized sentiment, while on-chain activity lags/stalls
                target_share, target_vel, target_disp = 0.19, 130.0, 0.82
                target_wallets, target_inf, target_a2a, target_launch = 360.0, 10_500.0, 70_000.0, 2.0
                vol_scale = 0.0045
                drift = -0.00045
            else:  # agent_launch_wave
                target_share, target_vel, target_disp = 0.11, 48.0, 0.28
                target_wallets, target_inf, target_a2a, target_launch = 780.0, 30_000.0, 210_000.0, 12.0
                vol_scale = 0.0021
                drift = 0.00040

            # Smooth AR(1) mean-reversion + noise
            alpha = 0.18
            st.narrative_share = float(
                np.clip(
                    st.narrative_share + alpha * (target_share - st.narrative_share) + self.rng.normal(0, 0.005),
                    0.005,
                    0.50,
                )
            )
            st.social_velocity = float(
                max(
                    1.0,
                    st.social_velocity + alpha * (target_vel - st.social_velocity) + self.rng.normal(0, 4.5),
                )
            )
            st.watchlist_delta = float(
                0.7 * st.watchlist_delta + 0.3 * (target_vel - 25.0) * 0.25 + self.rng.normal(0, 2.5)
            )
            st.sentiment_dispersion = float(
                np.clip(
                    st.sentiment_dispersion + alpha * (target_disp - st.sentiment_dispersion) + self.rng.normal(0, 0.04),
                    0.05,
                    0.98,
                )
            )

            st.active_wallets = float(
                max(20.0, st.active_wallets + alpha * (target_wallets - st.active_wallets) + self.rng.normal(0, 18.0))
            )
            st.inference_proxy = float(
                max(500.0, st.inference_proxy + alpha * (target_inf - st.inference_proxy) + self.rng.normal(0, 800.0))
            )
            st.agent_transfers = float(
                max(1000.0, st.agent_transfers + alpha * (target_a2a - st.agent_transfers) + self.rng.normal(0, 5000.0))
            )
            st.new_launches = float(
                max(0.0, st.new_launches + alpha * (target_launch - st.new_launches) + self.rng.normal(0, 0.8))
            )

            # 2. Evolve market mid-price coupled to signals + stochastic noise
            ret = drift + float(self.rng.normal(0.0, vol_scale))
            st.price = max(st.config.tick_size * 10, st.price * (1.0 + ret))
            st.funding_rate = float(np.clip(0.0001 + drift * 0.5 + self.rng.normal(0, 0.00003), -0.001, 0.002))

            # 3. Construct realistic top-of-book L2 snapshot
            mkt_half_spread = st.price * (st.config.base_half_spread_bps * 0.75 / 10_000.0)
            if self.current_regime == "hype_divergence_shock":
                mkt_half_spread *= 1.8

            tick = st.config.tick_size
            best_bid = max(tick, round((st.price - mkt_half_spread) / tick) * tick)
            best_ask = max(best_bid + tick, round((st.price + mkt_half_spread) / tick) * tick)

            bids = tuple(
                OrderBookLevel(
                    price=round(max(tick, best_bid - i * tick * 2), 6),
                    size=round(float(self.rng.uniform(40, 220)) / max(st.price, 0.05), 2),
                )
                for i in range(3)
            )
            asks = tuple(
                OrderBookLevel(
                    price=round(best_ask + i * tick * 2, 6),
                    size=round(float(self.rng.uniform(40, 220)) / max(st.price, 0.05), 2),
                )
                for i in range(3)
            )

            self._books[sym] = OrderBookSnapshot(
                symbol=sym,
                bids=bids,
                asks=asks,
                timestamp=self.current_time,
                venue="synthetic",
            )
            self._funding[sym] = FundingSnapshot(
                symbol=sym,
                hourly_rate=st.funding_rate,
                open_interest_usd=float(st.agent_transfers * 15.0),
                timestamp=self.current_time,
            )
            self._mindshare[sym] = MindshareMetric(
                symbol=sym,
                narrative_share=st.narrative_share,
                social_velocity=st.social_velocity,
                watchlist_delta=st.watchlist_delta,
                sentiment_dispersion=st.sentiment_dispersion,
                timestamp=self.current_time,
                source="synthetic",
            )
            self._onchain[sym] = OnChainAgentMetric(
                symbol=sym,
                active_agent_wallets=int(round(st.active_wallets)),
                inference_volume_proxy=st.inference_proxy,
                agent_to_agent_transfers=st.agent_transfers,
                new_agent_launches=int(round(st.new_launches)),
                timestamp=self.current_time,
                source="synthetic",
            )

    async def get_order_book(self, symbol: str) -> OrderBookSnapshot | None:
        return self._books.get(symbol)

    async def get_funding(self, symbol: str) -> FundingSnapshot | None:
        return self._funding.get(symbol)

    async def fetch(self, symbol: str) -> MindshareMetric | None:  # type: ignore[override]
        """Default MindshareProvider.fetch implementation."""
        return self._mindshare.get(symbol)

    async def fetch_mindshare(self, symbol: str) -> MindshareMetric | None:
        return self._mindshare.get(symbol)

    async def fetch_onchain(self, symbol: str) -> OnChainAgentMetric | None:
        return self._onchain.get(symbol)


class SyntheticMindshareAdapter(MindshareProvider):
    """Thin adapter exposing SyntheticEnvironment as a standalone MindshareProvider."""

    def __init__(self, env: SyntheticEnvironment) -> None:
        self.env = env

    async def fetch(self, symbol: str) -> MindshareMetric | None:
        return await self.env.fetch_mindshare(symbol)


class SyntheticOnChainAdapter(OnChainAgentProvider):
    """Thin adapter exposing SyntheticEnvironment as a standalone OnChainAgentProvider."""

    def __init__(self, env: SyntheticEnvironment) -> None:
        self.env = env

    async def fetch(self, symbol: str) -> OnChainAgentMetric | None:
        return await self.env.fetch_onchain(symbol)
