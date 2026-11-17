"""Rotation d'User-Agents réalistes.

TikTok renvoie plus volontiers les flux sans filigrane aux clients « navigateur »
récents. On fait tourner une poignée d'UA majeurs pour diluer l'empreinte.
"""

from __future__ import annotations

import itertools
import random

# Navigateurs grand public récents (Windows / macOS / Linux).
USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.6; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.6 Safari/605.1.15",
)


class RotatingUserAgents:
    """Fournit un User-Agent à chaque appel (rotation déterministe ou aléatoire)."""

    def __init__(self, agents: tuple[str, ...] = USER_AGENTS, *, shuffle: bool = False) -> None:
        pool = list(agents)
        if shuffle:
            random.shuffle(pool)
        self._cycle = itertools.cycle(pool)
        self._size = len(pool)

    def next(self) -> str:
        return next(self._cycle)

    def __len__(self) -> int:
        return self._size
