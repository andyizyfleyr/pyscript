"""Tests de régression du conteneur d'injection de dépendances.

Le bug corrigé ici : l'interface graphique construit le :class:`Container` dans son
thread moteur **avant** de démarrer la boucle asyncio (voir
``ui/engine_bridge.EngineBridge._run``). Toute capture de ``get_running_loop()`` dans
un ``__init__`` fait alors planter le moteur au lancement avec
« no running event loop » — ce que les tests classiques (tous exécutés *dans* une
boucle par pytest-asyncio) ne pouvaient pas détecter.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from tikvault.app import Container
from tikvault.infrastructure.paths import AppPaths
from tikvault.settings import AppSettings


def _paths(tmp_path: Path) -> AppPaths:
    """Arborescence jetable, isolée du système."""
    return AppPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        downloads_dir=tmp_path / "downloads",
    ).ensure()


@pytest.mark.parametrize("dans_un_thread", [False, True])
def test_container_constructible_sans_boucle(tmp_path: Path, dans_un_thread: bool) -> None:
    """Le conteneur doit pouvoir être construit sans boucle asyncio en cours.

    C'est exactement ce que fait le thread moteur de l'interface : ``Container(...)``
    d'abord, ``loop.run_until_complete(container.start())`` ensuite.
    """
    settings = AppSettings(output_dir=str(tmp_path / "sorties"))
    erreurs: list[BaseException] = []

    def construire() -> None:
        try:
            # Pas de boucle en cours ici : la construction doit quand même réussir.
            with pytest.raises(RuntimeError):
                asyncio.get_running_loop()
            Container(settings=settings, paths=_paths(tmp_path))
        except BaseException as exc:  # pragma: no cover - uniquement en cas de régression
            erreurs.append(exc)

    if dans_un_thread:
        thread = threading.Thread(target=construire)
        thread.start()
        thread.join(timeout=30)
    else:
        construire()
    assert not erreurs, f"construction impossible hors boucle : {erreurs!r}"


def test_cycle_de_vie_comme_le_thread_moteur(tmp_path: Path) -> None:
    """Reproduit fidèlement ``EngineBridge._run`` : construction hors boucle,
    puis ``start()``/``stop()`` sur une boucle créée après coup."""
    settings = AppSettings(output_dir=str(tmp_path / "sorties"))
    resultat: dict[str, object] = {}

    def corps_du_thread() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            container = Container(settings=settings, paths=_paths(tmp_path))
            loop.run_until_complete(container.start())
            resultat["demarre"] = True
            loop.run_until_complete(container.stop())
        except Exception as exc:  # pragma: no cover - uniquement en cas de régression
            resultat["erreur"] = exc
        finally:
            loop.close()

    thread = threading.Thread(target=corps_du_thread, name="test-engine")
    thread.start()
    thread.join(timeout=60)

    assert "erreur" not in resultat, f"le moteur a planté : {resultat['erreur']!r}"
    assert resultat.get("demarre") is True
