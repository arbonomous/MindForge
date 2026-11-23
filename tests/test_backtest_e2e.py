"""End-to-end deterministic simulation & HTML report generation tests."""

from pathlib import Path

import pytest

from mindforge.config import MindForgeConfig
from mindforge.engine.backtester import BacktestRunner


@pytest.mark.asyncio
async def test_e2e_synthetic_backtest_and_html_report(tmp_path: Path) -> None:
    cfg_path = Path(__file__).resolve().parent.parent / "configs" / "paper_ai_agents.yaml"
    cfg = MindForgeConfig.from_yaml(cfg_path)

    report_file = tmp_path / "e2e_report.html"
    runner = BacktestRunner(cfg, seed=42)
    result = await runner.run_synthetic(steps=120, report_path=report_file)

    assert result.summary.steps == 120
    assert result.summary.total_quotes > 0
    assert result.summary.total_fills > 0
    assert result.summary.ending_equity_usd > 0
    assert result.report_path == report_file
    assert report_file.exists()

    html = report_file.read_text(encoding="utf-8")
    assert "MindForge Performance Report" in html
    assert "<svg" in html


@pytest.mark.asyncio
async def test_csv_replay_backtest(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    cfg = MindForgeConfig.from_yaml(root / "configs" / "paper_ai_agents.yaml")
    csv_path = root / "examples" / "sample_history.csv"
    report_file = tmp_path / "csv_report.html"

    runner = BacktestRunner(cfg, seed=7)
    result = await runner.run_csv(csv_path, report_path=report_file)

    assert result.summary.steps == 16
    assert result.summary.total_quotes > 0
    assert report_file.exists()


def test_polymarket_clob_book_parser() -> None:
    from mindforge.data.polymarket_feed import PolymarketDataFeed

    payload = {
        "bids": [{"price": "0.41", "size": "120"}, {"price": "0.43", "size": "80"}],
        "asks": [{"price": "0.47", "size": "90"}, {"price": "0.45", "size": "50"}],
    }
    book = PolymarketDataFeed.parse_clob_book("AI_AGENT_OUTCOME_YES", payload)
    assert book is not None
    assert book.best_bid == 0.43
    assert book.best_ask == 0.45
    assert abs(book.mid_price - 0.44) < 1e-9
    assert book.venue == "polymarket_clob"
