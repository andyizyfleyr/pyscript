"""Empreinte de contenu et services disque."""

from __future__ import annotations

from pathlib import Path

import pytest

from tikvault.domain.errors import DiskFullError
from tikvault.services.dedupe import Blake2Hasher
from tikvault.services.disk import (
    directory_size,
    ensure_writable,
    free_bytes,
    human_duration,
    human_size,
)


class TestBlake2Hasher:
    async def test_empreinte_stable(self, tmp_path: Path) -> None:
        fichier = tmp_path / "video.mp4"
        fichier.write_bytes(b"contenu identique")
        hasher = Blake2Hasher()
        assert await hasher.digest(fichier) == await hasher.digest(fichier)

    async def test_contenus_differents(self, tmp_path: Path) -> None:
        a = tmp_path / "a.mp4"
        b = tmp_path / "b.mp4"
        a.write_bytes(b"un")
        b.write_bytes(b"deux")
        hasher = Blake2Hasher()
        assert await hasher.digest(a) != await hasher.digest(b)

    async def test_fichier_volumineux(self, tmp_path: Path) -> None:
        """Le calcul se fait par blocs : un gros fichier ne sature pas la mémoire."""
        fichier = tmp_path / "gros.mp4"
        fichier.write_bytes(b"x" * (3 * 1024 * 1024))
        empreinte = await Blake2Hasher().digest(fichier)
        assert len(empreinte) == 32

    async def test_longueur_personnalisee(self, tmp_path: Path) -> None:
        fichier = tmp_path / "v.mp4"
        fichier.write_bytes(b"x")
        assert len(await Blake2Hasher(digest_size=8).digest(fichier)) == 16


class TestDisque:
    def test_espace_libre_positif(self, tmp_path: Path) -> None:
        assert free_bytes(tmp_path) > 0

    def test_espaces_libres_sur_dossier_inexistant(self, tmp_path: Path) -> None:
        assert free_bytes(tmp_path / "inexistant" / "profond") > 0

    def test_creation_du_dossier(self, tmp_path: Path) -> None:
        cible = tmp_path / "a" / "b"
        ensure_writable(cible)
        assert cible.is_dir()

    def test_taille_dossier(self, tmp_path: Path) -> None:
        (tmp_path / "a.mp4").write_bytes(b"x" * 100)
        sous = tmp_path / "sous"
        sous.mkdir()
        (sous / "b.mp4").write_bytes(b"y" * 50)
        assert directory_size(tmp_path) == 150

    def test_taille_dossier_inexistant(self, tmp_path: Path) -> None:
        assert directory_size(tmp_path / "rien") == 0

    def test_disque_plein_detecte(self, tmp_path: Path) -> None:
        with pytest.raises(DiskFullError):
            # Demande volontairement plus que l'espace disponible sur la machine.
            ensure_writable(tmp_path, required=10**18)


class TestFormatage:
    @pytest.mark.parametrize(
        ("octets", "attendu"),
        [
            (0, "0,0 o"),
            (512, "512,0 o"),
            (2048, "2,0 Ko"),
            (5 * 1024 * 1024, "5,0 Mo"),
            (3 * 1024**3, "3,0 Go"),
        ],
    )
    def test_tailles(self, octets: float, attendu: str) -> None:
        assert human_size(octets) == attendu

    @pytest.mark.parametrize(
        ("secondes", "attendu"),
        [
            (None, "—"),
            (0, "0 s"),
            (45, "45 s"),
            (125, "2 min 05 s"),
            (3725, "1 h 02 min 05 s"),
        ],
    )
    def test_durees(self, secondes: float | None, attendu: str) -> None:
        assert human_duration(secondes) == attendu
