"""Gestion des cookies : import depuis un navigateur ou depuis un fichier « cookies.txt ».

Deux modes, exposés par le même port :class:`tikvault.domain.ports.CookieProvider` :

* **navigateur** — on délègue à yt-dlp (``cookiesfrombrowser``), qui sait déchiffrer
  les bases Chrome/Firefox/Edge sur Windows, macOS et Linux ;
* **fichier** — l'utilisateur fournit un export Netscape (extension « Get cookies.txt »
  du navigateur) ; on le valide puis on le copie dans le dossier de configuration.

Les cookies débloquent les vidéos « abonnés uniquement » et réduisent fortement les 429.
"""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path

import structlog

from tikvault.domain.errors import ConfigError
from tikvault.domain.ports import CookieProvider

logger = structlog.get_logger(__name__)

SUPPORTED_BROWSERS = (
    "chrome",
    "chromium",
    "brave",
    "edge",
    "opera",
    "vivaldi",
    "firefox",
    "safari",
)

_NETSCAPE_HEADER = "# Netscape HTTP Cookie File"
_INTERESTING = ("sessionid", "sid_tt", "sid_guard", "uid_tt", "tt-target-idc-sign")


class CookieStore(CookieProvider):
    """Implémentation du port ``CookieProvider`` (fichier **ou** navigateur)."""

    def __init__(
        self,
        *,
        cookie_file: Path | None = None,
        browser: str | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self._cookie_file = cookie_file
        self._browser = browser
        self._cache_dir = cache_dir or Path.home() / ".tikvault"

    # --- Port CookieProvider ---------------------------------------------
    async def export(self) -> Path | None:
        """Renvoie un fichier de cookies exploitable, ou ``None`` en mode navigateur."""
        if self._browser and not self._cookie_file:
            return None  # yt-dlp lira directement la base du navigateur
        if self._cookie_file is None:
            return None
        return self.install(self._cookie_file)

    def browser_spec(self) -> str | None:
        """Spécification ``navigateur[:profil]`` attendue par yt-dlp."""
        if self._cookie_file or not self._browser:
            return None
        browser = self._browser.split(":", 1)[0].strip().lower()
        if browser not in SUPPORTED_BROWSERS:
            msg = (
                f"Navigateur « {browser} » non pris en charge. "
                f"Choix possibles : {', '.join(SUPPORTED_BROWSERS)}."
            )
            raise ConfigError(msg)
        return self._browser

    # --- API spécifique ---------------------------------------------------
    def install(self, source: Path) -> Path:
        """Valide puis copie le fichier de cookies dans le dossier de configuration."""
        source = source.expanduser()
        if not source.exists():
            msg = f"Fichier de cookies introuvable : {source}"
            raise ConfigError(msg)

        content = source.read_text(encoding="utf-8-sig", errors="replace")
        if not content.lstrip().startswith(_NETSCAPE_HEADER):
            msg = (
                "Format de cookies non reconnu. Utilisez une extension d'export « Netscape » "
                "(le fichier doit commencer par « # Netscape HTTP Cookie File »)."
            )
            raise ConfigError(msg)

        entries = [line for line in content.splitlines() if line and not line.startswith("#")]
        if not entries:
            msg = "Le fichier de cookies est vide : reconnectez-vous à TikTok puis réexportez."
            raise ConfigError(msg)

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        target = self._cache_dir / "cookies.txt"
        shutil.copyfile(source, target)
        # Les cookies sont des identifiants de session : on restreint les droits.
        # Non bloquant si le système de fichiers ne gère pas chmod.
        with contextlib.suppress(OSError):
            target.chmod(0o600)

        logger.info("cookies_installes", entrees=len(entries), chemin=str(target))
        self._cookie_file = target
        return target

    def describe(self) -> dict[str, object]:
        """Résumé lisible pour la page Réglages (sans jamais afficher les valeurs)."""
        path = self._cookie_file
        if path is not None and path.exists():
            lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
            entries = [line for line in lines if line and not line.startswith("#")]
            names = {line.split("\t")[5] for line in entries if len(line.split("\t")) > 5}
            return {
                "mode": "fichier",
                "chemin": str(path),
                "cookies": len(entries),
                "session": bool(names & set(_INTERESTING)),
            }
        if self._browser:
            return {
                "mode": "navigateur",
                "navigateur": self._browser,
                "cookies": 0,
                "session": True,
            }
        return {"mode": "aucun", "cookies": 0, "session": False}

    def clear(self) -> None:
        self._cookie_file = None
        self._browser = None
        target = self._cache_dir / "cookies.txt"
        target.unlink(missing_ok=True)
