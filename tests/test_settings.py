"""Réglages : chargement, sauvegarde, variables d'environnement, projection en options."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tikvault.domain.errors import ConfigError
from tikvault.infrastructure.paths import AppPaths, default_paths
from tikvault.settings import AppSettings, ThemeMode


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    return AppPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        downloads_dir=tmp_path / "sorties",
    ).ensure()


class TestDefauts:
    def test_valeurs_par_defaut(self) -> None:
        settings = AppSettings()
        assert settings.max_concurrency == 3
        assert settings.filename_template == "{username}/{date}_{id}_{title}.mp4"
        assert settings.theme is ThemeMode.DARK
        assert settings.audio_format == "mp3"

    def test_projection_en_options_de_telechargement(self) -> None:
        settings = AppSettings(max_concurrency=8, audio_only=True, audio_format="m4a")
        options = settings.download_options()
        assert options.max_concurrency == 8
        assert options.audio_only is True
        assert options.audio_format == "m4a"
        assert options.final_extension == "m4a"

    def test_options_isolees_des_reglages(self) -> None:
        """Changer les réglages plus tard ne doit pas modifier un lot déjà en file."""
        settings = AppSettings()
        options = settings.download_options()
        settings.max_concurrency = 9
        assert options.max_concurrency == 3


class TestPersistance:
    def test_sauvegarde_et_rechargement(self, paths: AppPaths) -> None:
        settings = AppSettings(max_concurrency=6, proxies=["http://a:1"], theme=ThemeMode.LIGHT)
        chemin = settings.save(paths)
        assert chemin.exists()

        reloaded = AppSettings.load(paths)
        assert reloaded.max_concurrency == 6
        assert reloaded.proxies == ["http://a:1"]
        assert reloaded.theme is ThemeMode.LIGHT

    def test_fichier_absent(self, paths: AppPaths) -> None:
        assert AppSettings.load(paths).max_concurrency == AppSettings().max_concurrency

    def test_fichier_corrompu(self, paths: AppPaths) -> None:
        paths.settings_file.write_text("{ pas du json", encoding="utf-8")
        assert AppSettings.load(paths).max_concurrency == AppSettings().max_concurrency

    def test_json_lisible_et_sans_echappement_unicode(self, paths: AppPaths) -> None:
        AppSettings(filename_template="{username}/{title}.mp4").save(paths)
        contenu = json.loads(paths.settings_file.read_text(encoding="utf-8"))
        assert contenu["filename_template"] == "{username}/{title}.mp4"


class TestEnvironnement:
    def test_variable_d_environnement(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TIKVAULT_MAX_CONCURRENCY", "7")
        monkeypatch.setenv("TIKVAULT_THEME", "light")
        settings = AppSettings()
        assert settings.max_concurrency == 7
        assert settings.theme is ThemeMode.LIGHT

    def test_filtre_imbrique(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TIKVAULT_FILTERS__MIN_VIEWS", "50000")
        assert AppSettings().filters.min_views == 50_000

    def test_proxies_en_chaine_multiligne(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TIKVAULT_PROXIES", "http://a:1\nhttp://b:2")
        assert AppSettings().proxies == ["http://a:1", "http://b:2"]


class TestValidation:
    @pytest.mark.parametrize("valeur", [0, 11, -3])
    def test_concurrence_bornee(self, valeur: int) -> None:
        with pytest.raises(ValidationError):
            AppSettings(max_concurrency=valeur)

    @pytest.mark.parametrize("format_audio", ["ogg", "wma", ""])
    def test_format_audio_invalide(self, format_audio: str) -> None:
        with pytest.raises(ValidationError):
            AppSettings(audio_format=format_audio)

    def test_modele_de_nom_invalide(self) -> None:
        with pytest.raises(ConfigError):
            AppSettings(filename_template="sans-jeton.mp4")

    def test_modele_de_nom_valide(self) -> None:
        assert AppSettings(filename_template="{id}.{ext}").filename_template == "{id}.{ext}"


class TestChemins:
    def test_chemins_par_defaut(self) -> None:
        chemins = default_paths()
        assert chemins.database.name == "tikvault.sqlite3"
        assert chemins.thumbnails.name == "thumbnails"

    def test_mode_portable(self, tmp_path: Path) -> None:
        chemins = default_paths(portable_dir=tmp_path)
        assert chemins.config_dir == tmp_path / "config"
        assert chemins.downloads_dir == tmp_path / "downloads"

    def test_ensure_cree_l_arborescence(self, tmp_path: Path) -> None:
        chemins = default_paths(portable_dir=tmp_path).ensure()
        assert chemins.config_dir.is_dir()
        assert chemins.thumbnails.is_dir()
        assert chemins.log_file.parent.is_dir()
