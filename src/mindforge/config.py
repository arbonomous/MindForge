"""Configuration schema and YAML + .env loader for MindForge."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


class MarketConfig(BaseModel):
    """Per-market quoting and capital-light sizing configuration."""

    symbol: str
    initial_price: float = Field(default=1.0, gt=0.0, description="Used only in synthetic mode.")
    tick_size: float = Field(default=0.0001, gt=0.0)
    lot_size: float = Field(default=1.0, gt=0.0)
    min_notional_usd: float = Field(default=10.0, gt=0.0)
    base_order_usd: float = Field(
        default=30.0,
        gt=0.0,
        description="Capital-light per-level quote size in USD.",
    )
    max_position_usd: float = Field(
        default=150.0,
        gt=0.0,
        description="Maximum absolute inventory allowed in this market (USD).",
    )
    base_half_spread_bps: float = Field(
        default=18.0,
        gt=0.0,
        description="Baseline half-spread in basis points under calm attention.",
    )


class SignalWeightsConfig(BaseModel):
    """Controls how mindshare, on-chain activity, and forecasts move fair value."""

    mindshare_weight_bps: float = Field(
        default=8.0,
        description="Fair-value shift (bps) per 1.0 unit of normalized mindshare z-score.",
    )
    onchain_weight_bps: float = Field(
        default=10.0,
        description="Fair-value shift (bps) per 1.0 unit of normalized on-chain agent z-score.",
    )
    forecast_weight: float = Field(
        default=0.5,
        ge=0.0,
        le=2.0,
        description="Multiplier applied to AttentionForecaster bias_bps.",
    )
    funding_weight: float = Field(
        default=0.25,
        ge=0.0,
        description="Weight on hourly funding rate (converted to bps).",
    )
    max_fv_shift_bps: float = Field(
        default=45.0,
        gt=0.0,
        description="Hard cap on total signal-driven fair-value shift (bps).",
    )
    rolling_window: int = Field(
        default=30,
        ge=5,
        description="Rolling observations used for z-score normalization and volatility.",
    )


class QuotingConfig(BaseModel):
    """Avellaneda-Stoikov–inspired quoting parameters."""

    inventory_skew_bps: float = Field(
        default=25.0,
        ge=0.0,
        description="Reservation price shift (bps) at 100% of max_position_usd.",
    )
    vol_spread_multiplier: float = Field(
        default=0.6,
        ge=0.0,
        description="Extra half-spread bps per 1 bp of realized volatility.",
    )
    instability_spread_bps: float = Field(
        default=20.0,
        ge=0.0,
        description="Extra half-spread bps per 1.0 unit of attention_instability.",
    )
    instability_size_decay: float = Field(
        default=0.55,
        ge=0.0,
        description="Reduces quote size by 1 / (1 + decay * attention_instability).",
    )
    min_half_spread_bps: float = Field(default=6.0, gt=0.0)
    max_half_spread_bps: float = Field(default=250.0, gt=0.0)
    levels_per_side: int = Field(default=2, ge=1, le=5)
    level_spacing_bps: float = Field(default=10.0, gt=0.0)


class RiskConfig(BaseModel):
    """Risk engine limits, kill switches, and adverse-selection guards."""

    max_gross_exposure_usd: float = Field(default=400.0, gt=0.0)
    max_daily_loss_usd: float = Field(default=40.0, gt=0.0)
    max_drawdown_pct: float = Field(
        default=0.08,
        gt=0.0,
        le=1.0,
        description="Kill switch triggers if equity drops by this fraction from peak (e.g. 0.08 = 8%).",
    )
    mindshare_widen_threshold: float = Field(
        default=1.4,
        gt=0.0,
        description="Attention instability level above which risk engine widens spreads & trims size.",
    )
    mindshare_halt_threshold: float = Field(
        default=2.8,
        gt=0.0,
        description="Extreme attention shock threshold that pauses quoting.",
    )
    toxic_flow_markout_bps: float = Field(
        default=-12.0,
        description="If rolling average post-fill markout is worse than this (bps), widen spreads.",
    )
    toxic_flow_addon_bps: float = Field(default=15.0, ge=0.0)
    max_feed_age_sec: float = Field(default=30.0, gt=0.0)


class VaultConfig(BaseModel):
    """Capital-light inventory / vault hook configuration."""

    mode: Literal["paper", "own_wallet", "hyperliquid_vault", "token_loan"] = "paper"
    starting_equity_usd: float = Field(
        default=1000.0,
        gt=0.0,
        description="Simulated capital in paper mode, or capital budget cap in live/vault mode.",
    )
    vault_address: str | None = Field(
        default=None,
        description="Optional Hyperliquid vault address (0x...) when acting as vault strategy engine.",
    )
    vault_budget_fraction: float = Field(
        default=0.25,
        gt=0.0,
        le=1.0,
        description="Fraction of total vault equity this strategy is allowed to deploy.",
    )
    token_loan_inventory: dict[str, float] = Field(
        default_factory=dict,
        description="Optional initial borrowed token inventory per symbol for token-loan MM deals.",
    )


class ExecutionConfig(BaseModel):
    venue: Literal["paper", "hyperliquid"] = "paper"
    dry_run: bool = Field(
        default=True,
        description="Safety guard: prevents live order placement even on the Hyperliquid connector.",
    )
    maker_fee_bps: float = Field(
        default=-0.5,
        description="Negative = maker rebate (bps), positive = fee (bps).",
    )
    fill_probability_base: float = Field(default=0.35, gt=0.0, le=1.0)
    tick_interval_sec: float = Field(default=0.5, gt=0.0)
    hyperliquid_testnet: bool = False


class MindForgeConfig(BaseModel):
    """Top-level configuration object for a MindForge agent instance."""

    agent_name: str = "mindforge-paper-v1"
    markets: list[MarketConfig]
    signals: SignalWeightsConfig = Field(default_factory=SignalWeightsConfig)
    quoting: QuotingConfig = Field(default_factory=QuotingConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    vault: VaultConfig = Field(default_factory=VaultConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> MindForgeConfig:
        """Load configuration from YAML and apply optional .env overrides."""
        load_dotenv(override=False)
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        cfg = cls.model_validate(raw)

        # Allow environment variable to supply vault address without storing it in git
        env_vault = os.getenv("MINDFORGE_HL_VAULT_ADDRESS")
        if env_vault and not cfg.vault.vault_address:
            cfg.vault.vault_address = env_vault
        return cfg

    def get_market(self, symbol: str) -> MarketConfig:
        for m in self.markets:
            if m.symbol == symbol:
                return m
        raise KeyError(f"Unknown market symbol: {symbol}")
