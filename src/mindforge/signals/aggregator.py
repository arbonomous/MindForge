"""Aggregates raw mindshare and on-chain agent metrics into normalized SignalSnapshots.

Key derived features:
- mindshare_score: rolling z-score of narrative_share + social_velocity + watchlist_delta
- onchain_score: rolling z-score of active_agent_wallets + inference_volume_proxy +
  agent_to_agent_transfers + new_agent_launches
- hype_divergence: max(0, mindshare_score - onchain_score) — detects viral hype that
  is *not* backed by real on-chain agent activity
- attention_instability: combines velocity volatility, sentiment dispersion, and hype
  divergence so the quoting and risk engines can widen spreads or trim size
"""

from __future__ import annotations

from collections import defaultdict, deque

import numpy as np

from mindforge.types import MindshareMetric, OnChainAgentMetric, SignalSnapshot, utc_now


def _zscore(history: deque[float], value: float, clamp: float = 3.0) -> float:
    if len(history) < 4:
        return 0.0
    arr = np.asarray(history, dtype=float)
    mean = float(np.mean(arr))
    std = float(np.std(arr))
    if std < 1e-9:
        return 0.0
    return float(np.clip((value - mean) / std, -clamp, clamp))


class SignalAggregator:
    """Maintains rolling statistics per symbol and emits normalized SignalSnapshots."""

    def __init__(self, rolling_window: int = 30) -> None:
        self.rolling_window = max(5, rolling_window)
        self._ms_share: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=self.rolling_window))
        self._ms_vel: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=self.rolling_window))
        self._ms_watch: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=self.rolling_window))

        self._oc_wallets: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=self.rolling_window))
        self._oc_inf: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=self.rolling_window))
        self._oc_a2a: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=self.rolling_window))
        self._oc_launch: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=self.rolling_window))

        self._latest: dict[str, SignalSnapshot] = {}

    def update(
        self,
        symbol: str,
        mindshare: MindshareMetric | None = None,
        onchain: OnChainAgentMetric | None = None,
    ) -> SignalSnapshot:
        """Ingest the latest raw metrics and return an updated normalized SignalSnapshot."""
        ms_score = 0.0
        vel_cv = 0.0
        dispersion = 0.25

        if mindshare is not None:
            z_share = _zscore(self._ms_share[symbol], mindshare.narrative_share)
            z_vel = _zscore(self._ms_vel[symbol], mindshare.social_velocity)
            z_watch = _zscore(self._ms_watch[symbol], mindshare.watchlist_delta)

            self._ms_share[symbol].append(mindshare.narrative_share)
            self._ms_vel[symbol].append(mindshare.social_velocity)
            self._ms_watch[symbol].append(mindshare.watchlist_delta)

            ms_score = float(np.clip(0.45 * z_share + 0.35 * z_vel + 0.20 * z_watch, -3.0, 3.0))
            dispersion = float(np.clip(mindshare.sentiment_dispersion, 0.0, 1.0))

            vel_hist = self._ms_vel[symbol]
            if len(vel_hist) >= 4:
                arr = np.asarray(vel_hist, dtype=float)
                vel_cv = float(np.std(arr) / max(float(np.mean(arr)), 1.0))

        oc_score = 0.0
        if onchain is not None:
            z_w = _zscore(self._oc_wallets[symbol], float(onchain.active_agent_wallets))
            z_i = _zscore(self._oc_inf[symbol], onchain.inference_volume_proxy)
            z_a = _zscore(self._oc_a2a[symbol], onchain.agent_to_agent_transfers)
            z_l = _zscore(self._oc_launch[symbol], float(onchain.new_agent_launches))

            self._oc_wallets[symbol].append(float(onchain.active_agent_wallets))
            self._oc_inf[symbol].append(onchain.inference_volume_proxy)
            self._oc_a2a[symbol].append(onchain.agent_to_agent_transfers)
            self._oc_launch[symbol].append(float(onchain.new_agent_launches))

            oc_score = float(np.clip(0.30 * z_w + 0.30 * z_i + 0.25 * z_a + 0.15 * z_l, -3.0, 3.0))

        # Positive hype_divergence means social buzz is outrunning actual on-chain agent demand
        hype_divergence = max(0.0, ms_score - oc_score)

        # Attention instability combines velocity erraticness, sentiment dispersion, and hype divergence
        attention_instability = max(
            0.0,
            0.9 * vel_cv + 1.1 * max(0.0, dispersion - 0.25) + 0.55 * hype_divergence,
        )

        # Classify human-readable regime for dashboard & optional LLM prompt context
        if attention_instability >= 1.5 and hype_divergence >= 0.8:
            regime = "hype_divergence"
        elif ms_score >= 0.8 and oc_score >= 0.6:
            regime = "narrative_expansion"
        elif oc_score >= 0.9 and ms_score < 0.5:
            regime = "stealth_onchain_growth"
        elif ms_score <= -0.8 and oc_score <= -0.5:
            regime = "attention_decay"
        else:
            regime = "calm"

        ts = (
            mindshare.timestamp
            if mindshare is not None
            else (onchain.timestamp if onchain is not None else utc_now())
        )
        snapshot = SignalSnapshot(
            symbol=symbol,
            mindshare_score=ms_score,
            onchain_score=oc_score,
            hype_divergence=hype_divergence,
            attention_instability=attention_instability,
            raw_mindshare=mindshare,
            raw_onchain=onchain,
            regime=regime,
            timestamp=ts,
        )
        self._latest[symbol] = snapshot
        return snapshot

    def latest(self, symbol: str) -> SignalSnapshot | None:
        return self._latest.get(symbol)
