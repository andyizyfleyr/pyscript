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

donnees = [
    # (source, destination relative dans l'exécutable)
    (str(RACINE / "assets"), "assets"),
]
# Certificats/locales éventuels distribués avec yt-dlp.
donnees += collect_data_files("yt_dlp")

a = Analysis(
    [str(RACINE / "src" / "tikvault" / "__main__.py")],
    pathex=[str(RACINE / "src")],
    binaries=[],
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
