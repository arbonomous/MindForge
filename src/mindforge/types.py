"""Core domain data structures for MindForge.

All inter-module messages use lightweight, typed dataclasses so that
data feeds, signal providers, fair-value models, quoting engines, risk
engines, and execution venues stay cleanly decoupled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class RiskAction(StrEnum):
    ALLOW = "allow"
    WIDEN = "widen"
    UNWIND_ONLY = "unwind_only"
    HALT = "halt"


@dataclass(frozen=True)
class OrderBookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class OrderBookSnapshot:
    symbol: str
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    timestamp: datetime = field(default_factory=utc_now)
    venue: str = "synthetic"

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 0.0

    @property
    def mid_price(self) -> float:
        if not self.bids or not self.asks:
            return 0.0
        return 0.5 * (self.best_bid + self.best_ask)

    @property
    def microprice(self) -> float:
        """Volume-weighted top-of-book microprice."""
        if not self.bids or not self.asks:
            return 0.0
        b_px, b_sz = self.bids[0].price, self.bids[0].size
        a_px, a_sz = self.asks[0].price, self.asks[0].size
        total = b_sz + a_sz
        if total <= 0:
            return self.mid_price
        return (b_px * a_sz + a_px * b_sz) / total

    @property
    def spread_bps(self) -> float:
        mid = self.mid_price
        if mid <= 0:
            return 0.0
        return (self.best_ask - self.best_bid) / mid * 10_000.0


@dataclass(frozen=True)
class TradeTick:
    symbol: str
    price: float
    size: float
    side: Side
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class FundingSnapshot:
    symbol: str
    hourly_rate: float  # e.g. 0.0001 = 1 bp / hour
    open_interest_usd: float = 0.0
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class MindshareMetric:
    """Raw mindshare / attention metrics from an external or synthetic feed."""

    symbol: str
    narrative_share: float  # 0.0 .. 1.0 (e.g., Kaito-style share of AI narrative)
    social_velocity: float  # Mentions/posts per minute (or normalized rate)
    watchlist_delta: float  # Net watchlist / alert additions over rolling window
    sentiment_dispersion: float  # 0.0 .. 1.0 (higher = polarizing / unstable chatter)
    timestamp: datetime = field(default_factory=utc_now)
    source: str = "synthetic"


@dataclass(frozen=True)
class OnChainAgentMetric:
    """Raw on-chain AI agent activity metrics."""

    symbol: str
    active_agent_wallets: int  # Distinct agent wallet clusters active in window
    inference_volume_proxy: float  # Proxy for compute/inference demand (e.g., fee/call volume)
    agent_to_agent_transfers: float  # USD volume of agent-to-agent settlements
    new_agent_launches: int  # Newly deployed agent contracts/tokens in ecosystem
    timestamp: datetime = field(default_factory=utc_now)
    source: str = "synthetic"


@dataclass(frozen=True)
class SignalSnapshot:
    """Normalized, aggregated mindshare + on-chain feature vector for one market."""

    symbol: str
    mindshare_score: float  # Clamped z-score of narrative share + velocity + watchlist
    onchain_score: float  # Clamped z-score of wallet clusters + inference + transfers
    hype_divergence: float  # max(0, mindshare_score - onchain_score): unbacked hype indicator
    attention_instability: float  # >= 0.0; drives spread widening & size reduction
    raw_mindshare: MindshareMetric | None = None
    raw_onchain: OnChainAgentMetric | None = None
    regime: str = "normal"
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class ForecastResult:
    """Output of a statistical or LLM/ML AttentionForecaster."""

    bias_bps: float  # Directional fair-value adjustment in basis points
    confidence: float  # 0.0 .. 1.0
    regime_note: str = ""


@dataclass(frozen=True)
class FairValueEstimate:
    symbol: str
    microprice: float
    fair_value: float
    alpha_bps: float
    realized_vol_bps: float
    attention_instability: float
    funding_tilt_bps: float = 0.0
    forecast_bias_bps: float = 0.0
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class QuoteOrder:
    symbol: str
    side: Side
    price: float
    size: float  # Base asset units
    level: int = 0
    client_order_id: str = ""

    @property
    def notional_usd(self) -> float:
        return self.price * self.size


@dataclass(frozen=True)
class QuotePlan:
    """Target two-sided (or one-sided unwind) quotes produced by the QuotingEngine."""

    symbol: str
    reservation_price: float
    half_spread_bps: float
    inventory_ratio: float
    bids: tuple[QuoteOrder, ...]
    asks: tuple[QuoteOrder, ...]
    timestamp: datetime = field(default_factory=utc_now)

    @property
    def all_orders(self) -> list[QuoteOrder]:
        return list(self.bids) + list(self.asks)


@dataclass(frozen=True)
class FillEvent:
    symbol: str
    side: Side
    price: float
    size: float
    fee_usd: float  # Negative for maker rebate, positive for fee
    order_id: str
    mid_at_fill: float
    timestamp: datetime = field(default_factory=utc_now)

    @property
    def notional_usd(self) -> float:
        return self.price * self.size


@dataclass
class Position:
    symbol: str
    size: float = 0.0  # Positive = long, negative = short
    avg_entry_price: float = 0.0
    realized_pnl_usd: float = 0.0
    fees_usd: float = 0.0

    def notional_usd(self, mark_price: float) -> float:
        return self.size * mark_price

    def unrealized_pnl_usd(self, mark_price: float) -> float:
        if self.size == 0.0 or mark_price <= 0.0:
            return 0.0
        return self.size * (mark_price - self.avg_entry_price)


@dataclass(frozen=True)
class RiskDecision:
    action: RiskAction
    size_multiplier: float = 1.0  # 0.0 .. 1.0
    spread_multiplier: float = 1.0  # >= 1.0
    toxic_flow_addon_bps: float = 0.0
    reasons: tuple[str, ...] = ()

    @property
    def can_quote(self) -> bool:
        return self.action != RiskAction.HALT
