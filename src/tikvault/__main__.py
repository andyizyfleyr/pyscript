"""Point d'entrée de TikVault : interface graphique ou mode ligne de commande.

Usage
-----
.. code-block:: console

    tikvault                                  # interface graphique
    tikvault --headless "@compte" "#tag"      # sans fenêtre
    tikvault --headless --file liens.txt --wait
    tikvault --export rapport.csv             # export de l'historique
    tikvault --update                         # met yt-dlp à jour

Le mode ligne de commande utilise exactement le même moteur que l'interface :
file d'attente, relances, anti-doublon et persistance inclus.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# En mode exécutable, une mise à jour portable de yt-dlp est déposée dans un dossier
# utilisateur : on l'injecte en tête de ``sys.path`` **avant** tout import de yt_dlp.
_portable = os.environ.get("TIKVAULT_YTDLP_PATH")
if _portable and Path(_portable).is_dir() and _portable not in sys.path:
    sys.path.insert(0, _portable)

from tikvault import __version__  # noqa: E402
from tikvault.infrastructure.logging_setup import setup_logging  # noqa: E402
from tikvault.infrastructure.paths import default_paths  # noqa: E402
from tikvault.services.url_parser import ParsedSource  # noqa: E402
from tikvault.settings import AppSettings  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tikvault",
        description="TikVault — téléchargement massif de vidéos TikTok.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            '  tikvault "https://www.tiktok.com/@user/video/7123…"\n'
            '  tikvault --headless "@compte" --concurrency 5 --wait\n'
            "  tikvault --headless --file liens.txt --output D:/Videos --audio-only\n"
        ),
    )
    parser.add_argument("sources", nargs="*", help="Liens, @profils, #hashtags ou URL de son.")
    parser.add_argument("--file", dest="file", help="Fichier .txt ou .csv contenant des sources.")
    parser.add_argument(
        "--headless", action="store_true", help="Fonctionner sans interface graphique."
    )
    parser.add_argument(
        "--wait", action="store_true", help="Attendre la fin du lot (mode headless)."
    )
    parser.add_argument("--output", help="Dossier de destination.")
    parser.add_argument(
        "--template", help="Modèle de nom, ex. « {username}/{date}_{id}_{title}.mp4 »."
    )
    parser.add_argument("--concurrency", type=int, help="Téléchargements simultanés (1 à 10).")
    parser.add_argument("--rate-limit", type=int, help="Limitation de débit en Ko/s.")
    parser.add_argument("--audio-only", action="store_true", help="Extraire uniquement l'audio.")
    parser.add_argument("--audio-format", default=None, help="mp3, m4a, opus, flac, wav ou aac.")
    parser.add_argument("--max-items", type=int, help="Nombre maximum de vidéos par source.")
    parser.add_argument("--min-views", type=int, help="Nombre de vues minimum.")
    parser.add_argument("--proxy", action="append", help="Proxy à ajouter (répétable).")
    parser.add_argument("--cookies", help="Fichier cookies.txt à utiliser.")
    parser.add_argument(
        "--browser", help="Importer les cookies depuis un navigateur (chrome, firefox…)."
    )
    parser.add_argument(
        "--portable", help="Dossier « portable » : toute la configuration y est stockée."
    )
    parser.add_argument(
        "--export", metavar="CSV", help="Exporter l'historique en CSV puis quitter."
    )
    parser.add_argument(
        "--check-update", action="store_true", help="Vérifier la version de yt-dlp."
    )
    parser.add_argument("--update", action="store_true", help="Mettre yt-dlp à jour puis quitter.")
    parser.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING ou ERROR.")
    parser.add_argument("--version", action="version", version=f"TikVault {__version__}")
    return parser


def _apply_cli_overrides(settings: AppSettings, args: argparse.Namespace) -> AppSettings:
    """Reporte les options de ligne de commande sur les réglages."""
    updates: dict[str, object] = {}
    if args.output:
        updates["output_dir"] = args.output
    if args.template:
        updates["filename_template"] = args.template
    if args.concurrency:
        updates["max_concurrency"] = max(1, min(10, args.concurrency))
    if args.rate_limit:
        updates["rate_limit_kbps"] = args.rate_limit
    if args.audio_only:
        updates["audio_only"] = True
    if args.audio_format:
        updates["audio_format"] = args.audio_format
    if args.cookies:
        updates["cookies_file"] = args.cookies
    if args.browser:
        updates["cookies_browser"] = args.browser
    if args.proxy:
        updates["proxies"] = [*settings.proxies, *args.proxy]

    filters = settings.filters.model_copy(
        update={
            "max_items": args.max_items if args.max_items else settings.filters.max_items,
            "min_views": args.min_views if args.min_views else settings.filters.min_views,
        }
    )
    updates["filters"] = filters
    return settings.model_copy(update=updates) if updates else settings


async def _run_headless(args: argparse.Namespace, settings: AppSettings, paths: object) -> int:
    """Exécution sans interface : ajoute les sources, attend, affiche le bilan."""
    from tikvault.app import Container
    from tikvault.domain.events import LogEvent, TaskFailed, TaskStateChanged
    from tikvault.infrastructure.notifier import ConsoleNotifier

    container = Container(settings=settings, paths=paths, notifier=ConsoleNotifier())  # type: ignore[arg-type]

    async def _on_log(event: object) -> None:
        if isinstance(event, LogEvent):
            print(f"[{event.level}] {event.message}")
        elif isinstance(event, TaskStateChanged):
            print(
                f"· {event.task_id[:8]} → {event.current.value}"
                + (f" ({event.message})" if event.message else "")
            )
        elif isinstance(event, TaskFailed):
            print(f"✖ {event.task_id[:8]} : {event.message}")

    container.bus.subscribe_all(_on_log)

    async with container:
        if args.sources or args.file:
            sources = container.parser.parse_text("\n".join(args.sources))
            if args.file:
                sources.extend(container.parser.parse_file(Path(args.file)))
            unique: dict[str, ParsedSource] = {}
            for source in sources:
                unique.setdefault(source.dedupe_key, source)
            ordered = list(unique.values())
            if not ordered:
                print("Aucune source TikTok reconnue.", file=sys.stderr)
                return 2
            created = await container.queue.enqueue_sources(ordered, settings.download_options())
            print(f"{len(created)} tâche(s) créée(s).")

        if args.export:
            path = await container.reports.export(Path(args.export))
            print(f"Rapport exporté : {path}")

        if args.wait or (args.sources or args.file):
            await container.run_until_idle()
            # Laisse le temps au dernier événement de statistique d'être publié.
            await asyncio.sleep(0.5)

        stats = await container.queue.compute_stats()
        print(
            "\n— Bilan —\n"
            f"  réussies : {stats.done}\n"
            f"  échecs   : {stats.failed}\n"
            f"  ignorées : {stats.skipped}\n"
            f"  en file  : {stats.queued}\n"
            f"  volume   : {stats.bytes_downloaded / 1e6:.1f} Mo"
        )
        return 0 if stats.failed == 0 else 1


def _run_gui(settings: AppSettings, paths: object) -> int:
    """Lance l'interface graphique Qt."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from tikvault.ui.engine_bridge import EngineBridge
    from tikvault.ui.main_window import MainWindow
    from tikvault.ui.theme import apply_palette, palette_for

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)
    app = QApplication(sys.argv)
    app.setApplicationName("TikVault")
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)  # la zone de notification garde l'app vivante

    palette = palette_for(settings.theme.value, settings.accent_color)
    apply_palette(app, palette)

    from tikvault.ui.tray import TrayNotifier

    notifier_probe = TrayNotifier(None)  # type: ignore[arg-type]
    notifier = notifier_probe if notifier_probe.available else None

    bridge = EngineBridge(settings=settings, paths=paths, notifier=notifier)  # type: ignore[arg-type]
    bridge.start()

    window = MainWindow(bridge=bridge, settings=settings, paths=paths)  # type: ignore[arg-type]
    if settings.start_minimized and notifier is not None:
        window.hide()
    else:
        window.show()

    exit_code = app.exec()
    bridge.stop()
    return exit_code


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée console (``tikvault``) et module (``python -m tikvault``)."""
    args = build_parser().parse_args(argv)
    paths = default_paths(portable_dir=Path(args.portable) if args.portable else None).ensure()

    level = getattr(logging, (args.log_level or "INFO").upper(), logging.INFO)
    setup_logging(log_file=paths.log_file, level=level)

    settings = AppSettings.load(paths)
    settings = _apply_cli_overrides(settings, args)

    if args.update:
        from tikvault.infrastructure.updater import YtDlpUpdater

        success, message = asyncio.run(YtDlpUpdater(cache_dir=paths.cache_dir / "updates").apply())
        print(message)
        return 0 if success else 1

    if args.check_update:
        from tikvault.infrastructure.updater import YtDlpUpdater, current_version

        latest = asyncio.run(YtDlpUpdater(cache_dir=paths.cache_dir / "updates").check())
        print(f"installée : {current_version()} — disponible : {latest or 'inconnue'}")
        return 0

    if not args.headless and not args.sources and not args.file and not args.export:
        return _run_gui(settings, paths)

    return asyncio.run(_run_headless(args, settings, paths))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
