"""Pluggable attention forecasting interface.

Ships with:
- StatisticalEWMAForecaster: lightweight zero-API-cost default that tracks EWMA
  momentum of combined mindshare + on-chain scores and penalizes hype divergence.
- CallableForecaster: drop-in wrapper so you can plug in a plain Python async
  function, a PydanticAI agent, or a LangGraph workflow without changing core code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Awaitable, Callable

import numpy as np

from mindforge.types import ForecastResult, OrderBookSnapshot, SignalSnapshot

AsyncForecastFn = Callable[[SignalSnapshot, OrderBookSnapshot], Awaitable[ForecastResult]]


class AttentionForecaster(ABC):
    """Abstract interface for short-horizon attention / narrative forecasting."""

    @abstractmethod
    async def forecast(
        self,
        signal: SignalSnapshot,
        book: OrderBookSnapshot,
    ) -> ForecastResult:
        """Return a directional fair-value bias (bps) and confidence score."""


class StatisticalEWMAForecaster(AttentionForecaster):
    """Lightweight statistical forecaster using EWMA attention momentum + divergence damping."""

    def __init__(self, alpha: float = 0.25, max_bias_bps: float = 20.0) -> None:
        self.alpha = alpha
        self.max_bias_bps = max_bias_bps
        self._ewma_composite: dict[str, float] = defaultdict(float)

    async def forecast(
        self,
        signal: SignalSnapshot,
        book: OrderBookSnapshot,
    ) -> ForecastResult:
        sym = signal.symbol
        # Weight fundamental on-chain activity higher than pure social velocity
        composite = 0.45 * signal.mindshare_score + 0.55 * signal.onchain_score
        prev = self._ewma_composite[sym]
        updated = self.alpha * composite + (1.0 - self.alpha) * prev
        self._ewma_composite[sym] = updated

        momentum = composite - prev
        # Dampen / fade directional conviction when unbacked hype divergence is elevated
        raw_bias = (6.0 * updated + 4.0 * momentum) - (4.5 * signal.hype_divergence)
        bias_bps = float(np.clip(raw_bias, -self.max_bias_bps, self.max_bias_bps))

        # Confidence decreases when attention is unstable
        confidence = float(np.clip(1.0 / (1.0 + 0.7 * signal.attention_instability), 0.1, 0.95))
        return ForecastResult(
            bias_bps=bias_bps,
            confidence=confidence,
            regime_note=f"ewma:{signal.regime}",
        )


class CallableForecaster(AttentionForecaster):
    """Adapter that wraps any async function (plain Python, PydanticAI, or LangGraph)."""

    def __init__(self, fn: AsyncForecastFn) -> None:
        self._fn = fn

    async def forecast(
        self,
        signal: SignalSnapshot,
        book: OrderBookSnapshot,
    ) -> ForecastResult:
        return await self._fn(signal, book)
