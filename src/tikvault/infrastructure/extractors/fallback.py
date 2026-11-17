"""Extracteur de secours : interrogation directe du site TikTok via HTTP (httpx).

Pourquoi un plan B ? TikTok modifie régulièrement ses pages et ses protections, et
yt-dlp met parfois quelques jours à suivre. Ce moteur vise **directement** le flux
``playAddr`` (celui lu par le lecteur web, donc *sans filigrane*) et sert aussi à
récupérer les miniatures.

Il est volontairement conservateur :
  * lecture seule, aucun contournement de protection ;
  * toute réponse inattendue remonte une erreur métier explicite ;
  * il n'est utilisé que si yt-dlp échoue (priorité plus basse).
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import structlog

from tikvault.domain.errors import (
    DeletedVideoError,
    ExtractionError,
    GeoBlockedError,
    LoginRequiredError,
    PrivateVideoError,
    RateLimitedError,
)
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

_OEMBED = "https://www.tiktok.com/oembed"
_ITEM_LIST_API = "https://www.tiktok.com/api/post/item_list/"
_REHYDRATION_ID = "__UNIVERSAL_DATA_FOR_REHYDRATION__"
_SCRIPT_RE = re.compile(
    r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', re.S
)
_SIGI_RE = re.compile(r'<script[^>]+id="SIGI_STATE"[^>]*>(.*?)</script>', re.S)
_CHUNK = 256 * 1024


def _from_unix(value: Any) -> date | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC).date()
    except (TypeError, ValueError, OSError):
        return None


class TikTokWebExtractor(VideoExtractor):
    """Moteur HTTP direct, utilisé en secours de yt-dlp."""

    name = "tiktok-web"
    priority = 40

    def __init__(self, *, client: httpx.AsyncClient | None = None, timeout: float = 25.0) -> None:
        self._client = client
        self._timeout = timeout

    # ------------------------------------------------------------------
    def supports(self, url: str) -> bool:
        return "tiktok.com" in url or "tiktokv.com" in url

    def close(self) -> None:
        if self._client is not None:  # pragma: no cover - fermé par le conteneur
            asyncio.get_running_loop().create_task(self._client.aclose())

    def _headers(self, context: ExtractionContext) -> dict[str, str]:
        headers = {
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.tiktok.com/",
            "User-Agent": context.user_agent
            or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        }
        return headers

    def _client_for(self, context: ExtractionContext) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(
            headers=self._headers(context),
            timeout=context.timeout or self._timeout,
            follow_redirects=True,
            proxy=context.proxy,
        )

    # ------------------------------------------------------------------
    # Port VideoExtractor
    # ------------------------------------------------------------------
    async def probe(self, url: str, context: ExtractionContext) -> VideoMeta:
        item = await self._fetch_item(url, context)
        if item is None:
            item = await self._oembed(url, context)
        if item is None:
            msg = f"Impossible de lire les informations de {url} (page inaccessible)."
            raise ExtractionError(msg)
        return self._to_meta(item, url)

    async def resolve_source(
        self,
        source: str,
        source_type: SourceType,
        context: ExtractionContext,
        limit: int | None = None,
    ) -> ResolvedSource:
        if source_type is SourceType.VIDEO:
            meta = await self.probe(source, context)
            return ResolvedSource(
                source_type=SourceType.VIDEO,
                label=meta.display_title,
                original=source,
                urls=[meta.url],
                metas=[meta],
            )
        if source_type is SourceType.PROFILE:
            username = source.lstrip("@").split("/")[-1]
            items = await self._fetch_profile_items(username, context, limit or 30)
            urls = [f"https://www.tiktok.com/@{username}/video/{item['id']}" for item in items]
            metas = [self._to_meta(item, url) for item, url in zip(items, urls, strict=False)]
            return ResolvedSource(
                source_type=SourceType.PROFILE,
                label=f"@{username}",
                original=source,
                urls=urls,
                metas=metas,
            )
        msg = (
            f"L'extracteur de secours ne sait pas développer une source de type "
            f"« {source_type.value} » ; laissez yt-dlp s'en charger."
        )
        raise ExtractionError(msg)

    async def download(
        self,
        url: str,
        destination: Path,
        options: DownloadOptions,
        context: ExtractionContext,
        progress: ProgressCallback | None = None,
    ) -> DownloadResult:
        item = await self._fetch_item(url, context)
        if item is None:
            msg = "Flux vidéo introuvable via l'API web."
            raise ExtractionError(msg)

        play_addr = self._play_addr(item)
        if not play_addr:
            msg = "Adresse de lecture absente (vidéo privée, supprimée ou géo-bloquée)."
            raise ExtractionError(msg)

        meta = self._to_meta(item, url)
        if options.audio_only:
            msg = (
                "L'extracteur de secours ne peut pas convertir en audio : "
                "installez ffmpeg et laissez yt-dlp effectuer la conversion."
            )
            raise ExtractionError(msg)

        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        written = await self._stream(play_addr, partial, context, progress)

        final = destination.with_suffix(f".{meta.ext or 'mp4'}")
        partial.replace(final)
        return DownloadResult(
            file_path=final,
            size=final.stat().st_size,
            extractor=self.name,
            meta=meta,
            resumed=written["resumed"],
        )

    # ------------------------------------------------------------------
    # Récupération des données
    # ------------------------------------------------------------------
    async def _fetch_item(self, url: str, context: ExtractionContext) -> dict[str, Any] | None:
        """Lit la page de la vidéo et en extrait l'objet ``itemStruct``."""
        owns_client = self._client is None
        client = self._client_for(context)
        try:
            response = await client.get(url, headers=self._headers(context))
        except httpx.HTTPError as exc:
            msg = f"Page TikTok injoignable : {exc}"
            raise ExtractionError(msg) from exc
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code == 429:
            msg = "TikTok a renvoyé 429 (trop de requêtes)."
            raise RateLimitedError(msg)
        if response.status_code >= 400:
            msg = f"TikTok a répondu {response.status_code} pour {url}"
            raise ExtractionError(msg)

        data = self._extract_json(response.text)
        if data is None:
            return None

        scope = data.get("__DEFAULT_SCOPE__", {})
        detail = scope.get("webapp.video-detail") or {}
        status = detail.get("statusCode")
        if status not in (None, 0):
            self._raise_for_status(status, detail.get("statusMsg", ""))

        item = (detail.get("itemInfo") or {}).get("itemStruct")
        if item is None and "ItemModule" in data:
            module = data["ItemModule"]
            item = next(iter(module.values()), None)
        return item

    async def _oembed(self, url: str, context: ExtractionContext) -> dict[str, Any] | None:
        """Repli léger : l'API oEmbed publique (titre, auteur, miniature)."""
        owns_client = self._client is None
        client = self._client_for(context)
        try:
            response = await client.get(_OEMBED, params={"url": url})
            if response.status_code != 200:
                return None
            payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            return None
        finally:
            if owns_client:
                await client.aclose()

        author_url = str(payload.get("author_url") or "")
        video_match = re.search(r"/video/(\d+)", url)
        return {
            "id": video_match.group(1) if video_match else "",
            "desc": payload.get("title", ""),
            "author": {
                "uniqueId": author_url.rstrip("/").split("/")[-1].lstrip("@"),
                "nickname": payload.get("author_name", ""),
            },
            "video": {"cover": payload.get("thumbnail_url", ""), "duration": 0},
            "stats": {},
        }

    async def _fetch_profile_items(
        self, username: str, context: ExtractionContext, limit: int
    ) -> list[dict[str, Any]]:
        """Parcourt l'API interne de liste des vidéos d'un profil."""
        sec_uid = await self._sec_uid(username, context)
        params = {
            "aid": "1988",
            "app_name": "tiktok_web",
            "device_platform": "web_pc",
            "channel": "tiktok_web",
            "cookie_enabled": "true",
            "count": "30",
            "cursor": "0",
            "secUid": sec_uid,
            "type": "1",
        }
        owns_client = self._client is None
        client = self._client_for(context)
        items: list[dict[str, Any]] = []
        try:
            while len(items) < limit:
                response = await client.get(
                    _ITEM_LIST_API, params=params, headers=self._headers(context)
                )
                if response.status_code == 429:
                    msg = "TikTok a renvoyé 429 pendant le parcours du profil."
                    raise RateLimitedError(msg)
                if response.status_code >= 400:
                    break
                try:
                    payload = response.json()
                except json.JSONDecodeError:
                    break
                batch = payload.get("itemList") or []
                items.extend(batch)
                if not payload.get("hasMore") or not batch:
                    break
                params["cursor"] = str(payload.get("cursor", int(params["cursor"]) + 30))
        except httpx.HTTPError as exc:
            msg = f"API de profil injoignable : {exc}"
            raise ExtractionError(msg) from exc
        finally:
            if owns_client:
                await client.aclose()

        return items[:limit]

    async def _sec_uid(self, username: str, context: ExtractionContext) -> str:
        """Récupère le ``secUid`` (identifiant opaque) d'un profil."""
        url = f"https://www.tiktok.com/@{username}"
        owns_client = self._client is None
        client = self._client_for(context)
        try:
            response = await client.get(url, headers=self._headers(context))
        except httpx.HTTPError as exc:
            msg = f"Profil injoignable : {exc}"
            raise ExtractionError(msg) from exc
        finally:
            if owns_client:
                await client.aclose()

        data = self._extract_json(response.text) or {}
        scope = data.get("__DEFAULT_SCOPE__", {})
        user = ((scope.get("webapp.user-detail") or {}).get("userInfo") or {}).get("user") or {}
        sec_uid = user.get("secUid")
        if not sec_uid and (match := re.search(r'"secUid"\s*:\s*"([^"]+)"', response.text)):
            sec_uid = match.group(1)
        if not sec_uid:
            msg = f"Impossible de trouver l'identifiant technique du profil @{username}."
            raise ExtractionError(msg)
        return str(sec_uid)

    async def _stream(
        self,
        play_addr: str,
        partial: Path,
        context: ExtractionContext,
        progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        """Télécharge le flux par blocs, avec reprise sur fichier partiel."""
        resumed_from = partial.stat().st_size if partial.exists() else 0
        headers = dict(self._headers(context))
        if resumed_from:
            headers["Range"] = f"bytes={resumed_from}-"

        mode = "ab" if resumed_from else "wb"
        written = resumed_from
        total = 0
        owns_client = self._client is None
        client = self._client_for(context)
        try:
            async with client.stream("GET", play_addr, headers=headers) as response:
                if response.status_code in (403, 404):
                    msg = f"Flux refusé par TikTok (code {response.status_code})."
                    raise GeoBlockedError(msg)
                if response.status_code == 429:
                    msg = "TikTok a renvoyé 429 pendant le téléchargement."
                    raise RateLimitedError(msg)
                response.raise_for_status()

                length = response.headers.get("Content-Length")
                if length:
                    total = resumed_from + int(length)

                started = asyncio.get_running_loop().time()
                with partial.open(mode) as handle:
                    async for chunk in response.aiter_bytes(_CHUNK):
                        if context.cancel is not None and context.cancel.is_set():
                            msg = "Téléchargement annulé par l'utilisateur."
                            from tikvault.domain.errors import DownloadCancelled

                            raise DownloadCancelled(msg)
                        handle.write(chunk)
                        written += len(chunk)
                        if progress is not None:
                            elapsed = max(0.001, asyncio.get_running_loop().time() - started)
                            speed = (written - resumed_from) / elapsed
                            remaining = max(0, total - written) if total else 0
                            progress(
                                DownloadProgress(
                                    bytes_done=written,
                                    bytes_total=total,
                                    speed_bps=speed,
                                    eta_seconds=remaining / speed if speed > 0 and total else None,
                                )
                            )
        except httpx.HTTPError as exc:
            msg = f"Connexion interrompue pendant le téléchargement : {exc}"
            raise ExtractionError(msg) from exc
        finally:
            if owns_client:
                await client.aclose()

        if written == 0:
            msg = "Le serveur a renvoyé un fichier vide."
            raise ExtractionError(msg)
        return {"resumed": resumed_from > 0}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_json(html: str) -> dict[str, Any] | None:
        for pattern in (_SCRIPT_RE, _SIGI_RE):
            if match := pattern.search(html):
                try:
                    parsed = json.loads(match.group(1))
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    return cast("dict[str, Any]", parsed)
        return None

    @staticmethod
    def _raise_for_status(status: int, message: str) -> None:
        """Traduit les codes d'erreur de l'API web TikTok."""
        lowered = message.lower()
        if status in (10202, 10203) or "private" in lowered:
            raise PrivateVideoError(message or None)
        if status in (10204, 10221) or "not found" in lowered or "removed" in lowered:
            raise DeletedVideoError(message or None)
        if "login" in lowered or "sign in" in lowered:
            raise LoginRequiredError(message or None)
        if "country" in lowered or "region" in lowered:
            raise GeoBlockedError(message or None)
        raise ExtractionError(f"TikTok a refusé la requête (code {status}) : {message}")

    @staticmethod
    def _play_addr(item: dict[str, Any]) -> str | None:
        video = item.get("video") or {}
        for key in ("playAddr", "play_addr", "downloadAddr"):
            value = video.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
            if isinstance(value, dict):
                urls = value.get("url_list") or []
                if urls:
                    return str(urls[0])
            if isinstance(value, list) and value:
                return str(value[0])
        return None

    def _to_meta(self, item: dict[str, Any], url: str) -> VideoMeta:
        author = item.get("author") or {}
        video = item.get("video") or {}
        stats = item.get("stats") or item.get("statsV2") or {}
        video_id = str(item.get("id") or item.get("aweme_id") or "")
        return VideoMeta(
            video_id=video_id,
            url=str(item.get("shareUrl") or url or f"https://www.tiktok.com/video/{video_id}"),
            title=str(item.get("desc") or ""),
            uploader=str(author.get("nickname") or author.get("uniqueId") or ""),
            uploader_id=str(author.get("uniqueId") or ""),
            duration=float(video["duration"]) if video.get("duration") else None,
            upload_date=_from_unix(item.get("createTime")),
            view_count=_int(stats.get("playCount")),
            like_count=_int(stats.get("diggCount")),
            thumbnail=video.get("cover") or video.get("originCover"),
            ext="mp4",
            source=self.name,
        )


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


__all__ = ["TikTokWebExtractor", "date"]
