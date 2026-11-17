"""Relances : backoff exponentiel, jitter, filtrage des erreurs."""

from __future__ import annotations

import asyncio

import pytest

from tikvault.domain.errors import (
    DeletedVideoError,
    NetworkError,
    RateLimitedError,
)
from tikvault.services.retry import RetryOutcome, RetryPolicy, retry_async


async def _no_sleep(_delay: float) -> None:
    """Remplace asyncio.sleep pour des tests instantanés."""
    return None


class TestRetryPolicy:
    def test_croissance_exponentielle(self) -> None:
        policy = RetryPolicy(base_delay=1.0, multiplier=2.0, jitter=0.0)
        assert [policy.delay_for(n) for n in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 8.0]

    def test_plafond_respecte(self) -> None:
        policy = RetryPolicy(base_delay=10.0, multiplier=3.0, max_delay=25.0, jitter=0.0)
        assert policy.delay_for(5) == 25.0

    def test_jitter_borne_le_delai(self) -> None:
        policy = RetryPolicy(base_delay=4.0, multiplier=2.0, jitter=0.5)
        for _ in range(200):
            delay = policy.delay_for(2)  # théorique : 8 s
            assert 4.0 <= delay <= 12.0

    def test_jitter_produit_des_valeurs_differentes(self) -> None:
        policy = RetryPolicy(base_delay=4.0, jitter=0.5)
        valeurs = {round(policy.delay_for(1), 3) for _ in range(50)}
        assert len(valeurs) > 1, "le jitter doit étaler les relances"


class TestRetryAsync:
    async def test_succes_immediat(self) -> None:
        async def operation(_attempt: int) -> str:
            return "ok"

        outcome = await retry_async(operation, sleep=_no_sleep)
        assert outcome.success is True
        assert outcome.result == "ok"
        assert outcome.attempts == 1

    async def test_relaunch_jusqu_au_succes(self) -> None:
        appels: list[int] = []

        async def operation(attempt: int) -> str:
            appels.append(attempt)
            if attempt < 3:
                msg = "HTTP Error 429: Too Many Requests"
                raise RuntimeError(msg)
            return "ok"

        outcome = await retry_async(operation, sleep=_no_sleep)
        assert outcome.success is True
        assert appels == [1, 2, 3]

    async def test_erreur_non_relaunchable_arrete(self) -> None:
        appels = 0

        async def operation(_attempt: int) -> str:
            nonlocal appels
            appels += 1
            raise DeletedVideoError

        outcome = await retry_async(operation, policy=RetryPolicy(max_attempts=5), sleep=_no_sleep)
        assert outcome.success is False
        assert appels == 1, "une vidéo supprimée ne se relance pas"

    async def test_erreur_reseau_relancee(self) -> None:
        async def operation(_attempt: int) -> str:
            raise NetworkError

        outcome = await retry_async(operation, policy=RetryPolicy(max_attempts=3), sleep=_no_sleep)
        assert outcome.success is False
        assert outcome.attempts == 3

    async def test_predicat_personnalise(self) -> None:
        async def operation(_attempt: int) -> str:
            raise RateLimitedError

        outcome = await retry_async(
            operation,
            policy=RetryPolicy(max_attempts=4),
            should_retry=lambda _exc, attempt: attempt < 2,
            sleep=_no_sleep,
        )
        assert outcome.attempts == 2

    async def test_callback_on_retry_appele(self) -> None:
        vus: list[tuple[int, float]] = []

        async def on_retry(exc: BaseException, attempt: int, delay: float) -> None:
            vus.append((attempt, delay))
            assert isinstance(exc, BaseException)

        async def operation(_attempt: int) -> str:
            msg = "connection timed out"
            raise RuntimeError(msg)

        await retry_async(
            operation, policy=RetryPolicy(max_attempts=3), on_retry=on_retry, sleep=_no_sleep
        )
        assert [attempt for attempt, _ in vus] == [1, 2]

    async def test_annulation_ne_se_relance_pas(self) -> None:
        async def operation(_attempt: int) -> str:
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await retry_async(operation, sleep=_no_sleep)

    async def test_raise_if_failed(self) -> None:
        outcome: RetryOutcome[str] = RetryOutcome(success=False, error=NetworkError(), attempts=2)
        with pytest.raises(NetworkError):
            outcome.raise_if_failed()

    async def test_delais_reellement_appliques(self) -> None:
        """Vérifie que ``sleep`` est bien appelé avec le délai calculé."""
        delays: list[float] = []

        async def sleep(delay: float) -> None:
            delays.append(delay)

        async def operation(_attempt: int) -> str:
            msg = "too many requests"
            raise RuntimeError(msg)

        await retry_async(
            operation,
            policy=RetryPolicy(max_attempts=3, base_delay=2.0, jitter=0.0),
            sleep=sleep,
        )
        assert delays == [2.0, 4.0]
