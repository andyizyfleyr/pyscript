"""Configuration applicative (Pydantic v2 / pydantic-settings).

Trois sources, par priorité décroissante :
  1. variables d'environnement préfixées ``TIKVAULT_`` (ex. ``TIKVAULT_MAX_CONCURRENCY=6``) ;
  2. le fichier ``settings.json`` du dossier de configuration ;
  3. les valeurs par défaut ci-dessous.

Les objets imbriqués utilisent le délimiteur ``__`` :
``TIKVAULT_FILTERS__MIN_VIEWS=10000``.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from tikvault.domain.models import DownloadOptions, FilterOptions
from tikvault.infrastructure.paths import AppPaths, default_paths


class ThemeMode(StrEnum):
    DARK = "dark"
    LIGHT = "light"
    SYSTEM = "system"


class AppSettings(BaseSettings):
    """Réglages persistés de l'application."""

    model_config = SettingsConfigDict(
        env_prefix="TIKVAULT_",
        env_nested_delimiter="__",
        extra="ignore",
        validate_assignment=True,
    )

    # --- Téléchargement ---------------------------------------------------
    output_dir: str = "~/TikVault"
    filename_template: str = "{username}/{date}_{id}_{title}.mp4"
    best_quality: bool = True
    no_watermark: bool = True
    audio_only: bool = False
    audio_format: str = "mp3"
    embed_thumbnail: bool = True
    embed_metadata: bool = True
    max_concurrency: int = Field(default=3, ge=1, le=10)
    rate_limit_kbps: int | None = Field(default=None, ge=1)
    max_attempts: int = Field(default=4, ge=1, le=10)
    socket_timeout: float = Field(default=30.0, gt=0)
    filters: FilterOptions = Field(default_factory=FilterOptions)

    # --- Robustesse -------------------------------------------------------
    # ``NoDecode`` : la variable d'environnement est lue telle quelle (une entrée par
    # ligne) au lieu d'être interprétée comme du JSON.
    proxies: Annotated[list[str], NoDecode] = Field(default_factory=list)
    rotate_proxies: bool = True
    rotate_user_agents: bool = True
    cookies_file: str | None = None
    cookies_browser: str | None = None

    # --- Interface --------------------------------------------------------
    theme: ThemeMode = ThemeMode.DARK
    accent_color: str = "#ff2e63"  # rouge TikTok
    notify_on_finish: bool = True
    start_minimized: bool = False
    confirm_on_close: bool = True

    # --- Maintenance ------------------------------------------------------
    auto_update_ytdlp: bool = True
    watch_tick_seconds: float = Field(default=30.0, ge=5)
    log_level: str = "INFO"

    @field_validator("proxies", mode="before")
    @classmethod
    def _split_proxies(cls, value: Any) -> Any:
        """Accepte une liste ou une chaîne « un proxy par ligne » (zone de texte de l'UI)."""
        if isinstance(value, str):
            return [line.strip() for line in value.splitlines() if line.strip()]
        return value

    @field_validator("filename_template")
    @classmethod
    def _check_template(cls, value: str) -> str:
        from tikvault.services.filename import validate_template

        validate_template(value)
        return value

    @field_validator("audio_format")
    @classmethod
    def _check_audio_format(cls, value: str) -> str:
        allowed = {"mp3", "m4a", "opus", "flac", "wav", "aac"}
        if value.lower() not in allowed:
            msg = f"Format audio {value!r} non pris en charge (attendu : {sorted(allowed)})"
            raise ValueError(msg)
        return value.lower()

    # ------------------------------------------------------------------
    def download_options(self) -> DownloadOptions:
        """Projette les réglages sur les options de téléchargement d'un nouveau lot."""
        return DownloadOptions(
            output_dir=self.output_dir,
            filename_template=self.filename_template,
            best_quality=self.best_quality,
            no_watermark=self.no_watermark,
            audio_only=self.audio_only,
            audio_format=self.audio_format,
            embed_thumbnail=self.embed_thumbnail,
            embed_metadata=self.embed_metadata,
            max_concurrency=self.max_concurrency,
            rate_limit_kbps=self.rate_limit_kbps,
            max_attempts=self.max_attempts,
            socket_timeout=self.socket_timeout,
            rotate_proxies=self.rotate_proxies,
            rotate_user_agents=self.rotate_user_agents,
            filters=self.filters.model_copy(deep=True),
        )

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, paths: AppPaths | None = None) -> AppSettings:
        """Charge depuis le fichier de configuration (s'il existe)."""
        paths = paths or default_paths()
        file_path = paths.settings_file
        if file_path.exists():
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
                return cls.model_validate(payload)
            except (OSError, ValueError, json.JSONDecodeError):
                # Configuration illisible : on repart des valeurs par défaut sans planter.
                return cls()
        return cls()

    def save(self, paths: AppPaths | None = None) -> Path:
        paths = paths or default_paths()
        paths.config_dir.mkdir(parents=True, exist_ok=True)
        target = paths.settings_file
        target.write_text(
            json.dumps(self.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return target
