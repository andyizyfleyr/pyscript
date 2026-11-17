"""Fixtures partagées : bouchons d'extracteur, base en mémoire, conteneur câblé."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import date, timedelta
from pathlib import Path

import pytest

from tikvault.domain import ports
from tikvault.domain.errors import ExtractionError
from tikvault.domain.events import EngineEvent, EventBus
from tikvault.domain.models import (
    DownloadOptions,
    DownloadTask,
    SourceType,
    VideoMeta,
    WatchedAccount,
)
from tikvault.infrastructure.db.engine import Database
from tikvault.infrastructure.db.repository import SqlTaskRepository
from tikvault.services.download_service import DownloadService
from tikvault.services.queue_service import DownloadQueue
from tikvault.services.source_resolver import SourceResolver
from tikvault.services.url_parser import ParsedSource

VIDEO_ID = "7123456789012345678"
VIDEO_URL = f"https://www.tiktok.com/@alice/video/{VIDEO_ID}"


class FakeExtractor(ports.VideoExtractor):
    """Extracteur factice : écrit un fichier déterministe, sans réseau.

    * ``fail_times`` : nombre d'échecs avant succès (teste les relances) ;
    * ``fail_urls``  : URLs qui échouent toujours ;
    * ``content_for``: contenu par URL (teste le dédoublonnage par empreinte) ;
    * ``playlist``   : vidéos renvoyées pour une source « large » (profil, hashtag).
    """

    name = "fake"
    priority = 90

    def __init__(
        self,
        *,
        fail_times: int = 0,
        fail_urls: set[str] | None = None,
        content_for: dict[str, bytes] | None = None,
        playlist: list[VideoMeta] | None = None,
        probe_error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.fail_times = fail_times
        self.fail_urls = fail_urls or set()
        self.content_for = content_for or {}
        self.playlist = playlist or []
        self.probe_error = probe_error
        self.delay = delay
        self.attempts: dict[str, int] = {}
        self.downloaded: list[str] = []
        self.contexts: list[ports.ExtractionContext] = []

    def supports(self, url: str) -> bool:
        return url.startswith("http")

    async def probe(self, url: str, context: ports.ExtractionContext) -> VideoMeta:
        self.contexts.append(context)
        if self.probe_error is not None:
            raise self.probe_error
        video_id = url.rsplit("/", 1)[-1] or VIDEO_ID
        return VideoMeta(
            video_id=video_id,
            url=url,
            title=f"Vidéo {video_id[-4:]}",
            uploader="Alice Dupont",
            uploader_id="alice",
            duration=42.0,
            upload_date=date.today() - timedelta(days=3),
            view_count=120_000,
            like_count=9_000,
            thumbnail=f"https://example.invalid/{video_id}.jpg",
            source=self.name,
        )

    async def resolve_source(
        self,
        source: str,
        source_type: SourceType,
        context: ports.ExtractionContext,
        limit: int | None = None,
    ) -> ports.ResolvedSource:
        metas = list(self.playlist)
        if limit:
            metas = metas[:limit]
        urls = [meta.url for meta in metas]
        return ports.ResolvedSource(
            source_type=source_type, label=source, original=source, urls=urls, metas=metas
        )

    async def download(
        self,
        url: str,
        destination: Path,
        options: DownloadOptions,
        context: ports.ExtractionContext,
        progress: ports.ProgressCallback | None = None,
    ) -> ports.DownloadResult:
        self.contexts.append(context)
        self.attempts[url] = self.attempts.get(url, 0) + 1

        if self.delay:
            await asyncio.sleep(self.delay)
        if context.cancel is not None and context.cancel.is_set():
            from tikvault.domain.errors import DownloadCancelled

            msg = "annulé"
            raise DownloadCancelled(msg)
        if url in self.fail_urls:
            msg = f"ERROR: [TikTok] Video unavailable: {url}"
            raise RuntimeError(msg)
        if self.attempts[url] <= self.fail_times:
            msg = "HTTP Error 429: Too Many Requests"
            raise RuntimeError(msg)

        destination.parent.mkdir(parents=True, exist_ok=True)
        content = self.content_for.get(url, f"contenu-{url.rsplit('/', 1)[-1]}".encode())
        destination.write_bytes(content)

        if progress is not None:
            progress(
                ports.DownloadProgress(
                    bytes_done=len(content), bytes_total=len(content), speed_bps=1024
                )
            )
        self.downloaded.append(url)
        # On construit les métadonnées sans passer par probe() : une panne de résolution
        # ne doit pas empêcher le téléchargement lui-même.
        video_id = url.rsplit("/", 1)[-1] or "0"
        meta = VideoMeta(
            video_id=video_id,
            url=url,
            title=f"Vidéo {video_id[-4:]}",
            uploader="Alice Dupont",
            uploader_id="alice",
            duration=42.0,
            upload_date=date.today() - timedelta(days=3),
            view_count=120_000,
            thumbnail=f"https://example.invalid/{video_id}.jpg",
            source=self.name,
        )
        return ports.DownloadResult(
            file_path=destination, size=len(content), extractor=self.name, meta=meta
        )


class BrokenExtractor(ports.VideoExtractor):
    """Extracteur qui échoue toujours : sert à tester le repli sur un second moteur."""

    name = "broken"
    priority = 100

    def __init__(self, message: str = "Unsupported URL") -> None:
        self.message = message
        self.calls = 0

    def supports(self, url: str) -> bool:
        return True

    async def probe(self, url: str, context: ports.ExtractionContext) -> VideoMeta:
        self.calls += 1
        raise ExtractionError(self.message)

    async def resolve_source(
        self,
        source: str,
        source_type: SourceType,
        context: ports.ExtractionContext,
        limit: int | None = None,
    ) -> ports.ResolvedSource:
        raise ExtractionError(self.message)

    async def download(
        self,
        url: str,
        destination: Path,
        options: DownloadOptions,
        context: ports.ExtractionContext,
        progress: ports.ProgressCallback | None = None,
    ) -> ports.DownloadResult:
        self.calls += 1
        raise ExtractionError(self.message)


class RecordingNotifier:
    """Notifier factice qui mémorise les messages."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def notify(self, title: str, message: str, *, level: str = "info") -> None:
        self.messages.append((title, message))


