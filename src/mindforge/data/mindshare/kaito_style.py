"""Kaito-style narrative mindshare adapter (HTTP API + graceful local fallback).

Expects a JSON endpoint returning fields similar to:
  {
    "narrative_share": 0.082,       # 8.2% share of AI agent mindshare
    "social_velocity": 42.5,        # mentions/min
    "watchlist_delta": 14.0,        # net watchlist adds
    "sentiment_dispersion": 0.35    # 0..1 disagreement/volatility
  }
If no API URL is configured, users can supply a local replay map or pair
this with MindForge's SyntheticEnvironment.
"""

from __future__ import annotations

import logging
import os

import httpx

from mindforge.data.base import MindshareProvider
from mindforge.types import MindshareMetric, utc_now

logger = logging.getLogger(__name__)


class KaitoStyleMindshareProvider(MindshareProvider):
    """Fetches Kaito-style narrative share & attention velocity from an HTTP endpoint."""

    def __init__(
        self,
        api_url: str | None = None,
        api_key: str | None = None,
        timeout_sec: float = 5.0,
    ) -> None:
        self.api_url = (api_url or os.getenv("MINDFORGE_KAITO_API_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("MINDFORGE_KAITO_API_KEY", "")
        self._client = httpx.AsyncClient(timeout=timeout_sec)

    async def fetch(self, symbol: str) -> MindshareMetric | None:
        if not self.api_url:
            return None

        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            resp = await self._client.get(
                f"{self.api_url}/mindshare/{symbol}",
                headers=headers,
            )
            resp.raise_for_status()
            payload = resp.json()
            return MindshareMetric(
                symbol=symbol,
                narrative_share=float(payload.get("narrative_share", 0.0)),
                social_velocity=float(payload.get("social_velocity", 0.0)),
                watchlist_delta=float(payload.get("watchlist_delta", 0.0)),
                sentiment_dispersion=float(payload.get("sentiment_dispersion", 0.25)),
                timestamp=utc_now(),
                source="kaito_style_http",
            )
        except Exception as exc:
            logger.warning("KaitoStyleMindshareProvider fetch failed for %s: %s", symbol, exc)
            return None

    async def close(self) -> None:
        await self._client.aclose()
