# -*- mode: python ; coding: utf-8 -*-
"""Recette PyInstaller de TikVault (exécutable autonome, un seul fichier).

Fabrication (depuis la racine du dépôt, après ``uv sync --extra build``) :

.. code-block:: console

    uv run pyinstaller build/tikvault.spec --noconfirm

Le binaire est déposé dans ``dist/TikVault.exe`` sous Windows (``dist/TikVault``
sous Linux/macOS). La recette :

* embarque le dossier ``assets/`` (icônes de l'application) ;
* collecte les ressources de yt-dlp et de PySide6 nécessaires à l'exécution ;
* exclut ``tkinter`` et quelques gros modules jamais utilisés, pour alléger
  l'exécutable ;
* est « fenêtrée » (``console=False``) : aucun terminal ne s'ouvre au lancement.
  Pour un binaire de débogage avec console, passer ``console=True`` ci-dessous.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# ``SPECPATH`` est injecté par PyInstaller : chemin du dossier contenant ce .spec.
RACINE = Path(SPECPATH).resolve().parent  # noqa: F821

# yt-dlp charge ses extracteurs dynamiquement : il faut forcer leur collecte.
imports_caches = collect_submodules("yt_dlp")

# SQLAlchemy charge son pilote par NOM à l'exécution (« sqlite+aiosqlite:// ») :
# PyInstaller ne voit jamais ces imports → « No module named 'aiosqlite' » au
# premier démarrage du moteur. On force donc le pilote et ses dépendances.
imports_caches += [
    "aiosqlite",
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.sqlite.aiosqlite",
    "greenlet",  # pont sync/async interne de SQLAlchemy, chargé tardivement
]

donnees = [
    # (source, destination relative dans l'exécutable)
    (str(RACINE / "assets"), "assets"),
]
# Certificats/locales éventuels distribués avec yt-dlp.
donnees += collect_data_files("yt_dlp")

# --- FFmpeg embarqué (optionnel) -------------------------------------------
# Si `scripts/fetch_ffmpeg.py` a été lancé, build/ffmpeg/ contient ffmpeg.exe
# et ffprobe.exe : on les glisse dans l'exécutable, sous le dossier « ffmpeg ».
# Au démarrage, TikVault les place en tête du PATH (infrastructure/bundle.py).
binaires: list[tuple[str, str]] = []
DOSSIER_FFMPEG = RACINE / "build" / "ffmpeg"
for _nom in ("ffmpeg.exe", "ffprobe.exe", "ffmpeg", "ffprobe"):
    _exe = DOSSIER_FFMPEG / _nom
    if _exe.exists():
        binaires.append((str(_exe), "ffmpeg"))
if binaires:
    print(f"FFmpeg embarqué : {[b[0] for b in binaires]}")
else:
    print(
        "NOTE : build/ffmpeg absent — le .exe sera fabriqué SANS FFmpeg embarqué.\n"
        "       Lancez d'abord : uv run python scripts/fetch_ffmpeg.py"
    )

# --- OpenSSL : embarquement FORCÉ des DLL du Python de compilation ---------
# Symptôme corrigé : « DLL load failed while importing _ssl : La procédure
# spécifiée est introuvable » au lancement du .exe. Cause : _ssl.pyd se lie à
# une DLL OpenSSL incompatible trouvée ailleurs (PATH, System32…) au lieu de
# celle livrée avec Python. On ajoute explicitement les DLL officielles du
# Python qui compile, EN TÊTE de liste : elles priment sur toute autre copie.
import sys as _sys

if _sys.platform == "win32":
    _dlls_ssl: list[str] = []
    for _dossier in (
        Path(_sys.base_prefix) / "DLLs",
        Path(_sys.base_prefix),
        Path(_sys.base_prefix) / "bin",
    ):
        if not _dossier.is_dir():
            continue
        for _motif in ("libssl*.dll", "libcrypto*.dll", "libffi*.dll"):
            for _dll in _dossier.glob(_motif):
                binaires.insert(0, (str(_dll), "."))
                _dlls_ssl.append(_dll.name)
    if _dlls_ssl:
        print(f"OpenSSL embarqué explicitement : {sorted(set(_dlls_ssl))}")
    else:
        print(
            "ATTENTION : aucune DLL OpenSSL trouvée près du Python de compilation — "
            "si le .exe échoue sur _ssl, compilez avec le Python de python.org."
        )

a = Analysis(
    [str(RACINE / "src" / "tikvault" / "__main__.py")],
    pathex=[str(RACINE / "src")],
    binaries=binaires,
    datas=donnees,
    hiddenimports=imports_caches,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Jamais utilisés par TikVault : on allège le binaire.
        "tkinter",
        "unittest",
        "pydoc_data",
        "lib2to3",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3DCore",
        "PySide6.QtCharts",
        "PySide6.QtQuick",
        "PySide6.QtQml",
        "PySide6.QtMultimedia",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="TikVault",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX déclenche trop de faux positifs antivirus : désactivé.
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # application fenêtrée, pas de terminal.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(RACINE / "assets" / "icon.ico"),
)
