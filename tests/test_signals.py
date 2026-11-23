"""Tests for SignalAggregator (z-scores, hype divergence, attention instability)."""

from mindforge.signals.aggregator import SignalAggregator
from mindforge.types import MindshareMetric, OnChainAgentMetric


def test_hype_divergence_and_instability_spike_on_unbacked_social_surge() -> None:
    agg = SignalAggregator(rolling_window=20)

    # Warm up with calm baseline observations
    for i in range(10):
        agg.update(
            "VIRTUAL",
            mindshare=MindshareMetric(
                symbol="VIRTUAL",
                narrative_share=0.06 + (i % 2) * 0.002,
                social_velocity=25.0 + (i % 2) * 1.0,
                watchlist_delta=4.0 + (i % 2) * 0.5,
                sentiment_dispersion=0.20,
            ),
            onchain=OnChainAgentMetric(
                symbol="VIRTUAL",
                active_agent_wallets=400 + (i % 2) * 5,
                inference_volume_proxy=10_000.0 + (i % 2) * 200.0,
                agent_to_agent_transfers=80_000.0 + (i % 2) * 1000.0,
                new_agent_launches=3 + (i % 2),
            ),
        )

    # Inject viral social buzz with flat/falling on-chain activity
    shock = agg.update(
        "VIRTUAL",
        mindshare=MindshareMetric(
            symbol="VIRTUAL",
            narrative_share=0.22,
            social_velocity=150.0,
            watchlist_delta=45.0,
            sentiment_dispersion=0.88,
        ),
        onchain=OnChainAgentMetric(
            symbol="VIRTUAL",
            active_agent_wallets=350,
            inference_volume_proxy=9_000.0,
            agent_to_agent_transfers=70_000.0,
            new_agent_launches=1,
        ),
    )

    assert shock.mindshare_score > 1.5
    assert shock.onchain_score < 0.0
    assert shock.hype_divergence > 1.5
    assert shock.attention_instability > 1.5
    assert shock.regime == "hype_divergence"
