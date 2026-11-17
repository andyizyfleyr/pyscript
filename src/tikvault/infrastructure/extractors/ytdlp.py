"""Adaptateur yt-dlp, utilisé **comme bibliothèque** (jamais en sous-processus).

Points clés
-----------
* yt-dlp est synchrone : chaque appel passe par :func:`asyncio.to_thread` pour ne pas
  bloquer la boucle asyncio de l'application.
* La *progress hook* est rappelée depuis le thread de téléchargement ; on la relaie via
  :class:`tikvault.services.progress.ProgressRelay` (thread-safe + throttling).
* L'annulation est **coopérative** : la hook lève ``DownloadCancelled`` dès que
  l'événement d'annulation est positionné, ce qui interrompt proprement yt-dlp.
* Le fichier partiel (``.part``) est conservé : au redémarrage, ``continuedl`` reprend
  le téléchargement là où il s'était arrêté.

Note filigrane : pour TikTok, yt-dlp sélectionne le flux ``play_addr`` (sans logo).
L'URL ``download_addr``, elle, est filigranée — c'est précisément celle qu'utilisent les
sites « TikTok downloader ». L'extracteur de secours vise explicitement ``play_addr``.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

import structlog
import yt_dlp

from tikvault.domain.errors import DownloadCancelled, ExtractionError, FfmpegMissingError
from tikvault.domain.models import DownloadOptions, SourceType, VideoMeta
from tikvault.domain.ports import (
    DownloadProgress,
    DownloadResult,
    ExtractionContext,
    ProgressCallback,
    ResolvedSource,
    VideoExtractor,
)

logger = structlog.get_logger(__name__)

# Meilleure vidéo + meilleur audio, avec repli sur un flux progressif unique.
BEST_FORMAT = "bv*+ba/b"
#: Sans FFmpeg, impossible de fusionner vidéo + audio : on se limite aux flux complets.
BEST_FORMAT_NO_FFMPEG = "b"
AUDIO_ONLY_FORMAT = "ba/b"


def ffmpeg_available() -> bool:
    """FFmpeg est-il accessible dans le PATH ?

    Interrogé à chaque téléchargement (peu coûteux) plutôt que mis en cache :
    l'utilisateur peut installer FFmpeg pendant que l'application tourne.
    """
    return shutil.which("ffmpeg") is not None


#: Référentiel TikTok : certaines requêtes échouent sans en-tête de provenance.
BASE_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.tiktok.com/",
}


def _parse_upload_date(raw: str | None) -> date | None:
    if not raw or len(raw) < 8:
        return None
    try:
        return datetime.strptime(raw[:8], "%Y%m%d").date()
    except ValueError:
        return None


class YtDlpExtractor(VideoExtractor):
    """Moteur principal, basé sur yt-dlp."""

    name = "yt-dlp"
    priority = 100

    def __init__(self, *, version: str | None = None) -> None:
        self._version = version

    # ------------------------------------------------------------------
    @property
    def version(self) -> str:
        if self._version is None:
            self._version = str(getattr(yt_dlp.version, "__version__", "inconnue"))
        return self._version

    def supports(self, url: str) -> bool:
        return url.startswith(("http://", "https://"))

    def close(self) -> None:  # rien à libérer : un YoutubeDL par appel
        return None

    # ------------------------------------------------------------------
    # Construction des options yt-dlp
    # ------------------------------------------------------------------
    def _common_options(
        self, options: DownloadOptions, context: ExtractionContext
    ) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "restrictfilenames": False,
            "windowsfilenames": True,
            "continuedl": True,  # reprise du fichier partiel
            "retries": 2,
            "fragment_retries": 2,
            "socket_timeout": context.timeout,
            "noplaylist": True,
            "http_headers": dict(BASE_HEADERS),
        }
        if context.user_agent:
            opts["http_headers"]["User-Agent"] = context.user_agent
        if context.proxy:
            opts["proxy"] = context.proxy
        if context.cookies_file is not None:
            opts["cookiefile"] = str(context.cookies_file)
        elif context.cookies_from_browser:
            # ("chrome", profile, keyring, container) — yt-dlp accepte les parties vides.
            parts = [*context.cookies_from_browser.split(":"), "", "", ""][:4]
            opts["cookiesfrombrowser"] = (
                parts[0],
                parts[1] or None,
                parts[2] or None,
                parts[3] or None,
            )
        if options.rate_limit_kbps:
            opts["ratelimit"] = int(options.rate_limit_kbps * 1024)
        return opts

    def _download_options(
        self,
        options: DownloadOptions,
        context: ExtractionContext,
        destination: Path,
        progress: ProgressCallback | None,
        cancel: Any,
    ) -> dict[str, Any]:
        opts = self._common_options(options, context)
        destination.parent.mkdir(parents=True, exist_ok=True)

        # yt-dlp ajoute lui-même l'extension correspondant au conteneur final.
        opts["outtmpl"] = {"default": str(destination.with_suffix(""))}
        opts["paths"] = {"home": str(destination.parent)}
        opts["concurrent_fragment_downloads"] = 4

        # --- Post-traitements : tous exigent FFmpeg. S'il est absent, on dégrade
        # proprement plutôt que d'échouer : la vidéo est téléchargée telle quelle,
        # sans fusion de flux ni métadonnées/miniature intégrées.
        has_ffmpeg = ffmpeg_available()
        if options.audio_only and not has_ffmpeg:
            # Là, pas de plan B possible : la conversion MP3/M4A est le cœur de la demande.
            raise FfmpegMissingError()

        opts["format"] = (
            AUDIO_ONLY_FORMAT
            if options.audio_only
            else (BEST_FORMAT if has_ffmpeg else BEST_FORMAT_NO_FFMPEG)
        )

        postprocessors: list[dict[str, Any]] = []
        if options.audio_only:
            postprocessors.append(
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": options.audio_format,
                    "preferredquality": "192",
                }
            )
        if options.embed_metadata and has_ffmpeg:
            postprocessors.append({"key": "FFmpegMetadata", "add_metadata": True})
        if options.embed_thumbnail and not options.audio_only and has_ffmpeg:
            opts["writethumbnail"] = True
            postprocessors.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
        if not has_ffmpeg and (options.embed_metadata or options.embed_thumbnail):
            logger.warning(
                "ffmpeg_absent",
                message=(
                    "FFmpeg introuvable : téléchargement sans métadonnées ni miniature "
                    "intégrées. Installez FFmpeg pour un résultat complet."
                ),
            )
        opts["postprocessors"] = postprocessors

        if progress is not None or cancel is not None:
            opts["progress_hooks"] = [self._make_hook(progress, cancel)]
            opts["postprocessor_hooks"] = [self._make_pp_hook(progress, cancel)]
        return opts

    @staticmethod
    def _make_hook(progress: ProgressCallback | None, cancel: Any) -> Any:
        def hook(data: dict[str, Any]) -> None:
            if cancel is not None and cancel.is_set():
                # Levée depuis le thread yt-dlp : interrompt le téléchargement en cours.
                msg = "Téléchargement annulé par l'utilisateur."
                raise DownloadCancelled(msg)

            if progress is None:
                return
            status = data.get("status")
            if status == "downloading":
                total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
                progress(
                    DownloadProgress(
                        bytes_done=int(data.get("downloaded_bytes") or 0),
                        bytes_total=total,
                        speed_bps=float(data.get("speed") or 0.0),
                        eta_seconds=float(data["eta"]) if data.get("eta") is not None else None,
                        phase="téléchargement",
                    )
                )
            elif status == "finished":
                total = int(data.get("total_bytes") or data.get("downloaded_bytes") or 0)
                progress(
                    DownloadProgress(
                        bytes_done=total,
                        bytes_total=total,
                        speed_bps=0.0,
                        eta_seconds=None,
                        phase="finalisation",
                    )
                )

        return hook

    @staticmethod
    def _make_pp_hook(progress: ProgressCallback | None, cancel: Any) -> Any:
        def hook(data: dict[str, Any]) -> None:
            if cancel is not None and cancel.is_set():
                msg = "Téléchargement annulé par l'utilisateur."
                raise DownloadCancelled(msg)
            if progress is not None and data.get("status") == "started":
                name = str(data.get("postprocessor", "post-traitement"))
                progress(
                    DownloadProgress(bytes_done=0, bytes_total=0, phase=f"post-traitement ({name})")
                )

        return hook

    # ------------------------------------------------------------------
    # Conversion des métadonnées
    # ------------------------------------------------------------------
    def _to_meta(self, info: dict[str, Any], url: str = "") -> VideoMeta:
        return VideoMeta(
            video_id=str(info.get("id") or ""),
            url=str(info.get("webpage_url") or info.get("url") or url),
            title=str(info.get("title") or ""),
            uploader=str(info.get("uploader") or info.get("channel") or ""),
            uploader_id=str(info.get("uploader_id") or info.get("channel_id") or ""),
            duration=float(info["duration"]) if info.get("duration") else None,
            upload_date=_parse_upload_date(info.get("upload_date")),
            view_count=int(info["view_count"]) if info.get("view_count") is not None else None,
            like_count=int(info["like_count"]) if info.get("like_count") is not None else None,
            thumbnail=info.get("thumbnail"),
            ext=str(info.get("ext") or "mp4"),
            source=self.name,
        )

    # ------------------------------------------------------------------
    # Port VideoExtractor
    # ------------------------------------------------------------------
    async def probe(self, url: str, context: ExtractionContext) -> VideoMeta:
        opts = self._common_options(DownloadOptions(), context)
        opts.update({"skip_download": True, "extract_flat": False})
        info = await asyncio.to_thread(self._extract_info, url, opts, False)
        if not info:
            msg = f"yt-dlp n'a renvoyé aucune information pour {url}"
            raise ExtractionError(msg)
        if info.get("_type") == "playlist":
            entries = [e for e in (info.get("entries") or []) if e]
            if not entries:
                msg = f"Playlist vide : {url}"
                raise ExtractionError(msg)
            first: dict[str, Any] = entries[0]
            return self._to_meta(first, url)
        return self._to_meta(info, url)

    async def resolve_source(
        self,
        source: str,
        source_type: SourceType,
        context: ExtractionContext,
        limit: int | None = None,
    ) -> ResolvedSource:
        url = self.source_to_url(source, source_type)
        opts = self._common_options(DownloadOptions(), context)
        opts.update(
            {
                "extract_flat": "in_playlist",
                "noplaylist": False,
                "playlist_items": f"1:{limit}" if limit else None,
            }
        )
        opts = {key: value for key, value in opts.items() if value is not None}

        info = await asyncio.to_thread(self._extract_info, url, opts, False)
        if not info:
            msg = f"yt-dlp n'a renvoyé aucune information pour {source}"
            raise ExtractionError(msg)

        entries = list(info.get("entries") or [])
        if not entries and info.get("id"):
            entries = [info]  # source à une seule vidéo

        urls: list[str] = []
        metas: list[VideoMeta] = []
        for entry in entries:
            if not entry:
                continue
            entry_url = str(entry.get("url") or entry.get("webpage_url") or "")
            if not entry_url.startswith("http"):
                entry_id = str(entry.get("id") or "")
                if not entry_id:
                    continue
                entry_url = (
                    f"https://www.tiktok.com/@{entry.get('uploader_id') or 'user'}/video/{entry_id}"
                )
            meta = self._to_meta(entry, entry_url)
            if not meta.video_id:
                continue
            urls.append(entry_url)
            metas.append(meta)

        label = str(info.get("title") or source)
        return ResolvedSource(
            source_type=source_type, label=label, original=source, urls=urls, metas=metas
        )

    async def download(
        self,
        url: str,
        destination: Path,
        options: DownloadOptions,
        context: ExtractionContext,
        progress: ProgressCallback | None = None,
    ) -> DownloadResult:
        opts = self._download_options(options, context, destination, progress, context.cancel)
        try:
            info = await asyncio.to_thread(self._extract_info, url, opts, True)
        except DownloadCancelled:
            raise
        except yt_dlp.utils.DownloadError as exc:
            # On laisse la classification métier se faire en amont (DownloadService).
            raise RuntimeError(str(exc)) from exc

        produced = self._locate_file(info, destination, options)
        if produced is None or not produced.exists():
            msg = f"yt-dlp s'est terminé sans produire de fichier ({destination})"
            raise ExtractionError(msg)

        return DownloadResult(
            file_path=produced,
            size=produced.stat().st_size,
            extractor=self.name,
            meta=self._to_meta(info or {}, url) if info else None,
            resumed=bool(info and info.get("__x_forwarded_for_ip")),
            postprocessed=bool(opts.get("postprocessors")),
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _extract_info(url: str, opts: dict[str, Any], download: bool) -> dict[str, Any] | None:
        # yt-dlp n'est pas typé : on borne explicitement ce qui sort de la bibliothèque.
        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore[no-untyped-call]
            info = ydl.extract_info(url, download=download)
        if isinstance(info, list):  # très rare : plusieurs résultats
            info = info[0] if info else None
        if info is None:
            return None
        return cast("dict[str, Any]", info)

    @staticmethod
    def _locate_file(
        info: dict[str, Any] | None, destination: Path, options: DownloadOptions
    ) -> Path | None:
        """Retrouve le fichier réellement produit (l'extension peut changer après post-traitement)."""
        if info:
            for item in info.get("requested_downloads") or []:
                candidate = item.get("filepath") or item.get("_filename")
                if candidate and Path(candidate).exists():
                    return Path(candidate)

        expected_suffix = f".{options.final_extension}"
        base = destination.with_suffix("")
        voisins = sorted(base.parent.glob(f"{base.name}.*")) if base.parent.exists() else []
        for candidate in (base.with_suffix(expected_suffix), destination, *voisins):
            if candidate.exists() and candidate.is_file() and not candidate.name.endswith(".part"):
                return candidate
        return None

    @staticmethod
    def source_to_url(source: str, source_type: SourceType) -> str:
        """Convertit une source normalisée (@pseudo, #tag, URL) en URL TikTok."""
        if source.startswith(("http://", "https://")):
            return source
        match source_type:
            case SourceType.PROFILE:
                return f"https://www.tiktok.com/@{source.lstrip('@')}"
            case SourceType.HASHTAG:
                return f"https://www.tiktok.com/tag/{source.lstrip('#')}"
            case SourceType.SOUND:
                return f"https://www.tiktok.com/music/{source.lstrip('#')}"
            case _:
                return source
