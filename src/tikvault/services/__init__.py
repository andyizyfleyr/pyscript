"""Couche services : orchestration applicative (aucune dépendance à Qt ni à yt-dlp)."""

from __future__ import annotations

from tikvault.services.dedupe import Blake2Hasher, DedupeService
from tikvault.services.disk import (
    directory_size,
    ensure_writable,
    free_bytes,
    human_duration,
    human_size,
)
from tikvault.services.download_service import DownloadService
from tikvault.services.filename import render, resolve_destination, validate_template
from tikvault.services.progress import ProgressRelay
from tikvault.services.queue_service import DownloadQueue
from tikvault.services.report_service import ReportService
from tikvault.services.retry import RetryPolicy, retry_async
from tikvault.services.source_resolver import SourceResolver
from tikvault.services.url_parser import ParsedSource, SourceParser
from tikvault.services.watch_service import AccountWatcher

__all__ = [
    "AccountWatcher",
    "Blake2Hasher",
    "DedupeService",
    "DownloadQueue",
    "DownloadService",
    "ParsedSource",
    "ProgressRelay",
    "ReportService",
    "RetryPolicy",
    "SourceParser",
    "SourceResolver",
    "directory_size",
    "ensure_writable",
    "free_bytes",
    "human_duration",
    "human_size",
    "render",
    "resolve_destination",
    "retry_async",
    "validate_template",
]
