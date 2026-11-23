"""Rich live terminal dashboard for MindForge."""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from mindforge.reporting.metrics import PerformanceSummary
from mindforge.types import FairValueEstimate, Position, QuotePlan, RiskDecision, SignalSnapshot


@dataclass
class MarketDisplayRow:
    symbol: str
    mid_price: float
    fv: FairValueEstimate
    signal: SignalSnapshot | None
    position: Position
    plan: QuotePlan
    risk: RiskDecision


class RichDashboard:
    """Renders a live terminal view of quotes, mindshare signals, inventory, and PnL."""

    def __init__(self, agent_name: str, mode_label: str) -> None:
        self.agent_name = agent_name
        self.mode_label = mode_label
        self.console = Console()

    def render(
        self,
        step: int,
        summary: PerformanceSummary,
        rows: list[MarketDisplayRow],
        regime: str = "calm",
    ) -> Group:
        pnl_color = "green" if summary.total_pnl_usd >= 0 else "red"
        header = Text.assemble(
            ("MindForge ", "bold cyan"),
            (f"[{self.agent_name}] ", "bold white"),
            (f"Mode: {self.mode_label}  |  ", "dim"),
            (f"Step: {step}  |  ", "bold"),
            (f"Regime: {regime}", "yellow"),
        )

        kpi_table = Table.grid(expand=True)
        for _ in range(5):
            kpi_table.add_column(justify="center")
        kpi_table.add_row(
            f"[dim]Equity[/dim]\n[bold]${summary.ending_equity_usd:,.2f}[/bold]",
            f"[dim]Total PnL[/dim]\n[bold {pnl_color}]${summary.total_pnl_usd:+,.2f} ({summary.total_return_pct:+.2f}%)[/bold {pnl_color}]",
            f"[dim]Max Drawdown[/dim]\n[bold magenta]{summary.max_drawdown_pct:.2f}%[/bold magenta]",
            f"[dim]Fills / Fill Rate[/dim]\n[bold]{summary.total_fills} ({summary.fill_rate_pct:.1f}%)[/bold]",
            f"[dim]Volume / Avg ½-Spread[/dim]\n[bold]${summary.total_volume_usd:,.0f} / {summary.avg_half_spread_bps:.1f} bps[/bold]",
        )

        mkt_table = Table(expand=True, show_header=True, header_style="bold cyan")
        mkt_table.add_column("Market", style="bold")
        mkt_table.add_column("Mid / FV", justify="right")
        mkt_table.add_column("Best Bid / Ask", justify="right")
        mkt_table.add_column("½-Spread", justify="right")
        mkt_table.add_column("Inv ($)", justify="right")
        mkt_table.add_column("MS / OC Z", justify="right")
        mkt_table.add_column("Instab", justify="right")
        mkt_table.add_column("Risk", justify="center")

        for r in rows:
            best_b = f"{r.plan.bids[0].price:.4f}" if r.plan.bids else "-"
            best_a = f"{r.plan.asks[0].price:.4f}" if r.plan.asks else "-"
            inv_usd = r.position.notional_usd(r.mid_price)
            inv_style = "green" if inv_usd > 1 else ("red" if inv_usd < -1 else "dim")
            ms_z = r.signal.mindshare_score if r.signal else 0.0
            oc_z = r.signal.onchain_score if r.signal else 0.0
            instab = r.signal.attention_instability if r.signal else 0.0
            instab_style = "red" if instab >= 1.4 else ("yellow" if instab >= 0.8 else "green")
            risk_style = {
                "allow": "green",
                "widen": "yellow",
                "unwind_only": "magenta",
                "halt": "bold red",
            }.get(r.risk.action.value, "white")

            mkt_table.add_row(
                r.symbol,
                f"{r.mid_price:.4f} / {r.fv.fair_value:.4f}",
                f"{best_b} / {best_a}",
                f"{r.plan.half_spread_bps:.1f} bps",
                f"[{inv_style}]${inv_usd:+.1f}[/{inv_style}]",
                f"{ms_z:+.2f} / {oc_z:+.2f}",
                f"[{instab_style}]{instab:.2f}[/{instab_style}]",
                f"[{risk_style}]{r.risk.action.value.upper()}[/{risk_style}]",
            )

        return Group(
            Panel(header, border_style="cyan"),
            Panel(kpi_table, title="Portfolio Metrics", border_style="blue"),
            Panel(mkt_table, title="Active Quotes & Attention Signals", border_style="green"),
        )

    def print_final_summary(self, summary: PerformanceSummary, report_path: str | None = None) -> None:
        pnl_color = "green" if summary.total_pnl_usd >= 0 else "red"
        table = Table(title=f"MindForge Summary — {self.agent_name}", show_header=False, border_style="cyan")
        table.add_column("Metric", style="bold")
        table.add_column("Value", justify="right")
        table.add_row("Starting Equity", f"${summary.starting_equity_usd:,.2f}")
        table.add_row("Ending Equity", f"${summary.ending_equity_usd:,.2f}")
        table.add_row(
            "Total PnL",
            f"[{pnl_color}]${summary.total_pnl_usd:+,.2f} ({summary.total_return_pct:+.2f}%)[/{pnl_color}]",
        )
        table.add_row("Realized / Unrealized", f"${summary.realized_pnl_usd:+,.2f} / ${summary.unrealized_pnl_usd:+,.2f}")
        table.add_row("Net Fees (neg = rebate)", f"${summary.total_fees_usd:+,.3f}")
        table.add_row("Max Drawdown", f"{summary.max_drawdown_pct:.2f}%")
        table.add_row("Sharpe (per-run)", f"{summary.sharpe_ratio:.2f}")
        table.add_row("Quotes / Fills (Fill Rate)", f"{summary.total_quotes} / {summary.total_fills} ({summary.fill_rate_pct:.1f}%)")
        table.add_row("Total Traded Volume", f"${summary.total_volume_usd:,.2f}")
        table.add_row("Avg Half-Spread", f"{summary.avg_half_spread_bps:.1f} bps")
        if report_path:
            table.add_row("HTML Report", report_path)
        self.console.print(table)
