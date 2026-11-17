"""Moteurs de téléchargement (architecture « plugins »)."""

from __future__ import annotations

from tikvault.infrastructure.extractors.fallback import TikTokWebExtractor
from tikvault.infrastructure.extractors.registry import ChainExtractor, ExtractorRegistry
from tikvault.infrastructure.extractors.ytdlp import YtDlpExtractor

__all__ = ["ChainExtractor", "ExtractorRegistry", "TikTokWebExtractor", "YtDlpExtractor"]
