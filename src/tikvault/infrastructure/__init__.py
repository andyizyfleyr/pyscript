"""Couche infrastructure : adaptateurs concrets (yt-dlp, SQLite, HTTP, système)."""

from __future__ import annotations

from tikvault.infrastructure.cookies import CookieStore
from tikvault.infrastructure.db.engine import Database
from tikvault.infrastructure.db.repository import SqlTaskRepository
from tikvault.infrastructure.extractors.fallback import TikTokWebExtractor
from tikvault.infrastructure.extractors.registry import ChainExtractor, ExtractorRegistry
from tikvault.infrastructure.extractors.ytdlp import YtDlpExtractor
from tikvault.infrastructure.logging_setup import setup_logging
from tikvault.infrastructure.notifier import ConsoleNotifier, NotifySendNotifier, NullNotifier
from tikvault.infrastructure.paths import AppPaths, default_paths
from tikvault.infrastructure.proxy_pool import ProxyPool, parse_proxy_list
from tikvault.infrastructure.thumbnails import ThumbnailCache
from tikvault.infrastructure.updater import YtDlpUpdater, current_version, is_frozen
from tikvault.infrastructure.user_agents import RotatingUserAgents

__all__ = [
    "AppPaths",
    "ChainExtractor",
    "ConsoleNotifier",
    "CookieStore",
    "Database",
    "ExtractorRegistry",
    "NotifySendNotifier",
    "NullNotifier",
    "ProxyPool",
    "RotatingUserAgents",
    "SqlTaskRepository",
    "ThumbnailCache",
    "TikTokWebExtractor",
    "YtDlpExtractor",
    "YtDlpUpdater",
    "current_version",
    "default_paths",
    "is_frozen",
    "parse_proxy_list",
    "setup_logging",
]
