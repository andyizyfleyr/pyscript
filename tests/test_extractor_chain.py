"""Chaîne d'extracteurs : priorité, repli automatique sur le moteur suivant."""

from __future__ import annotations

from pathlib import Path

import pytest

from tikvault.domain.errors import ExtractionError
from tikvault.domain.models import DownloadOptions, SourceType, VideoMeta
from tikvault.domain.ports import DownloadResult, ExtractionContext
from tikvault.infrastructure.extractors.registry import ExtractorRegistry

from .conftest import VIDEO_URL, BrokenExtractor, FakeExtractor


@pytest.fixture
def context() -> ExtractionContext:
    return ExtractionContext()


class TestRegistre:
    def test_tri_par_priorite_decroissante(self) -> None:
        registry = ExtractorRegistry()
        faible = FakeExtractor()
        faible.priority = 10
        fort = FakeExtractor()
        fort.priority = 100
        registry.register(faible)
        registry.register(fort)
        assert registry.names() == ["fake", "fake"]
        assert registry.extractors[0] is fort

    def test_liste_vide(self) -> None:
        assert ExtractorRegistry().names() == []


class TestChaine:
    async def test_le_moteur_prioritaire_gagne(self, context: ExtractionContext) -> None:
        registry = ExtractorRegistry()
        primaire = FakeExtractor()
        primaire.priority = 100
        secours = FakeExtractor()
        secours.priority = 10
        registry.register(primaire)
        registry.register(secours)
        chain = registry.chain()

        meta = await chain.probe(VIDEO_URL, context)
        assert meta.video_id == VIDEO_URL.rsplit("/", 1)[-1]
        assert chain.last_used == "fake"
        assert primaire.contexts, "le moteur prioritaire doit être utilisé"

    async def test_repli_sur_le_moteur_suivant(
        self, context: ExtractionContext, tmp_path: Path
    ) -> None:
        """yt-dlp casse → l'extracteur de secours prend le relais sans intervention."""
        registry = ExtractorRegistry()
        casse = BrokenExtractor("Unsupported URL")
        secours = FakeExtractor()
        secours.priority = 40
        registry.register(casse)
        registry.register(secours)
        chain = registry.chain()

        meta = await chain.probe(VIDEO_URL, context)
        assert meta.video_id
        assert casse.calls == 1

        result = await chain.download(VIDEO_URL, tmp_path / "video.mp4", DownloadOptions(), context)
        assert isinstance(result, DownloadResult)
        assert result.file_path.exists()
        assert casse.calls == 2

    async def test_tous_les_moteurs_en_echec(
        self, context: ExtractionContext, tmp_path: Path
    ) -> None:
        registry = ExtractorRegistry()
        registry.register(BrokenExtractor("premier échec"))
        registry.register(BrokenExtractor("second échec"))
        chain = registry.chain()

        with pytest.raises(ExtractionError) as erreur:
            await chain.probe(VIDEO_URL, context)
        assert "premier échec" in str(erreur.value)
        assert "second échec" in str(erreur.value)

        with pytest.raises(ExtractionError):
            await chain.download(VIDEO_URL, tmp_path / "v.mp4", DownloadOptions(), context)

    async def test_resolve_source_avec_repli(self, context: ExtractionContext) -> None:
        registry = ExtractorRegistry()
        registry.register(BrokenExtractor())
        playlist = [
            VideoMeta(video_id="1", url="https://www.tiktok.com/@a/video/1"),
            VideoMeta(video_id="2", url="https://www.tiktok.com/@a/video/2"),
        ]
        registry.register(FakeExtractor(playlist=playlist))
        chain = registry.chain()

        resolved = await chain.resolve_source("@a", SourceType.PROFILE, context)
        assert len(resolved.urls) == 2
        assert len(resolved.metas) == 2

    async def test_resolve_source_vide_passe_au_suivant(self, context: ExtractionContext) -> None:
        registry = ExtractorRegistry()
        vide = FakeExtractor(playlist=[])
        vide.priority = 100
        plein = FakeExtractor(
            playlist=[VideoMeta(video_id="1", url="https://www.tiktok.com/@a/video/1")]
        )
        plein.priority = 10
        registry.register(vide)
        registry.register(plein)

        resolved = await registry.chain().resolve_source("@a", SourceType.PROFILE, context)
        assert len(resolved.urls) == 1

    def test_supports(self) -> None:
        registry = ExtractorRegistry()
        registry.register(FakeExtractor())
        assert registry.chain().supports("https://tiktok.com/x")
        assert not registry.chain().supports("ftp://exemple")


class TestPluginPersonnalise:
    async def test_ajout_d_un_nouveau_moteur(
        self, context: ExtractionContext, tmp_path: Path
    ) -> None:
        """Un tiers peut brancher son propre moteur sans toucher au reste du code."""

        class MoteurPerso(FakeExtractor):
            name = "perso"
            priority = 200

        registry = ExtractorRegistry()
        registry.register(FakeExtractor())
        registry.register(MoteurPerso())
        chain = registry.chain()

        result = await chain.download(VIDEO_URL, tmp_path / "v.mp4", DownloadOptions(), context)
        assert result.extractor == "perso"
