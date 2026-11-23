"""Fair-value estimation engine combining order-book microprice, funding,
mindshare signals, on-chain agent activity, and optional attention forecasts.
"""

from __future__ import annotations

from collections import defaultdict, deque

import numpy as np

from mindforge.config import SignalWeightsConfig
from mindforge.types import (
    FairValueEstimate,
    ForecastResult,
    FundingSnapshot,
    OrderBookSnapshot,
    SignalSnapshot,
)


class FairValueEngine:
    """Computes signal-adjusted fair value and rolling realized volatility per symbol."""

    def __init__(self, config: SignalWeightsConfig) -> None:
        self.config = config
        self._price_history: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=max(5, config.rolling_window))
        )

    def estimate(
        self,
        book: OrderBookSnapshot,
        signal: SignalSnapshot | None = None,
        funding: FundingSnapshot | None = None,
        forecast: ForecastResult | None = None,
    ) -> FairValueEstimate:
        """Produce a FairValueEstimate for the given market snapshot."""
        sym = book.symbol
        micro = book.microprice
        if micro <= 0:
            raise ValueError(f"Invalid order book microprice ({micro}) for {sym}")

        # 1. Update price history and compute realized return volatility in bps
        hist = self._price_history[sym]
        hist.append(micro)
        realized_vol_bps = self._realized_vol_bps(hist)

        # 2. Signal-driven alpha (mindshare + on-chain agent activity)
        ms_bps = 0.0
        oc_bps = 0.0
        instability = 0.0
        if signal is not None:
            ms_bps = self.config.mindshare_weight_bps * signal.mindshare_score
            oc_bps = self.config.onchain_weight_bps * signal.onchain_score
            instability = signal.attention_instability

        # 3. Optional AttentionForecaster contribution (weighted by confidence)
        forecast_bps = 0.0
        if forecast is not None:
            forecast_bps = (
                self.config.forecast_weight
                * forecast.bias_bps
                * float(np.clip(forecast.confidence, 0.0, 1.0))
            )

        # 4. Optional funding carry tilt (positive funding -> slight upward carry bias)
        funding_bps = 0.0
        if funding is not None:
            # hourly_rate * 10_000 converts to bps/hr
            funding_bps = self.config.funding_weight * (funding.hourly_rate * 10_000.0)

        # 5. Clamp total alpha so noisy signals can never blow up fair value
        raw_alpha_bps = ms_bps + oc_bps + forecast_bps + funding_bps
        alpha_bps = float(
            np.clip(
                raw_alpha_bps,
                -self.config.max_fv_shift_bps,
                self.config.max_fv_shift_bps,
            )
        )
        fair_value = micro * (1.0 + alpha_bps / 10_000.0)

        return FairValueEstimate(
            symbol=sym,
            microprice=micro,
            fair_value=fair_value,
            alpha_bps=alpha_bps,
            realized_vol_bps=realized_vol_bps,
            attention_instability=instability,
            funding_tilt_bps=funding_bps,
            forecast_bias_bps=forecast_bps,
            timestamp=book.timestamp,
        )

    @staticmethod
    def _realized_vol_bps(prices: deque[float]) -> float:
        if len(prices) < 3:
            return 8.0  # Conservative default before warmup
        arr = np.asarray(prices, dtype=float)
        rets = np.diff(np.log(np.maximum(arr, 1e-12)))
        return float(np.std(rets) * 10_000.0)
