"""Régression : configuration des logs dans un .exe fenêtré (sans console).

PyInstaller avec ``console=False`` met ``sys.stdout`` et ``sys.stderr`` à
``None`` : le démarrage plantait sur ``sys.stderr.isatty()``
(« 'NoneType' object has no attribute 'isatty' »).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from tikvault.infrastructure import logging_setup


@pytest.fixture(autouse=True)
def _reinitialiser(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Chaque test repart d'une configuration vierge, sans toucher aux autres tests."""
    monkeypatch.setattr(logging_setup, "_CONFIGURED", False)
    racine = logging.getLogger()
    anciens = racine.handlers[:]
    yield
    racine.handlers[:] = anciens


def test_sans_console_ne_plante_pas(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """.exe fenêtré : stderr absent → pas de crash, le fichier de log reste actif."""
    monkeypatch.setattr(sys, "stderr", None)
    journal = tmp_path / "logs" / "tikvault.log"

    logging_setup.setup_logging(log_file=journal)  # ne doit pas lever

    racine = logging.getLogger()
    assert any(isinstance(h, logging.handlers.RotatingFileHandler) for h in racine.handlers), (
        "le fichier de log doit rester actif sans console"
    )
    assert not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in racine.handlers
    ), "aucune sortie console ne doit être installée quand stderr est None"


def test_avec_console_sortie_installee(tmp_path: Path) -> None:
    """Lancement normal : la sortie console est bien là."""
    logging_setup.setup_logging(log_file=tmp_path / "t.log")
    racine = logging.getLogger()
    assert any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in racine.handlers
    )
