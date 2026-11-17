"""Tests du FFmpeg embarqué dans l'exécutable PyInstaller (infrastructure/bundle)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from tikvault.infrastructure.bundle import (
    bundled_ffmpeg_dir,
    ensure_ffmpeg_on_path,
    frozen_dir,
)

NOM_FFMPEG = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"


def _simuler_gel(monkeypatch: pytest.MonkeyPatch, base: Path) -> None:
    """Reproduit l'environnement PyInstaller : sys.frozen + sys._MEIPASS."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(base), raising=False)


def test_hors_gel_rien_ne_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lancé normalement (pas en .exe), le module est transparent."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    chemin_avant = os.environ.get("PATH", "")
    assert frozen_dir() is None
    assert bundled_ffmpeg_dir() is None
    assert ensure_ffmpeg_on_path() is False
    assert os.environ.get("PATH", "") == chemin_avant


def test_gel_sans_ffmpeg_embarque(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Un .exe fabriqué sans build/ffmpeg : pas d'injection, pas d'erreur."""
    _simuler_gel(monkeypatch, tmp_path)
    assert frozen_dir() == tmp_path
    assert bundled_ffmpeg_dir() is None
    assert ensure_ffmpeg_on_path() is False


def test_gel_avec_ffmpeg_embarque(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Un .exe avec FFmpeg : le dossier embarqué passe en tête du PATH."""
    dossier = tmp_path / "ffmpeg"
    dossier.mkdir()
    (dossier / NOM_FFMPEG).write_bytes(b"binaire factice")
    _simuler_gel(monkeypatch, tmp_path)
    monkeypatch.setenv("PATH", "/usr/bin")

    assert bundled_ffmpeg_dir() == dossier
    assert ensure_ffmpeg_on_path() is True
    # Tête du PATH = FFmpeg embarqué, prioritaire sur un éventuel FFmpeg système.
    assert os.environ["PATH"].split(os.pathsep)[0] == str(dossier)
    assert os.environ["PATH"].split(os.pathsep)[1] == "/usr/bin"
