"""Social / X-Twitter keyword velocity and watchlist-delta provider.

Supports plugging in a lightweight search/velocity proxy endpoint or a
custom async callback (e.g., polling X API v2 recent counts, Farcaster
casts, or Telegram channel velocity).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable

import httpx

from mindforge.data.base import MindshareProvider
from mindforge.types import MindshareMetric, utc_now

logger = logging.getLogger(__name__)

VelocityFetcher = Callable[[str], Awaitable[dict[str, float]]]


class SocialVelocityProvider(MindshareProvider):
    """Fetches keyword mention velocity and watchlist changes."""

    def __init__(
        self,
        api_url: str | None = None,
        bearer_token: str | None = None,
        custom_fetcher: VelocityFetcher | None = None,
        timeout_sec: float = 5.0,
    ) -> None:
        self.api_url = (api_url or os.getenv("MINDFORGE_SOCIAL_API_URL", "")).rstrip("/")
        self.bearer_token = bearer_token or os.getenv("MINDFORGE_SOCIAL_BEARER_TOKEN", "")
        self._custom_fetcher = custom_fetcher
        self._client = httpx.AsyncClient(timeout=timeout_sec)

    async def fetch(self, symbol: str) -> MindshareMetric | None:
        try:
            if self._custom_fetcher is not None:
                data = await self._custom_fetcher(symbol)
            elif self.api_url:
                headers = {"Accept": "application/json"}
                if self.bearer_token:
                    headers["Authorization"] = f"Bearer {self.bearer_token}"
                resp = await self._client.get(
                    f"{self.api_url}/velocity",
                    params={"symbol": symbol},
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
            else:
                return None

            return MindshareMetric(
                symbol=symbol,
                narrative_share=float(data.get("narrative_share", 0.05)),
                social_velocity=float(data.get("social_velocity", 0.0)),
                watchlist_delta=float(data.get("watchlist_delta", 0.0)),
                sentiment_dispersion=float(data.get("sentiment_dispersion", 0.3)),
                timestamp=utc_now(),
                source="social_velocity",
            )
        except Exception as exc:
            logger.warning("SocialVelocityProvider fetch failed for %s: %s", symbol, exc)
            return None

    async def close(self) -> None:
        await self._client.aclose()
