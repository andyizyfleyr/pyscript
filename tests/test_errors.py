"""Classification des erreurs brutes en erreurs métier lisibles."""

from __future__ import annotations

import pytest

from tikvault.domain.errors import (
    DeletedVideoError,
    DiskFullError,
    DownloadCancelled,
    ExtractionError,
    FailureKind,
    GeoBlockedError,
    LoginRequiredError,
    NetworkError,
    PrivateVideoError,
    RateLimitedError,
    TikVaultError,
    classify_error,
    is_retryable,
)


class TestClassification:
    @pytest.mark.parametrize(
        ("message", "attendu"),
        [
            ("ERROR: [TikTok] 7123: Private video. Sign in to verify", PrivateVideoError),
            ("ERROR: Video unavailable", DeletedVideoError),
            ("ERROR: This video does not exist", DeletedVideoError),
            ("ERROR: The video has been removed by the creator", DeletedVideoError),
            (
                "ERROR: The uploader has not made this video available in your country",
                GeoBlockedError,
            ),
            ("ERROR: HTTP Error 429: Too Many Requests", RateLimitedError),
            ("ERROR: rate limit exceeded", RateLimitedError),
            ("ERROR: Login required to view this content", LoginRequiredError),
            ("ERROR: cookies are required", LoginRequiredError),
            ("ERROR: unable to download webpage: timed out", NetworkError),
            ("ERROR: Unable to connect to host (connection reset by peer)", NetworkError),
            ("ERROR: [Errno 28] No space left on device", DiskFullError),
            ("ERROR: Requested format is not available", ExtractionError),
        ],
    )
    def test_messages_yt_dlp(self, message: str, attendu: type[TikVaultError]) -> None:
        assert isinstance(classify_error(RuntimeError(message)), attendu)

    def test_message_utilisateur_en_francais(self) -> None:
        erreur = classify_error(RuntimeError("ERROR: Private video"))
        assert "privée" in erreur.user_message.lower()

    def test_erreur_deja_metier_inchangee(self) -> None:
        originale = GeoBlockedError("message personnalisé")
        assert classify_error(originale) is originale

    def test_erreur_inconnue_devient_extraction(self) -> None:
        erreur = classify_error(ValueError("chose bizarre"))
        assert isinstance(erreur, ExtractionError)
        assert erreur.kind is FailureKind.EXTRACTOR

    def test_cause_conservee(self) -> None:
        cause = RuntimeError("détail technique")
        assert classify_error(cause).cause is cause

    def test_message_tronque(self) -> None:
        erreur = classify_error(RuntimeError("x" * 2000))
        assert len(erreur.message) <= 400


class TestRetryabilite:
    @pytest.mark.parametrize(
        ("erreur", "relancable"),
        [
            (RateLimitedError(), True),
            (NetworkError(), True),
            (GeoBlockedError(), True),  # réessayable via un proxy d'une autre région
            (ExtractionError(), True),
            (PrivateVideoError(), False),
            (DeletedVideoError(), False),
            (LoginRequiredError(), False),
            (DiskFullError(), False),
            (DownloadCancelled(), False),
        ],
    )
    def test_drapeau(self, erreur: TikVaultError, relancable: bool) -> None:
        assert erreur.retryable is relancable

    def test_predicat_depuis_exception_brute(self) -> None:
        assert is_retryable(RuntimeError("HTTP Error 429"))
        assert not is_retryable(RuntimeError("Private video"))

    def test_types_d_echec_stables(self) -> None:
        assert RateLimitedError.kind is FailureKind.RATE_LIMITED
        assert PrivateVideoError.kind is FailureKind.PRIVATE
        assert DownloadCancelled.kind is FailureKind.CANCELLED
