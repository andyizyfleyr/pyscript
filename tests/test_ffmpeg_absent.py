"""Régression : machine sans FFmpeg (cas réel rencontré sous Windows).

Deux bugs couverts :

1. yt-dlp remontait « ffmpeg not found », et le « not found » était classé
   :class:`DeletedVideoError` (« Vidéo supprimée par son auteur ou par TikTok ») —
   message complètement trompeur, avec 4 relances inutiles.
2. Les post-processeurs (métadonnées, miniature) et le format fusionné
   ``bv*+ba`` étaient demandés même sans FFmpeg → 100 % d'échecs en phase
   « post-traitement (Metadata) ». Désormais le téléchargement **dégrade
   proprement** : flux complet, sans intégration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tikvault.domain.errors import (
    DeletedVideoError,
    FfmpegMissingError,
    classify_error,
    is_retryable,
)
from tikvault.domain.models import DownloadOptions
from tikvault.domain.ports import ExtractionContext
from tikvault.infrastructure.extractors import ytdlp as ytdlp_mod
from tikvault.infrastructure.extractors.ytdlp import (
    AUDIO_ONLY_FORMAT,
    BEST_FORMAT,
    BEST_FORMAT_NO_FFMPEG,
    YtDlpExtractor,
)

# ---------------------------------------------------------------------------
# Classification des erreurs FFmpeg
# ---------------------------------------------------------------------------

MESSAGES_FFMPEG = [
    "ffmpeg not found. Please install or provide the path using --ffmpeg-location",
    "ERROR: Postprocessing: ffprobe and ffmpeg not found. Please install or provide the path",
    "You have requested merging of multiple formats but ffmpeg is not installed",
]


@pytest.mark.parametrize("message", MESSAGES_FFMPEG)
def test_erreur_ffmpeg_bien_classee(message: str) -> None:
    """« ffmpeg not found » ne doit JAMAIS devenir « vidéo supprimée »."""
    erreur = classify_error(RuntimeError(message))
    assert isinstance(erreur, FfmpegMissingError)
    assert not isinstance(erreur, DeletedVideoError)
    assert "FFmpeg" in erreur.message  # message explicite pour l'utilisateur
    assert not is_retryable(RuntimeError(message))  # relancer 4 fois ne sert à rien


def test_video_supprimee_toujours_classee() -> None:
    """Le motif « vidéo supprimée » continue de fonctionner, lui."""
    erreur = classify_error(RuntimeError("ERROR: Video not found or has been removed"))
    assert isinstance(erreur, DeletedVideoError)


# ---------------------------------------------------------------------------
# Dégradation gracieuse des options yt-dlp
# ---------------------------------------------------------------------------


def _opts_ytdlp(
    tmp_path: Path, options: DownloadOptions, *, avec_ffmpeg: bool, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    monkeypatch.setattr(ytdlp_mod, "ffmpeg_available", lambda: avec_ffmpeg)
    extracteur = YtDlpExtractor()
    return extracteur._download_options(
        options, ExtractionContext(), tmp_path / "sortie.mp4", None, None
    )


def test_sans_ffmpeg_video_telechargee_sans_postprocesseurs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = DownloadOptions(output_dir=str(tmp_path), embed_metadata=True, embed_thumbnail=True)
    opts = _opts_ytdlp(tmp_path, options, avec_ffmpeg=False, monkeypatch=monkeypatch)
    assert opts["postprocessors"] == []  # rien qui exige FFmpeg
    assert opts["format"] == BEST_FORMAT_NO_FFMPEG  # pas de fusion de flux
    assert "writethumbnail" not in opts


def test_avec_ffmpeg_postprocesseurs_actifs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = DownloadOptions(output_dir=str(tmp_path), embed_metadata=True, embed_thumbnail=True)
    opts = _opts_ytdlp(tmp_path, options, avec_ffmpeg=True, monkeypatch=monkeypatch)
    cles = [pp["key"] for pp in opts["postprocessors"]]
    assert "FFmpegMetadata" in cles
    assert "EmbedThumbnail" in cles
    assert opts["format"] == BEST_FORMAT


def test_audio_seul_sans_ffmpeg_erreur_claire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La conversion MP3 sans FFmpeg est impossible : erreur explicite, non retentée."""
    options = DownloadOptions(output_dir=str(tmp_path), audio_only=True)
    with pytest.raises(FfmpegMissingError) as excinfo:
        _opts_ytdlp(tmp_path, options, avec_ffmpeg=False, monkeypatch=monkeypatch)
    assert "FFmpeg" in str(excinfo.value.message)
    assert excinfo.value.retryable is False


def test_audio_seul_avec_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    options = DownloadOptions(output_dir=str(tmp_path), audio_only=True, audio_format="m4a")
    opts = _opts_ytdlp(tmp_path, options, avec_ffmpeg=True, monkeypatch=monkeypatch)
    assert opts["format"] == AUDIO_ONLY_FORMAT
    cles = [pp["key"] for pp in opts["postprocessors"]]
    assert "FFmpegExtractAudio" in cles
