"""Page Réglages v2 : l'essentiel d'abord, les options avancées repliées.

Le formulaire reste appliqué à chaud au moteur ; seuls l'organisation et le
niveau de détail visible changent par rapport à la v1 — aucun réglage n'a disparu.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from tikvault.infrastructure.cookies import SUPPORTED_BROWSERS
from tikvault.infrastructure.updater import current_version
from tikvault.settings import AppSettings, ThemeMode
from tikvault.ui.icons import icon
from tikvault.ui.theme import ACCENTS
from tikvault.ui.widgets.collapsible import CollapsibleSection

TOKENS_HELP = (
    "Jetons disponibles : {username} {id} {title} {date} {year} {index} "
    "{duration} {views} {source} {ext}"
)


class SettingsPage(QWidget):
    """Formulaire de réglages, appliqué à chaud au moteur."""

    settings_changed = Signal(object)  # AppSettings
    save_requested = Signal()
    check_update_requested = Signal()
    apply_update_requested = Signal()
    accent_changed = Signal(str)

    def __init__(
        self, settings: AppSettings, *, accent: str = "#ff2e63", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._accent = accent

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget(scroll)
        root = QVBoxLayout(content)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        title = QLabel("Réglages", content)
        title.setProperty("pageTitle", True)
        root.addWidget(title)
        subtitle = QLabel(
            "L'essentiel est ici. Les options avancées sont repliées plus bas — "
            "cliquez sur un titre pour les ouvrir.",
            content,
        )
        subtitle.setProperty("muted", True)
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        # --- Visible d'emblée : l'essentiel + les mises à jour du moteur ----
        root.addWidget(self._build_essentials(content))
        root.addWidget(self._build_updates(content))

        # --- Replié : tout le reste ----------------------------------------
        root.addWidget(
            CollapsibleSection(
                "Nom des fichiers et qualité", self._build_files_quality(content), parent=content
            )
        )
        root.addWidget(
            CollapsibleSection(
                "Filtres appliqués par défaut", self._build_filters(content), parent=content
            )
        )
        root.addWidget(
            CollapsibleSection(
                "Réseau : proxies, cookies, identité",
                self._build_network(content),
                parent=content,
            )
        )
        root.addWidget(
            CollapsibleSection(
                "Performance et relances", self._build_performance(content), parent=content
            )
        )
        root.addWidget(
            CollapsibleSection(
                "Comportement de la fenêtre", self._build_window(content), parent=content
            )
        )

        footer = QHBoxLayout()
        self._status = QLabel("", content)
        self._status.setProperty("muted", True)
        footer.addWidget(self._status)
        footer.addStretch(1)
        revert = QPushButton("Rétablir", content)
        revert.clicked.connect(lambda: self.load(self._settings))
        save = QPushButton(icon("check", "#ffffff", 40), "Enregistrer", content)
        save.setProperty("primary", True)
        save.clicked.connect(self.save_requested)
        footer.addWidget(revert)
        footer.addWidget(save)
        root.addLayout(footer)
        root.addStretch(1)

        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self.load(settings)

    # ------------------------------------------------------------------
    # L'essentiel (toujours visible)
    # ------------------------------------------------------------------
    def _build_essentials(self, parent: QWidget) -> QGroupBox:
        box = QGroupBox("L'essentiel", parent)
        form = QFormLayout(box)
        form.setContentsMargins(14, 16, 14, 14)

        folder_row = QHBoxLayout()
        self.output_dir = QLineEdit(parent)
        browse = QPushButton(icon("folder", "#8b93a7", 40), "Parcourir…", parent)
        browse.clicked.connect(self._choose_folder)
        folder_row.addWidget(self.output_dir, 1)
        folder_row.addWidget(browse)
        form.addRow("Dossier de destination", folder_row)

        self.audio_only = QCheckBox("Télécharger l'audio seul par défaut", parent)
        self.audio_format = QComboBox(parent)
        self.audio_format.addItems(("mp3", "m4a", "opus", "flac", "wav", "aac"))
        audio_row = QHBoxLayout()
        audio_row.addWidget(self.audio_only)
        audio_row.addWidget(QLabel("format", parent))
        audio_row.addWidget(self.audio_format)
        audio_row.addStretch(1)
        form.addRow("Audio", audio_row)

        self.concurrency = QSlider(Qt.Orientation.Horizontal, parent)
        self.concurrency.setRange(1, 10)
        self.concurrency.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.concurrency.setTickInterval(1)
        self.concurrency_label = QLabel("3", parent)
        self.concurrency.valueChanged.connect(
            lambda value: self.concurrency_label.setText(str(value))
        )
        conc_row = QHBoxLayout()
        conc_row.addWidget(self.concurrency, 1)
        conc_row.addWidget(self.concurrency_label)
        form.addRow("Téléchargements simultanés", conc_row)

        self.theme = QComboBox(parent)
        self.theme.addItem("Sombre", ThemeMode.DARK.value)
        self.theme.addItem("Clair", ThemeMode.LIGHT.value)
        self.theme.addItem("Système", ThemeMode.SYSTEM.value)
        form.addRow("Thème", self.theme)

        self.accent = QComboBox(parent)
        for name, value in ACCENTS.items():
            self.accent.addItem(name.capitalize(), value)
        form.addRow("Couleur d'accent", self.accent)

        self.notify = QCheckBox("Notification système à la fin d'un lot", parent)
        form.addRow("Notifications", self.notify)
        return box

    def _build_updates(self, parent: QWidget) -> QGroupBox:
        box = QGroupBox("Moteur de téléchargement (yt-dlp)", parent)
        form = QFormLayout(box)
        form.setContentsMargins(14, 16, 14, 14)

        self.version_label = QLabel(f"yt-dlp {current_version()}", parent)
        self.auto_update = QCheckBox("Vérifier les mises à jour au démarrage", parent)

        row = QHBoxLayout()
        row.addWidget(self.version_label)
        row.addSpacing(16)
        check = QPushButton(icon("refresh", "#00d2d3", 40), "Vérifier", parent)
        check.clicked.connect(self.check_update_requested)
        apply_button = QPushButton(
            icon("download", "#ffffff", 40), "Mettre à jour maintenant", parent
        )
        apply_button.setProperty("primary", True)
        apply_button.clicked.connect(self.apply_update_requested)
        row.addWidget(check)
        row.addWidget(apply_button)
        row.addStretch(1)
        form.addRow("Version installée", row)
        form.addRow("", self.auto_update)

        note = QLabel(
            "TikTok modifie souvent son site : mettre yt-dlp à jour répare la plupart des pannes.",
            parent,
        )
        note.setProperty("muted", True)
        note.setWordWrap(True)
        form.addRow("", note)
        return box

    # ------------------------------------------------------------------
    # Sections avancées (repliées)
    # ------------------------------------------------------------------
    def _build_files_quality(self, parent: QWidget) -> QWidget:
        panel = QWidget(parent)
        form = QFormLayout(panel)
        form.setContentsMargins(8, 4, 8, 4)

        self.template = QLineEdit(parent)
        form.addRow("Modèle de nom", self.template)
        hint = QLabel(TOKENS_HELP, parent)
        hint.setProperty("muted", True)
        hint.setWordWrap(True)
        form.addRow("", hint)

        self.best_quality = QCheckBox("Meilleure qualité disponible", parent)
        self.no_watermark = QCheckBox("Préférer le flux sans filigrane", parent)
        self.embed_thumbnail = QCheckBox("Incruster la miniature (nécessite FFmpeg)", parent)
        self.embed_metadata = QCheckBox(
            "Incruster les métadonnées — titre, auteur, date (nécessite FFmpeg)", parent
        )
        form.addRow("Qualité", self.best_quality)
        form.addRow("", self.no_watermark)
        form.addRow("Métadonnées", self.embed_thumbnail)
        form.addRow("", self.embed_metadata)
        return panel

    def _build_filters(self, parent: QWidget) -> QWidget:
        panel = QWidget(parent)
        form = QFormLayout(panel)
        form.setContentsMargins(8, 4, 8, 4)

        self.max_items = QSpinBox(parent)
        self.max_items.setRange(0, 100_000)
        self.max_items.setSpecialValueText("illimité")
        form.addRow("Nombre max de vidéos par source", self.max_items)

        self.min_views = QSpinBox(parent)
        self.min_views.setRange(0, 1_000_000_000)
        self.min_views.setSingleStep(1000)
        self.min_views.setSpecialValueText("aucun minimum")
        form.addRow("Vues minimum", self.min_views)

        self.min_duration = QSpinBox(parent)
        self.min_duration.setRange(0, 36_000)
        self.min_duration.setSuffix(" s")
        self.min_duration.setSpecialValueText("aucun minimum")
        self.max_duration = QSpinBox(parent)
        self.max_duration.setRange(0, 36_000)
        self.max_duration.setSuffix(" s")
        self.max_duration.setSpecialValueText("aucun maximum")
        duration_row = QHBoxLayout()
        duration_row.addWidget(QLabel("min", parent))
        duration_row.addWidget(self.min_duration)
        duration_row.addWidget(QLabel("max", parent))
        duration_row.addWidget(self.max_duration)
        duration_row.addStretch(1)
        form.addRow("Durée", duration_row)

        note = QLabel(
            "Les dates se règlent lot par lot via « Options avancées… » de la page Télécharger.",
            parent,
        )
        note.setProperty("muted", True)
        form.addRow("", note)
        return panel

    def _build_network(self, parent: QWidget) -> QWidget:
        panel = QWidget(parent)
        form = QFormLayout(panel)
        form.setContentsMargins(8, 4, 8, 4)

        self.proxies = QPlainTextEdit(parent)
        self.proxies.setPlaceholderText(
            "Un proxy par ligne, par exemple :\nhttp://utilisateur:motdepasse@1.2.3.4:8080\nsocks5://127.0.0.1:9050"
        )
        self.proxies.setMaximumHeight(90)
        form.addRow("Liste de proxies", self.proxies)

        self.rotate_proxies = QCheckBox(
            "Faire tourner les proxies (avec mise en quarantaine)", parent
        )
        self.rotate_ua = QCheckBox("Faire tourner les User-Agents", parent)
        form.addRow("Rotation", self.rotate_proxies)
        form.addRow("", self.rotate_ua)

        cookie_row = QHBoxLayout()
        self.cookies_file = QLineEdit(parent)
        self.cookies_file.setPlaceholderText(
            "cookies.txt (export « Netscape » de votre navigateur)"
        )
        cookie_browse = QPushButton(icon("file", "#8b93a7", 40), "Fichier…", parent)
        cookie_browse.clicked.connect(self._choose_cookies)
        cookie_row.addWidget(self.cookies_file, 1)
        cookie_row.addWidget(cookie_browse)
        form.addRow("Cookies", cookie_row)

        self.cookies_browser = QComboBox(parent)
        self.cookies_browser.addItem("— aucun —", None)
        for browser in SUPPORTED_BROWSERS:
            self.cookies_browser.addItem(browser.capitalize(), browser)
        form.addRow("Import depuis le navigateur", self.cookies_browser)

        note = QLabel(
            "Les cookies débloquent les vidéos réservées aux abonnés et réduisent fortement "
            "les limitations de requêtes (erreur 429).",
            parent,
        )
        note.setProperty("muted", True)
        note.setWordWrap(True)
        form.addRow("", note)
        return panel

    def _build_performance(self, parent: QWidget) -> QWidget:
        panel = QWidget(parent)
        form = QFormLayout(panel)
        form.setContentsMargins(8, 4, 8, 4)

        self.rate_limit = QSpinBox(parent)
        self.rate_limit.setRange(0, 1_000_000)
        self.rate_limit.setSuffix(" Ko/s")
        self.rate_limit.setSpecialValueText("illimité")
        form.addRow("Limitation de débit", self.rate_limit)

        self.max_attempts = QSpinBox(parent)
        self.max_attempts.setRange(1, 10)
        form.addRow("Tentatives maximum", self.max_attempts)
        return panel

    def _build_window(self, parent: QWidget) -> QWidget:
        panel = QWidget(parent)
        form = QFormLayout(panel)
        form.setContentsMargins(8, 4, 8, 4)

        self.start_minimized = QCheckBox("Démarrer dans la zone de notification", parent)
        self.confirm_close = QCheckBox(
            "Confirmer avant de fermer pendant un téléchargement", parent
        )
        form.addRow("Démarrage", self.start_minimized)
        form.addRow("Fermeture", self.confirm_close)
        return panel

    # ------------------------------------------------------------------
    def load(self, settings: AppSettings) -> None:
        """Remplit le formulaire depuis les réglages courants."""
        self._settings = settings
        self.output_dir.setText(settings.output_dir)
        self.template.setText(settings.filename_template)
        self.best_quality.setChecked(settings.best_quality)
        self.no_watermark.setChecked(settings.no_watermark)
        self.embed_thumbnail.setChecked(settings.embed_thumbnail)
        self.embed_metadata.setChecked(settings.embed_metadata)
        self.audio_only.setChecked(settings.audio_only)
        self.audio_format.setCurrentText(settings.audio_format)
        self.concurrency.setValue(settings.max_concurrency)
        self.rate_limit.setValue(settings.rate_limit_kbps or 0)
        self.max_attempts.setValue(settings.max_attempts)
        self.max_items.setValue(settings.filters.max_items or 0)
        self.min_views.setValue(settings.filters.min_views or 0)
        self.min_duration.setValue(int(settings.filters.min_duration or 0))
        self.max_duration.setValue(int(settings.filters.max_duration or 0))
        self.proxies.setPlainText("\n".join(settings.proxies))
        self.rotate_proxies.setChecked(settings.rotate_proxies)
        self.rotate_ua.setChecked(settings.rotate_user_agents)
        self.cookies_file.setText(settings.cookies_file or "")
        index = self.cookies_browser.findData(settings.cookies_browser)
        self.cookies_browser.setCurrentIndex(max(0, index))
        theme_index = self.theme.findData(settings.theme.value)
        self.theme.setCurrentIndex(max(0, theme_index))
        accent_index = self.accent.findData(settings.accent_color)
        self.accent.setCurrentIndex(max(0, accent_index))
        self.notify.setChecked(settings.notify_on_finish)
        self.start_minimized.setChecked(settings.start_minimized)
        self.confirm_close.setChecked(settings.confirm_on_close)
        self.auto_update.setChecked(settings.auto_update_ytdlp)
        self.version_label.setText(f"yt-dlp {current_version()}")

    def collect(self) -> AppSettings:
        """Construit un objet :class:`AppSettings` à partir du formulaire."""
        base = self._settings.model_copy(deep=True)
        filters = base.filters.model_copy(deep=True)
        filters.max_items = self.max_items.value() or None
        filters.min_views = self.min_views.value() or None
        filters.min_duration = self.min_duration.value() or None
        filters.max_duration = self.max_duration.value() or None

        return base.model_copy(
            update={
                "output_dir": self.output_dir.text().strip() or "~/TikVault",
                "filename_template": self.template.text().strip(),
                "best_quality": self.best_quality.isChecked(),
                "no_watermark": self.no_watermark.isChecked(),
                "embed_thumbnail": self.embed_thumbnail.isChecked(),
                "embed_metadata": self.embed_metadata.isChecked(),
                "audio_only": self.audio_only.isChecked(),
                "audio_format": self.audio_format.currentText(),
                "max_concurrency": self.concurrency.value(),
                "rate_limit_kbps": self.rate_limit.value() or None,
                "max_attempts": self.max_attempts.value(),
                "filters": filters,
                "proxies": [
                    line.strip() for line in self.proxies.toPlainText().splitlines() if line.strip()
                ],
                "rotate_proxies": self.rotate_proxies.isChecked(),
                "rotate_user_agents": self.rotate_ua.isChecked(),
                "cookies_file": self.cookies_file.text().strip() or None,
                "cookies_browser": self.cookies_browser.currentData(),
                "theme": ThemeMode(self.theme.currentData()),
                "accent_color": self.accent.currentData(),
                "notify_on_finish": self.notify.isChecked(),
                "start_minimized": self.start_minimized.isChecked(),
                "confirm_on_close": self.confirm_close.isChecked(),
                "auto_update_ytdlp": self.auto_update.isChecked(),
            }
        )

    # ------------------------------------------------------------------
    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Dossier de destination", self.output_dir.text()
        )
        if folder:
            self.output_dir.setText(folder)

    def _choose_cookies(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Fichier de cookies", str(Path.home()), "Cookies (*.txt);;Tous les fichiers (*)"
        )
        if path:
            self.cookies_file.setText(path)

    def set_version(self, version: str) -> None:
        self.version_label.setText(f"yt-dlp {version}")

    def set_status(self, message: str) -> None:
        self._status.setText(message)
