"""Pluggable mindshare signal providers."""

from mindforge.data.base import MindshareProvider
from mindforge.data.mindshare.kaito_style import KaitoStyleMindshareProvider
from mindforge.data.mindshare.social_velocity import SocialVelocityProvider

__all__ = [
    "MindshareProvider",
    "KaitoStyleMindshareProvider",
    "SocialVelocityProvider",
]
