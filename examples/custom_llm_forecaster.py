"""Example: Plugging a custom async attention forecaster into MindForge.

Shows how easily you can replace the default StatisticalEWMAForecaster with:
- A plain Python heuristic or ML model
- A PydanticAI agent
- A LangGraph workflow

No changes to FairValueEngine, QuotingEngine, or RiskEngine are required.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from mindforge.config import MindForgeConfig
from mindforge.engine.backtester import BacktestRunner
from mindforge.signals.forecaster import CallableForecaster
from mindforge.types import ForecastResult, OrderBookSnapshot, SignalSnapshot


async def custom_attention_forecaster(
    signal: SignalSnapshot,
    book: OrderBookSnapshot,
) -> ForecastResult:
    """Example pluggable forecaster (drop in PydanticAI / LangGraph calls here).

    Logic demonstrated:
    - If on-chain agent activity is surging while social buzz hasn't caught up yet
      ("stealth_onchain_growth"), lean bullish (+12 bps).
    - If social hype is spiking without confirming on-chain usage ("hype_divergence"),
      fade the hype (-10 bps) with lower confidence so spreads widen.
    """
    if signal.regime == "stealth_onchain_growth":
        return ForecastResult(
            bias_bps=12.0,
            confidence=0.80,
            regime_note="custom:stealth_onchain_bullish",
        )
    if signal.regime == "hype_divergence":
        return ForecastResult(
            bias_bps=-10.0,
            confidence=0.45,
            regime_note="custom:fade_unbacked_hype",
        )
    # Mildly follow combined signal otherwise
    bias = 5.0 * (0.4 * signal.mindshare_score + 0.6 * signal.onchain_score)
    return ForecastResult(
        bias_bps=max(-15.0, min(15.0, bias)),
        confidence=0.70,
        regime_note=f"custom:{signal.regime}",
    )


async def main() -> None:
    root = Path(__file__).resolve().parent.parent
    cfg = MindForgeConfig.from_yaml(root / "configs" / "paper_ai_agents.yaml")

    forecaster = CallableForecaster(custom_attention_forecaster)
    runner = BacktestRunner(cfg, forecaster=forecaster, seed=7)
    result = await runner.run_synthetic(steps=200)

    print(
        f"Custom forecaster run complete: "
        f"PnL=${result.summary.total_pnl_usd:+.2f} "
        f"({result.summary.total_return_pct:+.2f}%), "
        f"Fills={result.summary.total_fills}, "
        f"MaxDD={result.summary.max_drawdown_pct:.2f}%"
    )


if __name__ == "__main__":
    asyncio.run(main())
