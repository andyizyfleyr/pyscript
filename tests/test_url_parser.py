"""Analyse des sources : détection, nettoyage, dédoublonnage, import de fichiers."""

from __future__ import annotations

from pathlib import Path

import pytest

from tikvault.domain.models import SourceType
from tikvault.services.url_parser import SourceParser, canonical_video_id, classify, clean_url


@pytest.fixture
def parser() -> SourceParser:
    return SourceParser()


class TestCleanUrl:
    def test_supprime_les_parametres_de_suivi(self) -> None:
        url = "https://www.tiktok.com/@alice/video/7123?_r=1&_t=8xYz&is_from_webapp=1"
        assert clean_url(url) == "https://www.tiktok.com/@alice/video/7123"

    def test_conserve_les_parametres_utiles(self) -> None:
        url = "https://www.tiktok.com/@alice/video/7123?lang=fr&_r=1"
        assert clean_url(url) == "https://www.tiktok.com/@alice/video/7123?lang=fr"

    def test_retire_la_ponctuation_finale(self) -> None:
        assert clean_url("https://www.tiktok.com/@alice/video/7123.") == (
            "https://www.tiktok.com/@alice/video/7123"
        )

    def test_url_sans_parametre_inchangee(self) -> None:
        url = "https://vm.tiktok.com/ZM8xYz/"
        assert clean_url(url) == url


class TestClassify:
    @pytest.mark.parametrize(
        ("url", "attendu"),
        [
            ("https://www.tiktok.com/@alice/video/7123456789012345678", SourceType.VIDEO),
            ("https://vm.tiktok.com/ZM8xYz/video/7123456789012345678", SourceType.VIDEO),
            ("https://m.tiktok.com/v/7123456789012345678.html", SourceType.VIDEO),
            ("https://www.tiktok.com/@alice/photo/7123456789012345678", SourceType.VIDEO),
            ("https://www.tiktok.com/@alice", SourceType.PROFILE),
            ("https://www.tiktok.com/tag/danse", SourceType.HASHTAG),
            ("https://www.tiktok.com/music/son-1234567890", SourceType.SOUND),
            ("https://www.tiktok.com/@alice/playlist/7123456789012345678", SourceType.PLAYLIST),
            ("https://www.tiktok.com/@alice/collection/7123456789012345678", SourceType.PLAYLIST),
            ("https://exemple.invalid/autre", SourceType.UNKNOWN),
        ],
    )
    def test_qualification(self, url: str, attendu: SourceType) -> None:
        assert classify(url) is attendu

    def test_identifiant_depuis_url_courte(self) -> None:
        assert canonical_video_id("https://www.tiktok.com/@alice/video/7123456789012345678") == (
            "7123456789012345678"
        )

    def test_identifiant_brut(self) -> None:
        assert canonical_video_id("7123456789012345678") == "7123456789012345678"

    def test_aucun_identifiant(self) -> None:
        assert canonical_video_id("https://www.tiktok.com/@alice") is None


class TestParseText:
    def test_plusieurs_liens_sur_une_ligne(self, parser: SourceParser) -> None:
        texte = (
            "Regarde ça https://www.tiktok.com/@alice/video/7111111111111111111 "
            "et aussi https://www.tiktok.com/@bob/video/7222222222222222222 !"
        )
        sources = parser.parse_text(texte)
        assert len(sources) == 2
        assert all(source.kind is SourceType.VIDEO for source in sources)

    def test_dedoublonnage(self, parser: SourceParser) -> None:
        texte = "\n".join(["https://www.tiktok.com/@alice/video/7111111111111111111"] * 3)
        assert len(parser.parse_text(texte)) == 1

    def test_dedoublonnage_insensible_a_la_casse(self, parser: SourceParser) -> None:
        texte = (
            "https://www.tiktok.com/@Alice/video/7111111111111111111\n"
            "https://www.tiktok.com/@alice/video/7111111111111111111"
        )
        assert len(parser.parse_text(texte)) == 1

    def test_ordre_de_saisie_conserve(self, parser: SourceParser) -> None:
        texte = (
            "https://www.tiktok.com/@a/video/7111111111111111111\n"
            "https://www.tiktok.com/@b/video/7222222222222222222"
        )
        urls = [source.value for source in parser.parse_text(texte)]
        assert urls[0].endswith("7111111111111111111")
        assert urls[1].endswith("7222222222222222222")

    def test_profil_et_hashtag_nus(self, parser: SourceParser) -> None:
        sources = parser.parse_text("@alice et #danse")
        kinds = {source.kind for source in sources}
        assert kinds == {SourceType.PROFILE, SourceType.HASHTAG}
        assert "@alice" in [source.value for source in sources]

    def test_ignore_les_hashtags_de_bruit(self, parser: SourceParser) -> None:
        assert parser.parse_text("#fyp #foryou #viral") == []

    def test_ligne_de_commentaire_ignoree(self, parser: SourceParser) -> None:
        texte = "# ma liste de vidéos\nhttps://www.tiktok.com/@alice/video/7111111111111111111"
        assert len(parser.parse_text(texte)) == 1

    def test_identifiant_de_video_isole(self, parser: SourceParser) -> None:
        sources = parser.parse_text("7111111111111111111")
        assert len(sources) == 1
        assert sources[0].value.endswith("7111111111111111111")

    def test_ignore_les_urls_etrangeres(self, parser: SourceParser) -> None:
        assert parser.parse_text("https://youtube.com/watch?v=abc") == []

    def test_texte_vide(self, parser: SourceParser) -> None:
        assert parser.parse_text("") == []


class TestParseFile:
    def test_import_txt(self, parser: SourceParser, tmp_path: Path) -> None:
        fichier = tmp_path / "liens.txt"
        fichier.write_text(
            "# liste\n"
            "https://www.tiktok.com/@alice/video/7111111111111111111\n"
            "\n"
            "https://www.tiktok.com/@bob/video/7222222222222222222\n",
            encoding="utf-8",
        )
        assert len(parser.parse_file(fichier)) == 2

    def test_import_csv_avec_colonne_url(self, parser: SourceParser, tmp_path: Path) -> None:
        fichier = tmp_path / "liens.csv"
        fichier.write_text(
            "titre;url\n"
            "première;https://www.tiktok.com/@alice/video/7111111111111111111\n"
            "seconde;https://www.tiktok.com/@bob/video/7222222222222222222\n",
            encoding="utf-8",
        )
        sources = parser.parse_file(fichier)
        assert len(sources) == 2
        assert all(source.kind is SourceType.VIDEO for source in sources)

    def test_import_csv_sans_en_tete(self, parser: SourceParser, tmp_path: Path) -> None:
        fichier = tmp_path / "brut.csv"
        fichier.write_text(
            "https://www.tiktok.com/@alice/video/7111111111111111111,note\n", encoding="utf-8"
        )
        assert len(parser.parse_file(fichier)) == 1

    def test_csv_vide(self, parser: SourceParser, tmp_path: Path) -> None:
        fichier = tmp_path / "vide.csv"
        fichier.write_text("", encoding="utf-8")
        assert parser.parse_file(fichier) == []