#: Type renvoyé par la fixture ``queue`` (file, extracteur factice, notifieur).
QueueFixture = tuple[DownloadQueue, FakeExtractor, RecordingNotifier]


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def recorded_events(event_bus: EventBus) -> list[EngineEvent]:
    events: list[EngineEvent] = []
    event_bus.subscribe_all(events.append)
    return events


@pytest.fixture
async def database() -> AsyncIterator[Database]:
    db = Database(":memory:")
    await db.create_all()
    yield db
    await db.dispose()


@pytest.fixture
async def repository(database: Database) -> SqlTaskRepository:
    repo = SqlTaskRepository(database)
    await repo.init()
    return repo


@pytest.fixture
def options(tmp_path: Path) -> DownloadOptions:
    return DownloadOptions(output_dir=str(tmp_path / "sorties"), max_concurrency=2, max_attempts=3)


@pytest.fixture
def sample_task(options: DownloadOptions) -> DownloadTask:
    return DownloadTask(url=VIDEO_URL, max_attempts=3, options=options)


@pytest.fixture
def sample_source() -> ParsedSource:
    return ParsedSource(SourceType.VIDEO, VIDEO_URL, original=VIDEO_URL)


@pytest.fixture
def watched(options: DownloadOptions) -> WatchedAccount:
    return WatchedAccount(username="alice", interval_minutes=1, options=options)


@pytest.fixture
async def download_service(
    repository: SqlTaskRepository, event_bus: EventBus, tmp_path: Path
) -> tuple[DownloadService, FakeExtractor]:
    extractor = FakeExtractor()
    service = DownloadService(extractor=extractor, repository=repository, bus=event_bus)
    return service, extractor


@pytest.fixture
async def queue(
    repository: SqlTaskRepository,
    event_bus: EventBus,
    options: DownloadOptions,
) -> AsyncIterator[QueueFixture]:
    extractor = FakeExtractor()
    notifier = RecordingNotifier()
    resolver = SourceResolver(extractor=extractor, repository=repository, bus=event_bus)
    service = DownloadService(extractor=extractor, repository=repository, bus=event_bus)
    download_queue = DownloadQueue(
        repository=repository,
        resolver=resolver,
        download_service=service,
        bus=event_bus,
        notifier=notifier,
        stats_interval=0.05,
        output_dir=options.output_dir,
    )
    download_queue.set_concurrency(options.max_concurrency)
    await download_queue.start()
    yield download_queue, extractor, notifier
    await download_queue.shutdown(timeout=2.0)
