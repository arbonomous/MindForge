"""Command-line interface for MindForge (`mindforge simulate`, `run`, `backtest`)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import typer

from mindforge.config import MindForgeConfig
from mindforge.data.hyperliquid_feed import HyperliquidDataFeed
from mindforge.data.mindshare.kaito_style import KaitoStyleMindshareProvider
from mindforge.data.onchain.agent_activity import OnChainAgentActivityProvider
from mindforge.data.synthetic import (
    SyntheticEnvironment,
    SyntheticMindshareAdapter,
    SyntheticOnChainAdapter,
)
from mindforge.engine.backtester import BacktestRunner
from mindforge.engine.runner import MindForgeEngine
from mindforge.execution.hyperliquid import HyperliquidExecutionVenue
from mindforge.execution.inventory import PaperInventory, VaultInventoryHook
from mindforge.execution.paper import PaperExecutionVenue
from mindforge.reporting.dashboard import RichDashboard

app = typer.Typer(
    name="mindforge",
    help="MindForge: Open-source, capital-light AI market-making agent driven by mindshare & on-chain agent signals.",
    no_args_is_help=True,
)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


@app.command()
def simulate(
    config: Path = typer.Option(
        Path("configs/paper_ai_agents.yaml"),
        "--config",
        "-c",
        help="Path to MindForge YAML configuration file.",
    ),
    steps: int = typer.Option(300, "--steps", "-n", help="Number of simulation steps."),
    seed: int = typer.Option(42, "--seed", help="Random seed for deterministic runs."),
    live: bool = typer.Option(
        True,
        "--live/--no-live",
        help="Show the live Rich terminal dashboard while simulating.",
    ),
    fast: bool = typer.Option(
        True,
        "--fast/--realtime",
        help="Run as fast as possible (no sleep between ticks) vs tick_interval_sec.",
    ),
    report: Path | None = typer.Option(
        None,
        "--report",
        "-r",
        help="Optional path to write a self-contained HTML performance report.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run an offline paper-trading simulation with coupled mindshare & on-chain regimes."""
    _setup_logging(verbose)
    cfg = MindForgeConfig.from_yaml(config)
    env = SyntheticEnvironment(cfg.markets, seed=seed)
    inventory = PaperInventory(
        starting_equity_usd=cfg.vault.starting_equity_usd,
        initial_token_loans=cfg.vault.token_loan_inventory,
    )
    venue = PaperExecutionVenue(
        inventory=inventory,
        maker_fee_bps=cfg.execution.maker_fee_bps,
        fill_probability_base=cfg.execution.fill_probability_base,
        seed=seed + 1,
    )
    engine = MindForgeEngine(
        config=cfg,
        market_feed=env,
        execution=venue,
        inventory=inventory,
        mindshare_provider=SyntheticMindshareAdapter(env),
        onchain_provider=SyntheticOnChainAdapter(env),
        mode_label="PAPER-SIM",
    )
    asyncio.run(
        engine.run(
            max_steps=steps,
            live_dashboard=live,
            sleep_between_steps=not fast,
            report_path=report,
        )
    )


@app.command()
def run(
    config: Path = typer.Option(
        Path("configs/paper_ai_agents.yaml"),
        "--config",
        "-c",
        help="Path to MindForge YAML configuration file.",
    ),
    feed: str = typer.Option(
        "synthetic",
        "--feed",
        "-f",
        help="Market data source: 'synthetic' or 'hyperliquid' (public L2 WS/REST).",
    ),
    steps: int | None = typer.Option(
        None,
        "--steps",
        "-n",
        help="Optional step limit (omit to run continuously until Ctrl+C).",
    ),
    live: bool = typer.Option(True, "--live/--no-live"),
    report: Path | None = typer.Option(None, "--report", "-r"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run MindForge in continuous paper-trading (or dry-run Hyperliquid) mode."""
    _setup_logging(verbose)
    cfg = MindForgeConfig.from_yaml(config)

    if cfg.vault.mode == "hyperliquid_vault":
        inventory = VaultInventoryHook(cfg.vault, testnet=cfg.execution.hyperliquid_testnet)
    else:
        inventory = PaperInventory(
            starting_equity_usd=cfg.vault.starting_equity_usd,
            initial_token_loans=cfg.vault.token_loan_inventory,
        )

    if feed == "hyperliquid":
        symbols = [m.symbol for m in cfg.markets]
        market_feed = HyperliquidDataFeed(symbols=symbols, testnet=cfg.execution.hyperliquid_testnet)
        ms_provider = KaitoStyleMindshareProvider()
        oc_provider = OnChainAgentActivityProvider()
        venue = HyperliquidExecutionVenue(
            inventory=inventory,
            dry_run=cfg.execution.dry_run,
            testnet=cfg.execution.hyperliquid_testnet,
            vault_address=cfg.vault.vault_address,
            maker_fee_bps=cfg.execution.maker_fee_bps,
        )
        mode_label = "HL-DRY-RUN" if cfg.execution.dry_run else "HL-LIVE"
    else:
        env = SyntheticEnvironment(cfg.markets)
        market_feed = env
        ms_provider = SyntheticMindshareAdapter(env)
        oc_provider = SyntheticOnChainAdapter(env)
        venue = PaperExecutionVenue(
            inventory=inventory,
            maker_fee_bps=cfg.execution.maker_fee_bps,
            fill_probability_base=cfg.execution.fill_probability_base,
        )
        mode_label = "PAPER-SYNTH"

    engine = MindForgeEngine(
        config=cfg,
        market_feed=market_feed,
        execution=venue,
        inventory=inventory,
        mindshare_provider=ms_provider,
        onchain_provider=oc_provider,
        mode_label=mode_label,
    )
    asyncio.run(
        engine.run(
            max_steps=steps,
            live_dashboard=live,
            sleep_between_steps=True,
            report_path=report,
        )
    )


@app.command()
def backtest(
    config: Path = typer.Option(
        Path("configs/paper_ai_agents.yaml"),
        "--config",
        "-c",
        help="Path to MindForge YAML configuration file.",
    ),
    data: Path | None = typer.Option(
        None,
        "--data",
        "-d",
        help="Optional historical CSV file to replay (uses synthetic regimes if omitted).",
    ),
    steps: int = typer.Option(400, "--steps", "-n", help="Steps when running synthetic backtest."),
    seed: int = typer.Option(42, "--seed"),
    report: Path = typer.Option(
        Path("out/backtest_report.html"),
        "--report",
        "-r",
        help="Output path for the static HTML summary report.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run a fast, deterministic backtest and generate an HTML performance report."""
    _setup_logging(verbose)
    cfg = MindForgeConfig.from_yaml(config)
    runner = BacktestRunner(cfg, seed=seed)

    if data is not None:
        result = asyncio.run(runner.run_csv(data, report_path=report))
    else:
        result = asyncio.run(runner.run_synthetic(steps=steps, report_path=report))

    dash = RichDashboard(agent_name=cfg.agent_name, mode_label="BACKTEST")
    dash.print_final_summary(
        result.summary,
        report_path=str(result.report_path) if result.report_path else None,
    )


if __name__ == "__main__":
    app()
