# MindForge

**Open-source, capital-light AI market-making agent driven by real-time mindshare & on-chain agent activity signals.**

MindForge is a modular Python 3.11+ quoting and risk engine built for **AI agent tokens**, **narrative/attention contracts**, and **long-tail prediction or outcome markets**. It is designed for solo developers without large proprietary capital: it defaults to paper trading and deterministic simulation, supports tiny live order sizes (\$25–\$50 clips), and abstracts capital behind an `InventorySource` interface so the exact same strategy can run as the engine for a **Hyperliquid-style community vault** or **token-loan inventory deal**.

---

## Core Thesis

Traditional market makers treat social narrative shifts and on-chain agent activity as unmodeled noise—and get run over when attention spikes or reverses. **MindForge treats mindshare and on-chain agent activity as first-class features** across three layers:

1. **Fair-Value Estimation** (`FairValueEngine`): Blends top-of-book microprice, funding carry, normalized mindshare z-scores, on-chain agent demand z-scores, and an optional pluggable `AttentionForecaster` (EWMA default, or your own PydanticAI / LangGraph / LLM function).
2. **Inventory-Skewed Dynamic Quoting** (`QuotingEngine`): Avellaneda-Stoikov–inspired reservation price that leans bids when short and asks when long, while widening spreads and shrinking quote sizes when **attention instability** or **hype divergence** (social buzz outrunning actual on-chain usage) spikes.
3. **Capital-Light Risk Controls** (`RiskEngine`): Per-market position caps, gross exposure limits, daily loss & peak-to-trough drawdown kill switches, post-fill markout **adverse-selection (toxic-flow) guard**, stale-feed halts, and mindshare-shock circuit breakers.

---

## Quickstart

### 1. Install (Python 3.11+)

```bash
git clone https://github.com/arbonomous/MindForge.git
cd MindForge

# Using uv (fast) or pip
pip install -e ".[dev]"
```

### 2. Run a Paper-Trading Simulation (Zero API Keys Required)

Watch the live `Rich` terminal dashboard cycle through `calm`, `narrative_pump`, `hype_divergence_shock`, and `agent_launch_wave` regimes:

```bash
mindforge simulate --config configs/paper_ai_agents.yaml --steps 300 --report out/report.html
```

Or run headless (fast, no sleep) and generate a self-contained HTML report with inline SVG charts:

```bash
mindforge simulate --config configs/paper_ai_agents.yaml --steps 400 --no-live --report out/report.html
```

### 3. Stream Live Public Hyperliquid Order Books (Paper / Dry-Run Mode)

No API keys or private keys are needed to stream live Hyperliquid L2 order books and funding rates while paper-simulating fills:

```bash
mindforge run --config configs/hyperliquid_vault_example.yaml --feed hyperliquid --steps 100
```

### 4. Run Tests & Examples

```bash
pytest -v
python examples/run_paper_sim.py
python examples/custom_llm_forecaster.py
```

---

## Project Architecture

```text
MindForge/
├── configs/
│   ├── paper_ai_agents.yaml           # Capital-light paper config ($1,000 equity, $25-$35 clips)
│   └── hyperliquid_vault_example.yaml # Annotated Hyperliquid vault / tiny live capital config
├── examples/
│   ├── run_paper_sim.py               # Standalone paper simulation + HTML report
│   └── custom_llm_forecaster.py       # Plugging in a custom async / LLM forecaster
├── src/mindforge/
│   ├── types.py                       # Immutable domain models (OrderBook, SignalSnapshot, QuotePlan, etc.)
│   ├── config.py                      # Pydantic v2 YAML + .env settings loader
│   ├── data/
│   │   ├── base.py                    # Abstract MarketDataFeed, MindshareProvider, OnChainAgentProvider
│   │   ├── hyperliquid_feed.py        # Public Hyperliquid REST + WebSocket L2 book & funding feed
│   │   ├── synthetic.py               # Coupled synthetic market + mindshare + on-chain regime generator
│   │   ├── mindshare/                 # Kaito-style narrative share & social keyword velocity adapters
│   │   └── onchain/                   # On-chain wallet clusters, inference proxies, agent transfers, launches
│   ├── signals/
│   │   ├── aggregator.py              # Rolling z-scores, hype_divergence, attention_instability
│   │   └── forecaster.py              # StatisticalEWMAForecaster + CallableForecaster (LLM/ML hook)
│   ├── models/
│   │   └── fair_value.py              # Signal-adjusted fair value & realized volatility
│   ├── quoting/
│   │   └── engine.py                  # Inventory skew, dynamic spreads, size scaling, tick/lot rounding
│   ├── risk/
│   │   └── engine.py                  # Limits, drawdown kill switch, toxic-flow guard, shock circuit breaker
│   ├── execution/
│   │   ├── base.py                    # Abstract ExecutionVenue, InventorySource, OrderSigner
│   │   ├── inventory.py               # PaperInventory + VaultInventoryHook (Hyperliquid vault / token loans)
│   │   ├── paper.py                   # Realistic paper fill simulator with maker rebates/fees
│   │   └── hyperliquid.py             # Hyperliquid connector with vaultAddress & dry_run=True safety guard
│   ├── engine/
│   │   ├── runner.py                  # Async MindForgeEngine orchestrator
│   │   └── backtester.py              # Deterministic synthetic & CSV-replay backtest runner
│   ├── reporting/
│   │   ├── metrics.py                 # PnL, drawdown, Sharpe, fill rate, volume tracking
│   │   ├── dashboard.py               # Rich live terminal TUI dashboard
│   │   └── html_report.py             # Self-contained dark-mode HTML summary report (inline SVG)
│   └── cli.py                         # `mindforge simulate | run | backtest`
└── tests/                             # Unit & end-to-end pytest suite
```

