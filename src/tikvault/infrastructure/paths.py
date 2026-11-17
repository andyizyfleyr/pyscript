"""Emplacements de fichiers portables (configuration, base, cache, téléchargements)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir, user_downloads_dir

APP_NAME = "TikVault"
APP_AUTHOR = "TikVault"


@dataclass(slots=True, frozen=True)
class AppPaths:
    """Chemins standards de l'application, créés à la demande."""

    config_dir: Path
    data_dir: Path
    cache_dir: Path
    downloads_dir: Path

    @property
    def database(self) -> Path:
        return self.data_dir / "tikvault.sqlite3"

    @property
    def log_file(self) -> Path:
        return self.data_dir / "logs" / "tikvault.log"

    @property
    def settings_file(self) -> Path:
        return self.config_dir / "settings.json"

    @property
    def thumbnails(self) -> Path:
        return self.cache_dir / "thumbnails"

    def ensure(self) -> AppPaths:
        """Crée l'arborescence (idempotent) et renvoie ``self``."""
        for path in (
            self.config_dir,
            self.data_dir,
            self.cache_dir,
            self.downloads_dir,
            self.thumbnails,
        ):
            path.mkdir(parents=True, exist_ok=True)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        return self


def default_paths(*, portable_dir: Path | None = None) -> AppPaths:
    """Chemins par défaut.

    :param portable_dir: s'il est fourni (clé USB, mode « portable »), tout est stocké dessous.
    """
    if portable_dir is not None:
        root = portable_dir.expanduser().resolve()
        return AppPaths(
            config_dir=root / "config",
            data_dir=root / "data",
            cache_dir=root / "cache",
            downloads_dir=root / "downloads",
        )

    return AppPaths(
        config_dir=Path(user_config_dir(APP_NAME, APP_AUTHOR)),
        data_dir=Path(user_data_dir(APP_NAME, APP_AUTHOR)),
        cache_dir=Path(user_data_dir(APP_NAME, APP_AUTHOR, roaming=False)) / "cache",
        downloads_dir=Path(user_downloads_dir()) / APP_NAME,
    )
