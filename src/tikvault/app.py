"""Racine de composition : construction et câblage de tous les composants.

C'est le **seul** endroit de l'application où les implémentations concrètes sont
instanciées. L'UI et la CLI reçoivent un :class:`Container` déjà câblé et ne
connaissent que les ports du domaine — d'où la possibilité de tester le moteur
avec des bouchons, sans Qt ni réseau.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

from tikvault.domain.events import EventBus, LogEvent, UpdateAvailable
from tikvault.domain.ports import Notifier
from tikvault.infrastructure.cookies import CookieStore
from tikvault.infrastructure.db.engine import Database
from tikvault.infrastructure.db.repository import SqlTaskRepository
from tikvault.infrastructure.extractors.fallback import TikTokWebExtractor
from tikvault.infrastructure.extractors.registry import ChainExtractor, ExtractorRegistry
from tikvault.infrastructure.extractors.ytdlp import YtDlpExtractor
from tikvault.infrastructure.notifier import NullNotifier
from tikvault.infrastructure.paths import AppPaths, default_paths
from tikvault.infrastructure.proxy_pool import ProxyPool, parse_proxy_list
from tikvault.infrastructure.thumbnails import ThumbnailCache
from tikvault.infrastructure.updater import YtDlpUpdater, current_version
from tikvault.infrastructure.user_agents import RotatingUserAgents
from tikvault.services.dedupe import Blake2Hasher, DedupeService
from tikvault.services.download_service import DownloadService
from tikvault.services.queue_service import DownloadQueue
from tikvault.services.report_service import ReportService
from tikvault.services.source_resolver import SourceResolver
from tikvault.services.url_parser import SourceParser
from tikvault.services.watch_service import AccountWatcher
from tikvault.settings import AppSettings

logger = structlog.get_logger(__name__)


class Container:
    """Conteneur d'injection de dépendances de TikVault."""

    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
        paths: AppPaths | None = None,
        notifier: Notifier | None = None,
        database: Database | None = None,
        registry: ExtractorRegistry | None = None,
    ) -> None:
        self.paths = paths or default_paths().ensure()
        self.settings = settings or AppSettings.load(self.paths)
        self.bus = EventBus()
        self.notifier: Notifier = notifier or NullNotifier()

        # --- Infrastructure -------------------------------------------------
        self.database = database or Database(self.paths.database)
        self.repository = SqlTaskRepository(self.database)
        self.user_agents = RotatingUserAgents()
        self.proxies = ProxyPool(urls=parse_proxy_list(self.settings.proxies))
        self.cookies = CookieStore(
            cookie_file=Path(self.settings.cookies_file) if self.settings.cookies_file else None,
            browser=self.settings.cookies_browser,
            cache_dir=self.paths.config_dir,
        )
        self.thumbnails = ThumbnailCache(self.paths.thumbnails)
        self.updater = YtDlpUpdater(cache_dir=self.paths.cache_dir / "updates")

        # --- Moteurs de téléchargement (plugins) ----------------------------
        self.registry = registry or ExtractorRegistry()
        if registry is None:
            self.registry.register(YtDlpExtractor())
            self.registry.register(TikTokWebExtractor())
        self.extractor: ChainExtractor = self.registry.chain()

        # --- Services -------------------------------------------------------
        self.parser = SourceParser()
        self.dedupe = DedupeService(self.repository, Blake2Hasher())
        self.download_service = DownloadService(
            extractor=self.extractor,
            repository=self.repository,
            bus=self.bus,
            proxies=self.proxies,
            user_agents=self.user_agents,
            cookies=self.cookies,
            dedupe=self.dedupe,
        )
        self.resolver = SourceResolver(
            extractor=self.extractor,
            repository=self.repository,
            bus=self.bus,
            user_agents=self.user_agents,
            cookies=self.cookies,
        )
        self.queue = DownloadQueue(
            repository=self.repository,
            resolver=self.resolver,
            download_service=self.download_service,
            bus=self.bus,
            notifier=self.notifier if self.settings.notify_on_finish else None,
            output_dir=self.settings.output_dir,
        )
        self.queue.set_concurrency(self.settings.max_concurrency)
        self.watcher = AccountWatcher(
            repository=self.repository,
            extractor=self.extractor,
            queue=self.queue,
            bus=self.bus,
            tick_seconds=self.settings.watch_tick_seconds,
        )
        self.reports = ReportService(self.repository)

        self._started = False

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Initialise la base, démarre la file et la veille."""
        if self._started:
            return
        await self.repository.init()
        await self.queue.start()
        await self.watcher.start()
        self._started = True
        logger.info(
            "application_demarree",
            ytdlp=current_version(),
            extracteurs=self.registry.names(),
            proxies=len(self.proxies),
        )
        await self.bus.publish(
            LogEvent(
                level="info",
                message=(
                    f"TikVault démarré — moteur yt-dlp {current_version()}, "
                    f"{len(self.registry.names())} extracteur(s), {len(self.proxies)} proxy(s)."
                ),
            )
        )

    async def stop(self) -> None:
        """Arrêt propre : veille, file, cache, base."""
        if not self._started:
            return
        await self.watcher.stop()
        await self.queue.shutdown()
        await self.thumbnails.aclose()
        await self.repository.close()
        self._started = False
        self.bus.clear()
        logger.info("application_arretee")

    async def __aenter__(self) -> Container:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # Réglages
    # ------------------------------------------------------------------
    def apply_settings(self, settings: AppSettings) -> None:
        """Applique de nouveaux réglages à chaud et les persiste."""
        self.settings = settings
        settings.save(self.paths)

        # Concurrence et dossier de destination : effet immédiat.
        self.queue.set_concurrency(settings.max_concurrency)
        self.queue.output_dir = settings.output_dir

        # Proxies : reconstruction du pool.
        self.proxies = ProxyPool(urls=parse_proxy_list(settings.proxies))

        # Cookies : nouveau fournisseur.
        self.cookies = CookieStore(
            cookie_file=Path(settings.cookies_file) if settings.cookies_file else None,
            browser=settings.cookies_browser,
            cache_dir=self.paths.config_dir,
        )

        # On re-câble le service de téléchargement sur les nouveaux fournisseurs.
        self.download_service.configure(
            proxies=self.proxies, user_agents=self.user_agents, cookies=self.cookies
        )

        logger.info(
            "reglages_appliques", concurrence=settings.max_concurrency, theme=settings.theme.value
        )

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    async def check_updates(self) -> str | None:
        """Vérifie la version de yt-dlp et publie un événement si une mise à jour existe."""
        latest = await self.updater.check()
        current = current_version()
        if latest and latest != current:
            await self.bus.publish(
                UpdateAvailable(current_version=current, latest_version=latest, component="yt-dlp")
            )
            return latest
        return None

    async def update_engine(self) -> tuple[bool, str]:
        """Déclenche la mise à jour de yt-dlp."""
        return await self.updater.apply()

    async def run_until_idle(self, timeout: float | None = None) -> None:
        """Attend la fin du lot en cours (mode ligne de commande)."""
        await self.queue.wait_idle(timeout=timeout)


async def wait_forever() -> None:
    """Bloque jusqu'à annulation (utilisé par la CLI ``--headless --wait``)."""
    await asyncio.Event().wait()
