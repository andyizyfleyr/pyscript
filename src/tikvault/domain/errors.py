"""Hiérarchie d'erreurs métier et classification des erreurs de yt-dlp.

L'objectif : transformer un message brut (« ERROR: [TikTok] 7xxx: Video unavailable »)
en une erreur typée, avec un message clair **en français** et un drapeau « réessayable ».
L'UI n'a donc jamais à analyser des chaînes de caractères.
"""

from __future__ import annotations

import re
from typing import ClassVar

from tikvault.domain.models import FailureKind

# ``FailureKind`` est réexportée ici : c'est le module d'erreurs que l'UI importe.
__all__ = [
    "ConfigError",
    "DeletedVideoError",
    "DiskFullError",
    "DownloadCancelled",
    "ExtractionError",
    "FailureKind",
    "GeoBlockedError",
    "LoginRequiredError",
    "NetworkError",
    "PrivateVideoError",
    "RateLimitedError",
    "TikVaultError",
    "VideoUnavailableError",
    "classify_error",
    "is_retryable",
]


class TikVaultError(Exception):
    """Erreur de base de l'application."""

    kind: ClassVar[FailureKind] = FailureKind.UNKNOWN
    retryable: ClassVar[bool] = False
    default_message: ClassVar[str] = "Erreur inconnue."

    def __init__(self, message: str | None = None, *, cause: BaseException | None = None) -> None:
        self.message = message or self.default_message
        self.cause = cause
        super().__init__(self.message)

    @property
    def user_message(self) -> str:
        """Message destiné à l'utilisateur final (toujours en français)."""
        return self.message


class ExtractionError(TikVaultError):
    """Aucun extracteur n'a pu traiter la source."""

    kind = FailureKind.EXTRACTOR
    retryable = True
    default_message = "Impossible d'extraire la vidéo. TikTok a peut-être changé son site."


class VideoUnavailableError(TikVaultError):
    """Vidéo inaccessible pour une raison indéterminée."""

    kind = FailureKind.DELETED
    retryable = False
    default_message = "Cette vidéo n'est plus disponible."


class PrivateVideoError(VideoUnavailableError):
    kind = FailureKind.PRIVATE
    default_message = "Vidéo privée ou réservée aux abonnés : téléchargement impossible."


class DeletedVideoError(VideoUnavailableError):
    kind = FailureKind.DELETED
    default_message = "Vidéo supprimée par son auteur ou par TikTok."


class GeoBlockedError(VideoUnavailableError):
    kind = FailureKind.GEOBLOCKED
    retryable = True  # réessayable avec un proxy d'une autre région
    default_message = (
        "Vidéo indisponible dans votre pays. Ajoutez un proxy situé dans la région concernée."
    )


class RateLimitedError(TikVaultError):
    kind = FailureKind.RATE_LIMITED
    retryable = True
    default_message = (
        "TikTok limite nos requêtes (erreur 429). Ralentissez la cadence ou ajoutez des proxies."
    )


class LoginRequiredError(TikVaultError):
    kind = FailureKind.LOGIN_REQUIRED
    retryable = False
    default_message = "Cette vidéo exige une connexion : importez les cookies de votre navigateur."


class NetworkError(TikVaultError):
    kind = FailureKind.NETWORK
    retryable = True
    default_message = "Problème réseau (délai dépassé ou connexion interrompue)."


class DiskFullError(TikVaultError):
    kind = FailureKind.DISK_FULL
    retryable = False
    default_message = "Espace disque insuffisant. Choisissez un autre dossier de destination."


class DownloadCancelled(TikVaultError):
    """Annulation coopérative demandée par l'utilisateur."""

    kind = FailureKind.CANCELLED
    retryable = False
    default_message = "Téléchargement annulé."


class ConfigError(TikVaultError):
    """Configuration invalide (template de nom, cookies, proxy…)."""

    kind = FailureKind.UNKNOWN
    retryable = False
    default_message = "Configuration invalide."


# ---------------------------------------------------------------------------
# Classification des erreurs brutes
# ---------------------------------------------------------------------------
# Chaque règle : motif regex (insensible à la casse) → type d'erreur.
# Ordre important : les cas les plus précis d'abord.
_PATTERNS: list[tuple[re.Pattern[str], type[TikVaultError]]] = [
    (re.compile(r"private video|only the owner|followers-only", re.I), PrivateVideoError),
    (
        re.compile(r"video unavailable|does(n't| not) exist|has been removed|not found", re.I),
        DeletedVideoError,
    ),
    (
        re.compile(r"not available in your country|geo.?restrict|blocked in|country", re.I),
        GeoBlockedError,
    ),
    (re.compile(r"\b429\b|too many requests|rate.?limit", re.I), RateLimitedError),
    (
        re.compile(r"login|log in|sign in|cookies? (are|is) required|authentication", re.I),
        LoginRequiredError,
    ),
    (re.compile(r"no space left|disk full|enospc", re.I), DiskFullError),
    (
        re.compile(
            r"timed? ?out|temporary failure|connection (reset|refused|aborted)|"
            r"unable to (download|connect)|network is unreachable|ssl|eof occurred",
            re.I,
        ),
        NetworkError,
    ),
    (
        re.compile(r"unsupported url|no video formats|requested format is not available", re.I),
        ExtractionError,
    ),
]


def classify_error(exc: BaseException) -> TikVaultError:
    """Traduit n'importe quelle exception (souvent issue de yt-dlp) en erreur métier.

    La chaîne analysée contient le type **et** le message de l'exception : yt-dlp
    remonte parfois l'information utile uniquement dans le texte.
    """
    if isinstance(exc, TikVaultError):
        return exc

    haystack = f"{type(exc).__name__}: {exc}"
    for pattern, error_type in _PATTERNS:
        if pattern.search(haystack):
            # On conserve le message brut pour le journal, mais on expose un libellé clair.
            error = error_type(cause=exc)
            error.message = f"{error.default_message}"
            return error

    return ExtractionError(message=f"Échec du téléchargement : {exc}"[:400], cause=exc)


def is_retryable(exc: BaseException) -> bool:
    """Décide si une nouvelle tentative est pertinente."""
    return classify_error(exc).retryable
