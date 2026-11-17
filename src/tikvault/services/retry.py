"""Nouvelle tentative intelligente : backoff exponentiel + jitter + filtrage des erreurs.

Pourquoi du jitter ? Quand 5 workers butent sur la même limite de requêtes (429),
des délais identiques les feraient réessayer **en même temps** et aggraver le blocage.
Le jitter étale les tentatives dans le temps.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

import structlog

from tikvault.domain.errors import TikVaultError, classify_error

T = TypeVar("T")

logger = structlog.get_logger(__name__)


@dataclass(slots=True, frozen=True)
class RetryPolicy:
    """Paramètres de relance (valeurs par défaut pensées pour TikTok)."""

    max_attempts: int = 4
    base_delay: float = 1.5  # secondes
    max_delay: float = 90.0
    multiplier: float = 2.0
    jitter: float = 0.5  # proportion de délai aléatoire (0 → déterministe, 1 → « full jitter »)

    def delay_for(self, attempt: int) -> float:
        """Délai avant la tentative numéro ``attempt`` (1 = première relance)."""
        raw = self.base_delay * (self.multiplier ** max(0, attempt - 1))
        raw = min(raw, self.max_delay)
        if self.jitter <= 0:
            return raw
        # « Decorrelated jitter » : on garde au moins la moitié du délai théorique.
        spread = raw * self.jitter
        return max(0.1, raw - spread + random.uniform(0, 2 * spread))


@dataclass(slots=True)
class RetryOutcome(Generic[T]):
    """Bilan d'une opération relançable (générique sur le type du résultat)."""

    success: bool
    result: T | None = None
    error: BaseException | None = None
    attempts: int = 0

    def raise_if_failed(self) -> None:
        if not self.success and self.error is not None:
            raise self.error


async def retry_async(
    operation: Callable[[int], Awaitable[T]],
    *,
    policy: RetryPolicy | None = None,
    should_retry: Callable[[BaseException, int], bool] | None = None,
    on_retry: Callable[[BaseException, int, float], Awaitable[None] | None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> RetryOutcome[T]:
    """Exécute ``operation(attempt)`` en relançant tant que l'erreur est récupérable.

    :param operation: coroutine recevant le numéro de tentative (commence à 1).
    :param should_retry: prédicat ``(erreur, tentative) -> bool`` ; par défaut on se base
        sur la classification métier (:func:`tikvault.domain.errors.classify_error`).
    :param on_retry: rappelé avant chaque attente (permet d'émettre un événement UI).
    :param sleep: injectable pour accélérer les tests.
    """
    policy = policy or RetryPolicy()
    predicate = should_retry or _default_should_retry

    last_error: BaseException | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            result = await operation(attempt)
        except asyncio.CancelledError:
            # Une annulation utilisateur ne se relance jamais.
            raise
        except BaseException as exc:
            last_error = exc
            classified: TikVaultError = classify_error(exc)
            logger.warning(
                "echec_tentative",
                tentative=attempt,
                max=policy.max_attempts,
                erreur=classified.kind.value,
                detail=str(exc)[:200],
            )
            if attempt >= policy.max_attempts or not predicate(exc, attempt):
                return RetryOutcome(success=False, error=exc, attempts=attempt)

            delay = policy.delay_for(attempt)
            if on_retry is not None:
                callback_result = on_retry(exc, attempt, delay)
                if asyncio.iscoroutine(callback_result):
                    await callback_result
            await sleep(delay)
        else:
            return RetryOutcome(success=True, result=result, attempts=attempt)

    return RetryOutcome(success=False, error=last_error, attempts=policy.max_attempts)


def _default_should_retry(exc: BaseException, attempt: int) -> bool:
    return classify_error(exc).retryable
