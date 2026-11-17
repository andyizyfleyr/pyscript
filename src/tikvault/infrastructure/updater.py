"""Mise à jour automatique de yt-dlp depuis l'application.

TikTok change souvent : la majorité des pannes se répare en mettant yt-dlp à jour.
Deux chemins sont proposés :

* **installation uv/pip classique** — on relance l'installateur dans un sous-processus ;
* **exécutable PyInstaller** — le bundle est figé, on télécharge alors la dernière
  archive ``yt-dlp`` dans le dossier utilisateur et on l'injecte via ``PYTHONPATH``
  au prochain lancement (variable d'environnement gérée par ``tikvault.__main__``).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
import structlog

PYPI_JSON = "https://pypi.org/pypi/yt-dlp/json"
RELEASE_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt_dlp-*.whl"

logger = structlog.get_logger(__name__)


def current_version() -> str:
    """Version de yt-dlp actuellement importable."""
    try:
        import yt_dlp

        return str(getattr(yt_dlp.version, "__version__", "inconnue"))
    except Exception:
        return "inconnue"


def is_frozen() -> bool:
    """L'application tourne-t-elle depuis un exécutable PyInstaller ?"""
    return bool(getattr(sys, "frozen", False))


class YtDlpUpdater:
    """Vérifie et applique les mises à jour de yt-dlp (port ``Updater``)."""

    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._cache_dir = cache_dir or Path.home() / ".tikvault" / "updates"
        self._client = client

    async def current_version(self) -> str:
        return await asyncio.to_thread(current_version)

    async def check(self) -> str | None:
        """Renvoie la dernière version publiée sur PyPI, ou ``None`` si injoignable."""
        client = self._client or httpx.AsyncClient(timeout=15.0, follow_redirects=True)
        owns = self._client is None
        try:
            response = await client.get(PYPI_JSON)
            if response.status_code != 200:
                return None
            return str(response.json()["info"]["version"])
        except (httpx.HTTPError, KeyError, ValueError):
            return None
        finally:
            if owns:
                await client.aclose()

    async def apply(self) -> tuple[bool, str]:
        """Met yt-dlp à jour. Renvoie ``(succès, message)``."""
        latest = await self.check()
        if latest is None:
            return False, "Impossible de joindre PyPI : vérifiez votre connexion."
        if latest == current_version():
            return True, f"yt-dlp est déjà à jour ({latest})."

        if is_frozen():
            return await self._apply_frozen(latest)
        return await self._apply_pip(latest)

    # ------------------------------------------------------------------
    async def _apply_pip(self, latest: str) -> tuple[bool, str]:
        """Mise à jour via uv (si présent) sinon pip, dans l'interpréteur courant."""
        uv = shutil.which("uv")
        if uv is not None:
            command = [uv, "pip", "install", "--upgrade", f"yt-dlp=={latest}"]
        else:
            command = [sys.executable, "-m", "pip", "install", "--upgrade", f"yt-dlp=={latest}"]

        logger.info("mise_a_jour_ytdlp", commande=" ".join(command))
        try:
            completed = await asyncio.to_thread(
                subprocess.run, command, capture_output=True, text=True, timeout=600, check=False
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"La mise à jour a échoué : {exc}"

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip().splitlines()
            return False, f"Échec de la mise à jour : {detail[-1] if detail else 'raison inconnue'}"
        return True, f"yt-dlp {latest} installé. Redémarrez l'application pour l'utiliser."

    async def _apply_frozen(self, latest: str) -> tuple[bool, str]:
        """En mode exécutable : télécharge la roue et l'expose via ``PYTHONPATH``."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        target = self._cache_dir / "yt_dlp-latest"
        client = self._client or httpx.AsyncClient(timeout=60.0, follow_redirects=True)
        owns = self._client is None
        try:
            wheel_url = (
                "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt_dlp-"
                f"{latest}-py3-none-any.whl"
            )
            response = await client.get(wheel_url)
            if response.status_code != 200:
                return False, f"Téléchargement impossible (code {response.status_code})."
            wheel = self._cache_dir / f"yt_dlp-{latest}-py3-none-any.whl"
            wheel.write_bytes(response.content)
        except httpx.HTTPError as exc:
            return False, f"Téléchargement impossible : {exc}"
        finally:
            if owns:
                await client.aclose()

        # Décompactage de la roue (c'est une archive zip) dans un dossier dédié.
        import zipfile

        try:
            await asyncio.to_thread(_extract_wheel, wheel, target)
        except (OSError, zipfile.BadZipFile) as exc:
            return False, f"Extraction impossible : {exc}"

        existing = os.environ.get("TIKVAULT_YTDLP_PATH", "")
        os.environ["TIKVAULT_YTDLP_PATH"] = str(target)
        logger.info("ytdlp_portable_installe", chemin=str(target), precedent=existing)
        return True, (
            f"yt-dlp {latest} téléchargé. Redémarrez l'application : la nouvelle version "
            "sera chargée automatiquement."
        )


def _extract_wheel(wheel: Path, target: Path) -> None:
    """Décompresse une roue dans ``target`` (yt-dlp est un paquet Python pur)."""
    import zipfile

    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(target)
