"""Mobile-friendly live dashboard for MindForge ($100 Hyperliquid Mainnet paper trading).

Runs the real MindForgeEngine streaming live Hyperliquid L2 order books, live trades,
and live funding rates (VIRTUAL, KAITO, AIXBT) with real Tier-0 fees & gas accounting,
while serving an iPhone-optimized auto-refreshing dashboard on port 8080.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from mindforge.config import MindForgeConfig
from mindforge.data.hyperliquid_feed import HyperliquidDataFeed
from mindforge.engine.runner import MindForgeEngine
from mindforge.execution.hyperliquid import HyperliquidExecutionVenue
from mindforge.execution.inventory import PaperInventory

STATE_LOCK = threading.Lock()
LIVE_STATE: dict[str, Any] = {
    "status": "starting",
    "uptime_sec": 0,
    "step": 0,
    "starting_equity": 100.0,
    "equity": 100.0,
    "net_pnl": 0.0,
    "trading_pnl_ex_bridge": 0.0,
    "realized_pnl": 0.0,
    "unrealized_pnl": 0.0,
    "bridge_gas": 0.15,
    "trading_fees": 0.0,
    "l1_gas": 0.0,
    "funding_paid": 0.0,
    "max_drawdown_pct": 0.0,
    "total_fills": 0,
    "maker_fills": 0,
    "taker_fills": 0,
    "volume_usd": 0.0,
    "markets": [],
    "recent_fills": [],
    "equity_curve": [100.0],
}

MOBILE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover" />
<title>MindForge Live ($100 Paper)</title>
<style>
  :root {
    --bg: #090d16; --card: #111827; --border: #1f2937;
    --fg: #f1f5f9; --muted: #94a3b8; --accent: #22d3ee;
    --green: #34d399; --red: #f87171; --amber: #fbbf24;
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  body {
    margin: 0; padding: 14px 12px 28px;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
    background: var(--bg); color: var(--fg); line-height: 1.4;
  }
  .wrap { max-width: 540px; margin: 0 auto; display: flex; flex-direction: column; gap: 12px; }
  .header { display: flex; justify-content: space-between; align-items: center; }
  .title { font-size: 18px; font-weight: 700; color: var(--accent); letter-spacing: -0.01em; }
  .pill {
    display: inline-flex; align-items: center; gap: 6px; font-size: 11px; font-weight: 600;
    padding: 4px 9px; border-radius: 999px; background: rgba(52,211,153,0.12);
    color: var(--green); border: 1px solid rgba(52,211,153,0.35);
  }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--green); animation: pulse 1.5s infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }
  .sub { font-size: 12px; color: var(--muted); margin-top: 2px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 14px; }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .kpi-lbl { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
  .kpi-val { font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; margin-top: 2px; }
  .kpi-sm { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; margin-top: 2px; }
  .pos { color: var(--green); }
  .neg { color: var(--red); }
  .sec-title { font-size: 12px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 10px; }
  .fee-row { display: flex; justify-content: space-between; font-size: 13px; padding: 5px 0; border-bottom: 1px solid var(--border); font-variant-numeric: tabular-nums; }
  .fee-row:last-child { border-bottom: none; }
  .mkt { padding: 10px 0; border-bottom: 1px solid var(--border); }
  .mkt:last-child { border-bottom: none; padding-bottom: 0; }
  .mkt-top { display: flex; justify-content: space-between; align-items: center; }
  .mkt-sym { font-weight: 700; font-size: 15px; }
  .badge { font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 6px; border: 1px solid; }
  .mkt-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px; margin-top: 8px; font-size: 12px; font-variant-numeric: tabular-nums; }
  .lbl { font-size: 10px; color: var(--muted); }
  .fill-item { display: flex; justify-content: space-between; font-size: 12px; padding: 6px 0; border-bottom: 1px solid var(--border); font-variant-numeric: tabular-nums; }
  .fill-item:last-child { border-bottom: none; }
  svg { width: 100%; height: 96px; display: block; }
</style>
</head>
<body>
<div class="wrap">
  <div>
    <div class="header">
      <div class="title">MindForge Live</div>
      <div class="pill"><span class="dot"></span><span id="status-txt">LIVE</span></div>
    </div>
    <div class="sub" id="sub-txt">Hyperliquid Mainnet Public Data &middot; $100 Paper &middot; Post-Only</div>
  </div>

  <div class="grid2">
    <div class="card">
      <div class="kpi-lbl">Equity</div>
      <div class="kpi-val" id="eq">$100.00</div>
      <div class="kpi-sm" id="pnl">Net: $0.00 (0.00%)</div>
    </div>
    <div class="card">
      <div class="kpi-lbl">Trading PnL</div>
      <div class="kpi-val" id="tpnl">$0.00</div>
      <div class="kpi-sm" id="fills">0 fills &middot; $0 vol</div>
    </div>
  </div>

  <div class="card">
    <div class="sec-title">Live Equity Curve (USD)</div>
    <svg id="spark" viewBox="0 0 360 96"></svg>
  </div>

  <div class="card">
    <div class="sec-title">Live Markets &amp; Resting Quotes</div>
    <div id="mkts">Connecting to Hyperliquid...</div>
  </div>

  <div class="card">
    <div class="sec-title">All Fees, Gas &amp; Funding Deducted</div>
    <div class="fee-row"><span>Bridge Gas (1-time Arb &rarr; HL)</span><span id="f-bridge">-$0.1500</span></div>
    <div class="fee-row"><span>Trading Fees (1.5 bps maker / 4.5 taker)</span><span id="f-trade">-$0.00000</span></div>
    <div class="fee-row"><span>Hyperliquid L1 Order Gas</span><span id="f-gas">$0.00000 (gasless)</span></div>
    <div class="fee-row"><span>Perp Funding (live rate)</span><span id="f-fund">$0.000000</span></div>
  </div>

  <div class="card">
    <div class="sec-title">Recent Live Fills</div>
    <div id="fill-list" class="kpi-sm">Waiting for live trades to touch resting maker quotes...</div>
  </div>
</div>

<script>
function renderSpark(vals) {
  const svg = document.getElementById("spark");
  if (!vals || vals.length < 2) return;
  const W = 360, H = 96, px = 8, py = 10;
  const mn = Math.min(...vals), mx = Math.max(...vals);
  const span = Math.max(mx - mn, 0.005);
  const pts = vals.map((v, i) => {
    const x = px + (i / (vals.length - 1)) * (W - px * 2);
    const y = py + (H - py * 2) - ((v - mn) / span) * (H - py * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const color = vals[vals.length - 1] >= vals[0] ? "#34d399" : "#22d3ee";
  svg.innerHTML = `
    <polyline fill="none" stroke="${color}" stroke-width="2.2" points="${pts}"/>
    <text x="6" y="12" fill="#94a3b8" font-size="10">$${mx.toFixed(3)}</text>
    <text x="6" y="92" fill="#94a3b8" font-size="10">$${mn.toFixed(3)}</text>
  `;
}

async function poll() {
  try {
    const r = await fetch("/api/state", { cache: "no-store" });
    const d = await r.json();
    const mins = Math.floor(d.uptime_sec / 60);
    const secs = d.uptime_sec % 60;
    document.getElementById("status-txt").textContent = `LIVE ${mins}m ${String(secs).padStart(2,"0")}s`;
    document.getElementById("eq").textContent = `$${d.equity.toFixed(4)}`;

    const pnlEl = document.getElementById("pnl");
    const pct = ((d.net_pnl / d.starting_equity) * 100).toFixed(3);
    pnlEl.textContent = `Net: ${d.net_pnl >= 0 ? "+" : ""}$${d.net_pnl.toFixed(4)} (${pct}%)`;
    pnlEl.className = "kpi-sm " + (d.net_pnl >= 0 ? "pos" : "neg");

    const tpEl = document.getElementById("tpnl");
    tpEl.textContent = `${d.trading_pnl_ex_bridge >= 0 ? "+" : ""}$${d.trading_pnl_ex_bridge.toFixed(4)}`;
    tpEl.className = "kpi-val " + (d.trading_pnl_ex_bridge >= 0 ? "pos" : "neg");

    document.getElementById("fills").textContent = `${d.total_fills} fills (${d.maker_fills}M/${d.taker_fills}T) · $${d.volume_usd.toFixed(1)} vol`;
    document.getElementById("f-bridge").textContent = `-$${d.bridge_gas.toFixed(4)}`;
    document.getElementById("f-trade").textContent = `-$${d.trading_fees.toFixed(5)}`;
    document.getElementById("f-gas").textContent = `$${d.l1_gas.toFixed(5)} (gasless)`;
    document.getElementById("f-fund").textContent = `${d.funding_paid > 0 ? "-" : "+"}$${Math.abs(d.funding_paid).toFixed(6)}`;

    renderSpark(d.equity_curve);

    if (d.markets.length) {
      document.getElementById("mkts").innerHTML = d.markets.map(m => {
        const rkCol = m.risk === "HALT" ? "#f87171" : (m.risk === "WIDEN" ? "#fbbf24" : "#34d399");
        const invCol = m.inv_usd > 0.5 ? "#34d399" : (m.inv_usd < -0.5 ? "#f87171" : "#94a3b8");
        return `
          <div class="mkt">
            <div class="mkt-top">
              <span class="mkt-sym">${m.symbol} <span style="font-weight:400;font-size:12px;color:#94a3b8">$${m.mid.toFixed(5)}</span></span>
              <span class="badge" style="color:${rkCol};border-color:${rkCol}">${m.risk}</span>
            </div>
            <div class="mkt-grid">
              <div><div class="lbl">Our Bid</div><div>${m.bid ? "$" + m.bid.toFixed(5) : "—"}</div></div>
              <div><div class="lbl">Our Ask</div><div>${m.ask ? "$" + m.ask.toFixed(5) : "—"}</div></div>
              <div><div class="lbl">Inventory</div><div style="color:${invCol};font-weight:600">${m.inv_usd >= 0 ? "+" : ""}$${m.inv_usd.toFixed(2)}</div></div>
            </div>
          </div>
        `;
      }).join("");
    }

    if (d.recent_fills.length) {
      document.getElementById("fill-list").innerHTML = d.recent_fills.slice().reverse().map(f => {
        const c = f.side === "BUY" ? "#34d399" : "#f87171";
        return `
          <div class="fill-item">
            <span><strong style="color:${c}">${f.side}</strong> ${f.symbol} (${f.kind})</span>
            <span>$${f.price.toFixed(5)} · $${f.notional.toFixed(2)} (fee $${f.fee.toFixed(4)})</span>
          </div>
        `;
      }).join("");
    }
  } catch (e) {}
}
poll();
setInterval(poll, 2000);
</script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/api/state":
            with STATE_LOCK:
                body = json.dumps(LIVE_STATE).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = MOBILE_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return  # Keep stdout clean


def start_http_server(port: int = 8080) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


async def run_live_paper_bot() -> None:
    root = Path(__file__).resolve().parent.parent
    cfg = MindForgeConfig.from_yaml(root / "configs" / "paper_100_hyperliquid_live.yaml")
    symbols = [m.symbol for m in cfg.markets]

    feed = HyperliquidDataFeed(symbols=symbols, testnet=False)
    inv = PaperInventory(starting_equity_usd=cfg.vault.starting_equity_usd)
    venue = HyperliquidExecutionVenue(
        inventory=inv,
        dry_run=True,
        testnet=False,
        maker_fee_bps=cfg.execution.maker_fee_bps,
        taker_fee_bps=cfg.execution.taker_fee_bps,
        gas_fee_per_tx_usd=cfg.execution.gas_fee_per_tx_usd,
        realistic_live_fills=True,
    )
    engine = MindForgeEngine(cfg, feed, venue, inv, mode_label="HL-LIVE-100USD")
    await feed.start()
    await asyncio.sleep(1.5)

    start_ts = time.time()
    step = 0
    while True:
        step += 1
        rows = await engine.step_once()
        s = engine.tracker.summary()
        pf = venue._paper_fallback

        with STATE_LOCK:
            LIVE_STATE["status"] = "live"
            LIVE_STATE["uptime_sec"] = int(time.time() - start_ts)
            LIVE_STATE["step"] = step
            LIVE_STATE["starting_equity"] = s.starting_equity_usd
            LIVE_STATE["equity"] = s.ending_equity_usd
            LIVE_STATE["net_pnl"] = s.total_pnl_usd
            LIVE_STATE["trading_pnl_ex_bridge"] = s.total_pnl_usd + engine.bridge_gas_paid_usd
            LIVE_STATE["realized_pnl"] = s.realized_pnl_usd
            LIVE_STATE["unrealized_pnl"] = s.unrealized_pnl_usd
            LIVE_STATE["bridge_gas"] = engine.bridge_gas_paid_usd
            LIVE_STATE["trading_fees"] = pf.total_trading_fees_usd
            LIVE_STATE["l1_gas"] = pf.total_gas_fees_usd
            LIVE_STATE["funding_paid"] = engine.total_funding_paid_usd
            LIVE_STATE["max_drawdown_pct"] = s.max_drawdown_pct
            LIVE_STATE["total_fills"] = s.total_fills
            LIVE_STATE["maker_fills"] = pf.maker_fills
            LIVE_STATE["taker_fills"] = pf.taker_fills
            LIVE_STATE["volume_usd"] = s.total_volume_usd
            LIVE_STATE["markets"] = [
                {
                    "symbol": r.symbol,
                    "mid": r.mid_price,
                    "fv": r.fv.fair_value,
                    "bid": r.plan.bids[0].price if r.plan.bids else None,
                    "ask": r.plan.asks[0].price if r.plan.asks else None,
                    "spread_bps": r.plan.half_spread_bps,
                    "inv_usd": r.position.notional_usd(r.mid_price),
                    "risk": r.risk.action.value.upper(),
                }
                for r in rows
            ]
            LIVE_STATE["recent_fills"] = [
                {
                    "symbol": f.symbol,
                    "side": f.side.value.upper(),
                    "kind": "MAKER" if f.is_maker else "TAKER",
                    "price": f.price,
                    "notional": f.notional_usd,
                    "fee": f.fee_usd,
                }
                for f in engine.tracker.fills[-15:]
            ]
            curve = LIVE_STATE["equity_curve"]
            curve.append(round(s.ending_equity_usd, 4))
            if len(curve) > 120:
                del curve[:-120]

        await asyncio.sleep(cfg.execution.tick_interval_sec)


if __name__ == "__main__":
    start_http_server(8080)
    print("MindForge Mobile Live Dashboard listening on http://0.0.0.0:8080", flush=True)
    asyncio.run(run_live_paper_bot())
