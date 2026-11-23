"""On-chain AI agent activity provider.

Tracks fundamental on-chain demand signals for AI agent ecosystems:
- Active agent wallet clusters
- Inference / compute payment volume proxies
- Agent-to-agent settlement transfers (USD)
- New agent launches in the ecosystem

Supports either a REST indexer endpoint (e.g., Dune/Flipside/custom subgraph)
or a custom async callback so solo developers can plug in any chain indexer.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable

import httpx

from mindforge.data.base import OnChainAgentProvider
from mindforge.types import OnChainAgentMetric, utc_now

logger = logging.getLogger(__name__)

OnChainFetcher = Callable[[str], Awaitable[dict[str, float]]]


class OnChainAgentActivityProvider(OnChainAgentProvider):
    """Fetches on-chain AI agent ecosystem metrics via HTTP indexer or callback."""

    def __init__(
        self,
        api_url: str | None = None,
        api_key: str | None = None,
        custom_fetcher: OnChainFetcher | None = None,
        timeout_sec: float = 5.0,
    ) -> None:
        self.api_url = (api_url or os.getenv("MINDFORGE_ONCHAIN_API_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("MINDFORGE_ONCHAIN_API_KEY", "")
        self._custom_fetcher = custom_fetcher
        self._client = httpx.AsyncClient(timeout=timeout_sec)

    async def fetch(self, symbol: str) -> OnChainAgentMetric | None:
        try:
            if self._custom_fetcher is not None:
                data = await self._custom_fetcher(symbol)
            elif self.api_url:
                headers = {"Accept": "application/json"}
                if self.api_key:
                    headers["X-API-Key"] = self.api_key
                resp = await self._client.get(
                    f"{self.api_url}/agents/{symbol}",
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
            else:
                return None

            return OnChainAgentMetric(
                symbol=symbol,
                active_agent_wallets=int(data.get("active_agent_wallets", 0)),
                inference_volume_proxy=float(data.get("inference_volume_proxy", 0.0)),
                agent_to_agent_transfers=float(data.get("agent_to_agent_transfers", 0.0)),
                new_agent_launches=int(data.get("new_agent_launches", 0)),
                timestamp=utc_now(),
                source="onchain_http",
            )
        except Exception as exc:
            logger.warning("OnChainAgentActivityProvider fetch failed for %s: %s", symbol, exc)
            return None

    async def close(self) -> None:
        await self._client.aclose()
