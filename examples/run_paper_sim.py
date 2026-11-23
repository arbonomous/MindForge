"""Quick standalone paper-trading simulation example.

Runs a 300-step deterministic simulation across AI agent markets (VIRTUAL,
AI16Z, AIXBT) cycling through calm, narrative_pump, hype_divergence_shock,
and agent_launch_wave regimes, then writes an HTML report.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from mindforge.config import MindForgeConfig
from mindforge.engine.backtester import BacktestRunner
from mindforge.reporting.dashboard import RichDashboard


async def main() -> None:
    root = Path(__file__).resolve().parent.parent
    cfg = MindForgeConfig.from_yaml(root / "configs" / "paper_ai_agents.yaml")

    runner = BacktestRunner(cfg, seed=42)
    report_path = root / "out" / "paper_sim_report.html"
    result = await runner.run_synthetic(steps=300, report_path=report_path)

    dash = RichDashboard(agent_name=cfg.agent_name, mode_label="EXAMPLE-SIM")
    dash.print_final_summary(result.summary, report_path=str(result.report_path))


if __name__ == "__main__":
    asyncio.run(main())
