"""Pool de proxies avec rotation, compteur d'échecs et mise en quarantaine.

Stratégie : on privilégie le proxy ayant le moins d'échecs et hors quarantaine.
Si **tous** sont en quarantaine, on renvoie quand même le moins mauvais : mieux vaut
une tentative lente qu'un téléchargement bloqué indéfiniment.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field


@dataclass(slots=True)
class ProxyState:
    """État courant d'un proxy."""

    url: str
    failures: int = 0
    successes: int = 0
    quarantined_until: float = 0.0

    def healthy_at(self, now: float) -> bool:
        return now >= self.quarantined_until


@dataclass(slots=True)
class ProxyPool:
    """Rotation de proxies, conforme au port :class:`tikvault.domain.ports.ProxyProvider`."""

    urls: list[str] = field(default_factory=list)
    cooldown_seconds: float = 90.0
    max_failures: int = 3
    clock: Callable[[], float] = time.monotonic

    _states: list[ProxyState] = field(default_factory=list, init=False, repr=False)
    _cursor: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self._states = [ProxyState(url=url.strip()) for url in self.urls if url.strip()]

    def __len__(self) -> int:
        return len(self._states)

    def next(self) -> str | None:
        """Proxy à utiliser pour la prochaine tentative (``None`` si pool vide).

        La sélection est une vraie rotation (répartition de la charge) restreinte aux
        proxies hors quarantaine. Le compteur d'échecs sert uniquement à décider de la
        mise à l'écart : il ne doit pas figer la rotation sur un seul proxy.
        """
        if not self._states:
            return None
        now = self.clock()
        total = len(self._states)
        ordered = [self._states[(self._cursor + i) % total] for i in range(total)]
        healthy = [state for state in ordered if state.healthy_at(now)]
        chosen = (healthy or ordered)[0]
        self._cursor = (self._states.index(chosen) + 1) % total
        return chosen.url

    def report_failure(self, proxy: str) -> None:
        state = self._find(proxy)
        if state is None:
            return
        state.failures += 1
        if state.failures >= self.max_failures:
            state.quarantined_until = self.clock() + self.cooldown_seconds
            state.failures = 0  # on repart à zéro après la quarantaine

    def report_success(self, proxy: str) -> None:
        """Un succès remet le compteur d'échecs à zéro : le proxy est de nouveau fiable."""
        state = self._find(proxy)
        if state is None:
            return
        state.successes += 1
        state.failures = 0
        state.quarantined_until = 0.0

    def status(self) -> list[dict[str, object]]:
        """État lisible pour la page Réglages."""
        now = self.clock()
        return [
            {
                "url": state.url,
                "échecs": state.failures,
                "succès": state.successes,
                "disponible": state.healthy_at(now),
            }
            for state in self._states
        ]

    def _find(self, proxy: str) -> ProxyState | None:
        for state in self._states:
            if state.url == proxy:
                return state
        return None


def parse_proxy_list(raw: str | Iterable[str]) -> list[str]:
    """Analyse la liste saisie dans les réglages (une entrée par ligne, ou séparée par « ; »)."""
    if isinstance(raw, str):
        chunks = [part for chunk in raw.splitlines() for part in chunk.split(";")]
    else:
        chunks = list(raw)

    cleaned: list[str] = []
    for chunk in chunks:
        url = chunk.strip().strip(",").strip()
        if not url or url.startswith("#"):
            continue
        if "://" not in url:
            url = f"http://{url}"
        if url not in cleaned:
            cleaned.append(url)
    return cleaned