---

## How Mindshare & On-Chain Signals Work

1. **Mindshare Feeds** (`MindshareProvider`):
   - `KaitoStyleMindshareProvider`: polls narrative share, social velocity, watchlist delta, and sentiment dispersion from an HTTP endpoint (or falls back gracefully when unset).
   - `SocialVelocityProvider`: plugs into X/Twitter keyword velocity or any custom async callback.
2. **On-Chain Agent Activity** (`OnChainAgentProvider`):
   - `OnChainAgentActivityProvider`: tracks active agent wallet clusters, inference/compute volume proxies, agent-to-agent transfer volume, and new agent launches.
3. **Derived Features** (`SignalAggregator`):
   - `mindshare_score` and `onchain_score`: rolling z-scores clamped to `[-3, +3]`.
   - `hype_divergence = max(0, mindshare_score - onchain_score)`: flags viral social spikes that lack confirming on-chain usage.
   - `attention_instability`: combines social velocity variance, sentiment dispersion, and `hype_divergence`. High instability **widens spreads**, **shrinks clip sizes**, and can trigger the **mindshare shock circuit breaker**.

### Plugging in an LLM / Agent Forecaster (PydanticAI, LangGraph, or Plain Python)

Wrap any `async def(signal: SignalSnapshot, book: OrderBookSnapshot) -> ForecastResult` with `CallableForecaster`:

```python
from mindforge.signals.forecaster import CallableForecaster
from mindforge.types import ForecastResult

async def my_llm_forecaster(signal, book) -> ForecastResult:
    # Call PydanticAI, LangGraph, or an HTTP model endpoint here
    return ForecastResult(bias_bps=8.0, confidence=0.75, regime_note="llm_bullish")

forecaster = CallableForecaster(my_llm_forecaster)
```

See [examples/custom_llm_forecaster.py](examples/custom_llm_forecaster.py) for a complete runnable script.

---

## Attaching Tiny Live Capital or a Community Vault

MindForge decouples **capital** from **strategy**:

1. **Hyperliquid Community Vault**:
   - Set `vault.mode: hyperliquid_vault` and `MINDFORGE_HL_VAULT_ADDRESS=0x...` in `.env` (see [configs/hyperliquid_vault_example.yaml](configs/hyperliquid_vault_example.yaml)).
   - `VaultInventoryHook.sync()` reads live vault TVL from Hyperliquid's `/info` `vaultDetails` endpoint and allocates `vault_budget_fraction` to the strategy.
   - `HyperliquidExecutionVenue` attaches `vaultAddress` to order payloads and defaults to `dry_run=True` for safety. To go live on testnet or mainnet, implement the 1-method `OrderSigner` interface (e.g., using `eth-account` or `hyperliquid-python-sdk`) and set `dry_run: false`.
2. **Token-Loan Market-Making Deals**:
   - Populate `vault.token_loan_inventory` in YAML (e.g., `{VIRTUAL: 5000}`) to track borrowed project inventory alongside tiny stablecoin working capital.
3. **Adding New Venues (Solana DEX / Polymarket)**:
   - Implement `MarketDataFeed` (`get_order_book`, `get_funding`) and `ExecutionVenue` (`place_quotes`, `cancel_all`, `get_open_orders`) — zero changes needed in `FairValueEngine`, `QuotingEngine`, or `RiskEngine`.

---

## Optional Docker Usage

```bash
docker compose up --build
# Writes out/simulation_report.html on your host
```

---

## Disclaimer

MindForge is open-source research and trading infrastructure provided **as-is** under the MIT License. Market making in volatile, low-liquidity AI agent and narrative markets carries substantial risk of loss. Always start in paper-trading mode and test thoroughly on testnet with tiny capital before risking real funds.