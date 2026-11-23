"""Self-contained static HTML summary report generator (zero external CDN deps).

Produces a dark-themed single-file HTML dashboard with inline SVG charts for:
- Portfolio Equity Curve
- Gross Inventory Exposure
- Attention Instability & Dynamic Half-Spread response
"""

from __future__ import annotations

from html import escape
from pathlib import Path

from mindforge.reporting.metrics import PerformanceSummary, TickRecord


def _svg_line_chart(
    values: list[float],
    color: str,
    width: int = 760,
    height: int = 190,
    label_prefix: str = "",
    label_suffix: str = "",
) -> str:
    if not values:
        return f'<svg width="{width}" height="{height}"><text x="20" y="95" fill="#94a3b8">No data</text></svg>'

    pad_x, pad_y = 48, 20
    vmin = min(values)
    vmax = max(values)
    span = max(vmax - vmin, 1e-9)

    inner_w = width - 2 * pad_x
    inner_h = height - 2 * pad_y
    n = max(len(values) - 1, 1)

    pts = []
    for i, v in enumerate(values):
        x = pad_x + (i / n) * inner_w
        y = pad_y + inner_h - ((v - vmin) / span) * inner_h
        pts.append(f"{x:.1f},{y:.1f}")
    polyline = " ".join(pts)

    area_pts = f"{pad_x},{pad_y + inner_h} {polyline} {pad_x + inner_w},{pad_y + inner_h}"

    return f"""
    <svg viewBox="0 0 {width} {height}" class="chart-svg">
      <line x1="{pad_x}" y1="{pad_y}" x2="{width - pad_x}" y2="{pad_y}" stroke="#1e293b" stroke-dasharray="4 4"/>
      <line x1="{pad_x}" y1="{pad_y + inner_h / 2:.1f}" x2="{width - pad_x}" y2="{pad_y + inner_h / 2:.1f}" stroke="#1e293b" stroke-dasharray="4 4"/>
      <line x1="{pad_x}" y1="{pad_y + inner_h}" x2="{width - pad_x}" y2="{pad_y + inner_h}" stroke="#334155"/>
      <polygon points="{area_pts}" fill="{color}" fill-opacity="0.12"/>
      <polyline fill="none" stroke="{color}" stroke-width="2.2" points="{polyline}"/>
      <text x="6" y="{pad_y + 4}" fill="#94a3b8" font-size="11">{label_prefix}{vmax:.2f}{label_suffix}</text>
      <text x="6" y="{pad_y + inner_h}" fill="#94a3b8" font-size="11">{label_prefix}{vmin:.2f}{label_suffix}</text>
    </svg>
    """


def generate_html_report(
    output_path: str | Path,
    agent_name: str,
    summary: PerformanceSummary,
    history: list[TickRecord],
) -> Path:
    """Write a self-contained static HTML report and return its Path."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    eq_svg = _svg_line_chart([h.equity_usd for h in history], color="#22d3ee", label_prefix="$")
    exp_svg = _svg_line_chart([h.gross_exposure_usd for h in history], color="#a78bfa", label_prefix="$")
    instab_svg = _svg_line_chart([h.avg_instability for h in history], color="#f59e0b")
    spread_svg = _svg_line_chart([h.avg_half_spread_bps for h in history], color="#34d399", label_suffix=" bps")

    pnl_cls = "pos" if summary.total_pnl_usd >= 0 else "neg"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>MindForge Report — {escape(agent_name)}</title>
<style>
  :root {{
    --bg: #0b1120; --card: #111827; --border: #1f2937;
    --text: #e2e8f0; --muted: #94a3b8; --accent: #22d3ee;
    --pos: #34d399; --neg: #f87171;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 28px; font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
    background: var(--bg); color: var(--text); line-height: 1.5;
  }}
  .container {{ max-width: 1080px; margin: 0 auto; }}
  h1 {{ margin: 0 0 6px; font-size: 26px; color: var(--accent); }}
  .subtitle {{ color: var(--muted); margin-bottom: 24px; font-size: 14px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 24px; }}
  .card {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; padding: 16px;
  }}
  .kpi-label {{ font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }}
  .kpi-value {{ font-size: 22px; font-weight: 700; margin-top: 4px; }}
  .pos {{ color: var(--pos); }}
  .neg {{ color: var(--neg); }}
  .chart-card {{ margin-bottom: 18px; }}
  .chart-title {{ font-size: 15px; font-weight: 600; margin: 0 0 10px; }}
  .chart-svg {{ width: 100%; height: auto; display: block; }}
  .footer {{ margin-top: 24px; color: var(--muted); font-size: 12px; text-align: center; }}
</style>
</head>
<body>
<div class="container">
  <h1>MindForge Performance Report</h1>
  <div class="subtitle">Agent: <strong>{escape(agent_name)}</strong> &middot; Steps: {summary.steps} &middot; Capital-Light Mindshare Market Making</div>

  <div class="grid">
    <div class="card">
      <div class="kpi-label">Ending Equity</div>
      <div class="kpi-value">${summary.ending_equity_usd:,.2f}</div>
    </div>
    <div class="card">
      <div class="kpi-label">Total PnL</div>
      <div class="kpi-value {pnl_cls}">${summary.total_pnl_usd:+,.2f} ({summary.total_return_pct:+.2f}%)</div>
    </div>
    <div class="card">
      <div class="kpi-label">Max Drawdown</div>
      <div class="kpi-value">{summary.max_drawdown_pct:.2f}%</div>
    </div>
    <div class="card">
      <div class="kpi-label">Fill Rate</div>
      <div class="kpi-value">{summary.fill_rate_pct:.1f}% ({summary.total_fills}/{summary.total_quotes})</div>
    </div>
    <div class="card">
      <div class="kpi-label">Traded Volume</div>
      <div class="kpi-value">${summary.total_volume_usd:,.0f}</div>
    </div>
    <div class="card">
      <div class="kpi-label">Avg Half-Spread</div>
      <div class="kpi-value">{summary.avg_half_spread_bps:.1f} bps</div>
    </div>
  </div>

  <div class="card chart-card">
    <div class="chart-title">Portfolio Equity Curve (USD)</div>
    {eq_svg}
  </div>

  <div class="card chart-card">
    <div class="chart-title">Gross Inventory Exposure (USD)</div>
    {exp_svg}
  </div>

  <div class="card chart-card">
    <div class="chart-title">Attention Instability (Mindshare Velocity &amp; Hype Divergence)</div>
    {instab_svg}
  </div>

  <div class="card chart-card">
    <div class="chart-title">Dynamic Half-Spread Response (bps)</div>
    {spread_svg}
  </div>

  <div class="footer">Generated by MindForge &middot; Open-Source Capital-Light AI Market-Making Engine</div>
</div>
</body>
</html>
"""
    out.write_text(html, encoding="utf-8")
    return out
