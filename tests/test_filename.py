"""Modèle de nom de fichier : rendu, sécurité, unicité."""

from __future__ import annotations

from datetime import date
from pathlib import Path, PurePosixPath

import pytest

from tikvault.domain.errors import ConfigError
from tikvault.domain.models import DownloadOptions, VideoMeta
from tikvault.services.filename import (
    render,
    resolve_destination,
    sanitize_component,
    validate_template,
    with_unique_suffix,
)


@pytest.fixture
def meta() -> VideoMeta:
    return VideoMeta(
        video_id="7111111111111111111",
        url="https://www.tiktok.com/@alice/video/7111111111111111111",
        title="Ma super vidéo / été 2024 ?",
        uploader="Alice Dupont",
        uploader_id="alice",
        duration=42.0,
        upload_date=date(2024, 7, 15),
        view_count=12345,
        source="fake",
    )


class TestRender:
    def test_modele_par_defaut(self, meta: VideoMeta) -> None:
        chemin = render("{username}/{date}_{id}_{title}.mp4", meta)
        assert chemin == PurePosixPath(
            "alice/2024-07-15_7111111111111111111_Ma super vidéo été 2024.mp4"
        )

    def test_tous_les_jetons(self, meta: VideoMeta) -> None:
        chemin = render("{year}-{index}-{duration}-{views}-{source}.{ext}", meta)
        assert str(chemin) == "2024-0001-042-12345-fake.mp4"

    def test_extension_audio(self, meta: VideoMeta) -> None:
        options = DownloadOptions(audio_only=True, audio_format="m4a")
        chemin = render("{id}.{ext}", meta, options=options)
        assert str(chemin) == "7111111111111111111.m4a"

    def test_sans_metadonnees(self) -> None:
        chemin = render("{username}/{id}.mp4", None, url="https://www.tiktok.com/@x/video/7999")
        assert str(chemin) == "inconnu/7999.mp4"

    def test_caracteres_interdits_nettoyes(self) -> None:
        meta = VideoMeta(
            video_id="1",
            url="u",
            title='a<b>c:d"e|f?g*h',
            uploader_id="x/y\\z",
        )
        chemin = render("{username}/{title}.mp4", meta)
        assert "<" not in str(chemin)
        assert ":" not in str(chemin)
        assert "|" not in str(chemin)

    def test_tentative_evasion_refusee(self, meta: VideoMeta) -> None:
        with pytest.raises(ConfigError):
            validate_template("../../etc/{id}.mp4")

    def test_titre_tres_long_tronque(self) -> None:
        meta = VideoMeta(video_id="1", url="u", title="x" * 500, uploader_id="alice")
        chemin = render("{title}.mp4", meta)
        assert len(chemin.name) <= 130

    def test_jeton_inconnu_refuse(self) -> None:
        with pytest.raises(ConfigError, match="Jetons inconnus"):
            validate_template("{inexistant}.mp4")

    def test_modele_vide_refuse(self) -> None:
        with pytest.raises(ConfigError):
            validate_template("   ")

    def test_modele_sans_jeton_refuse(self) -> None:
        with pytest.raises(ConfigError):
            validate_template("video.mp4")

    def test_accolades_dans_le_titre_ne_cassent_rien(self) -> None:
        meta = VideoMeta(video_id="1", url="u", title="titre {bizarre}", uploader_id="alice")
        assert "titre" in render("{title}.mp4", meta).name


class TestSanitize:
    @pytest.mark.parametrize(
        ("entree", "attendu"),
        [
            ("normal", "normal"),
            ("  espaces   multiples ", "espaces multiples"),
            ("points...", "points"),
            ("CON", "_CON"),
            ("", "sans-titre"),
            ("...", "sans-titre"),
        ],
    )
    def test_nettoyage(self, entree: str, attendu: str) -> None:
        assert sanitize_component(entree) == attendu


class TestResolveDestination:
    def test_chemin_sous_la_racine(self, tmp_path: Path) -> None:
        destination = resolve_destination(tmp_path, PurePosixPath("alice/video.mp4"))
        assert destination == tmp_path / "alice" / "video.mp4"

    def test_evasion_refusee(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError):
            resolve_destination(tmp_path, PurePosixPath("../echappe.mp4"))


class TestUniqueSuffix:
    def test_ajoute_un_suffixe_si_existant(self, tmp_path: Path) -> None:
        fichier = tmp_path / "video.mp4"
        fichier.write_text("x")
        assert with_unique_suffix(fichier).name == "video (1).mp4"

    def test_inchange_si_libre(self, tmp_path: Path) -> None:
        fichier = tmp_path / "video.mp4"
        assert with_unique_suffix(fichier) == fichier
